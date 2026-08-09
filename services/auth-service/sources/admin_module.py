"""
Admin and setup module for auth-service (FastAPI).

Mount it from the main auth_service.py with:
    from admin_module import router as admin_router, setup_gate
    app.include_router(admin_router)
    app.middleware("http")(setup_gate)

Requires: fastapi, redis.asyncio, pyyaml, argon2-cffi, httpx, filelock, pydantic
Required env vars: ORTHANC_ADMIN_USER, ORTHANC_ADMIN_PASS, ORTHANC_URL, REDIS_URL
"""

import json
import asyncio
import logging
import os
import secrets as pysecrets
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as aioredis
import yaml
from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import urlparse

from filelock import FileLock, Timeout
from pydantic import BaseModel, EmailStr, Field
from redis.exceptions import RedisError


# ============================================================================
# Config + globals
# ============================================================================

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")
# These credentials are only truly needed by the endpoints that talk to
# Orthanc (reload, health check). Read leniently so the module still imports
# when a container starts before the compose file has been updated: the
# endpoints that use them check and return 503 when they are empty.
ORTHANC_USER = os.environ.get("ORTHANC_ADMIN_USER", "")
ORTHANC_PASS = os.environ.get("ORTHANC_ADMIN_PASS", "")


def _require_orthanc_creds():
    if not ORTHANC_USER or not ORTHANC_PASS:
        raise HTTPException(
            503,
            "ORTHANC_ADMIN_USER/ORTHANC_ADMIN_PASS non configures dans .env — "
            "l'endpoint est disponible mais ne peut pas appeler Orthanc",
        )

# Parent directories are bind-mounted, never the files themselves: atomic
# writes rename, which fails on a file mount (EBUSY), and the resulting new
# inode would stay invisible to the other containers.
AUTHELIA_YML = Path(
    os.getenv("ADMIN_AUTHELIA_PATH", "/host/authelia-config/users_database.yml")
)
ORTHANC_JSON = Path(
    os.getenv("ADMIN_ORTHANC_PATH", "/host/orthanc-config/orthanc.json")
)
BACKUPS_DIR = Path(os.getenv("ADMIN_BACKUPS_DIR", "/host/backups"))

# configuration.yml lives in the same directory as users_database.yml, which
# is already bind-mounted: nothing more to mount to read or write it.
AUTHELIA_CONFIG = Path(
    os.getenv("ADMIN_AUTHELIA_CONFIG_PATH", "/host/authelia-config/configuration.yml")
)
# The .env file lives at the project root. It is mounted as a FILE rather
# than through its directory: mounting the root would give the container
# write access to docker-compose.yml and to the scripts. As a consequence,
# writes happen in place (see _write_env_var).

# Module logger. auth_service configures logging at startup; we hook into its
# hierarchy so that the level set by LOG_LEVEL applies here too.
logger = logging.getLogger("auth-service.admin")

ENV_FILE = Path(os.getenv("ADMIN_ENV_PATH", "/host/env/.env"))

# Application settings, as opposed to bootstrap variables.
#
# The .env file only exists for what docker compose must know BEFORE a
# container starts: secrets, credentials, PUID/PGID, SSL_MODE. A setting that
# only auth-service reads, once running, has no business being there --
# putting it in forces mounting .env writable, rewriting it in place since a
# rename is impossible on a file bind-mount, and mixes interface preferences
# with passwords.
#
# This file lives in a mounted directory, is written atomically, and holds no
# secret.
SETTINGS_FILE = Path(
    os.getenv("ADMIN_SETTINGS_PATH", "/host/app-settings/settings.json")
)

SETUP_KEY = "orthanc_authelia:setup_completed"
SETUP_FIRST_ADMIN_KEY = "orthanc_authelia:setup_first_admin_created"

# Account shipped in users_database.yml.example for one reason only: Authelia
# refuses to start on an empty database ("users: non zero value required").
# Disabled and group-less, it is removed when the wizard is finalised.
BOOTSTRAP_USERNAME = "bootstrap@localhost"
AUDIT_STREAM = "admin:audit"
CSRF_COOKIE = "orthanc_admin_csrf"

IMAGE_VERSION = os.getenv("IMAGE_VERSION", "dev")

# Restarting Orthanc. The Docker socket is deliberately not mounted here: we
# go through a proxy that only allows restarting a container (see socket-proxy
# in docker-compose). Empty means the feature is unavailable, which the panel
# states up front rather than failing at use time.
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "").rstrip("/")
ORTHANC_CONTAINER = os.getenv("ORTHANC_CONTAINER", "orthanc-server")

# argon2id parameters match Authelia's defaults, so the hashes we write are
# the ones it knows how to verify.
_hasher = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, salt_len=16,
)

# Global Redis client, injected from auth_service.py
_redis: aioredis.Redis | None = None


def set_redis(client: aioredis.Redis) -> None:
    """Called by auth_service.py at startup to inject the Redis connection."""
    global _redis
    _redis = client


def _r() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call set_redis() at startup.")
    return _redis


# ============================================================================
# Helpers: user-facing messages
# ============================================================================

def _msg(key: str, fallback: str, **params: Any) -> str:
    """A message in the interface language, for anything the user will read.

    HTTPException details are surfaced as-is by the panel, so they are
    interface text, not code: they must follow the language the user picked.
    Hard-coding them in French left an English interface showing French
    errors.

    Goes through the same translation files as the rest of the interface --
    translations/{en,fr}.json, section "ui" -- which the frontend also reads
    via window.__I18N__. One source, one language setting, no parallel
    mechanism to keep in sync.

    `fallback` is the French wording, used when the key is missing: a missing
    translation degrades to a readable sentence rather than to a raw key.
    """
    try:
        from auth_service import translations

        template = translations().get("ui", {}).get(key) or fallback
    except Exception:  # noqa: BLE001 - translations unavailable, use fallback
        template = fallback

    try:
        return template.format(**params) if params else template
    except (KeyError, IndexError):
        # A translation whose placeholders do not match must not crash the
        # very error it is meant to describe.
        return fallback.format(**params) if params else fallback


# ============================================================================
# Helpers : backups + audit + atomic write
# ============================================================================

def _backup(path: Path, tag: str = "") -> Path:
    """Copy path to backups/{name}.bak.{ts}[.tag], keeping the last 10."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f".bak.{ts}" + (f".{tag}" if tag else "")
    dest = BACKUPS_DIR / (path.name + suffix)
    shutil.copy2(path, dest)
    # Rotation: keep the ten most recent backups of this file
    prefix = path.name + ".bak."
    backups = sorted(BACKUPS_DIR.glob(prefix + "*"), reverse=True)
    for old in backups[10:]:
        old.unlink(missing_ok=True)
    return dest


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path through a temporary file and an atomic rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


async def _audit(event: str, actor: str, **fields: Any) -> None:
    """Append an entry to the admin:audit Redis stream."""
    entry = {"event": event, "actor": actor, "ts": str(int(time.time()))}
    for k, v in fields.items():
        entry[k] = str(v)
    await _r().xadd(AUDIT_STREAM, entry, maxlen=10000)


# ============================================================================
# Helpers : URL publique
# ============================================================================

def _normalise_public_url(raw: str) -> tuple[str, str]:
    """Validate the public URL. Returns (origin, host without port).

    The origin drives redirections, port included; the host is what session
    cookies are scoped to -- a cookie never carries a port.
    """
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https":
        raise HTTPException(400, _msg("err_public_url_https",
                                "l'URL publique doit commencer par https://"))
    if not parsed.hostname:
        raise HTTPException(400, _msg("err_public_url_host",
                                "hote manquant dans l'URL publique"))
    if parsed.path.strip("/"):
        raise HTTPException(
            400,
            "indiquer l'origine seule, sans chemin "
            "(exemple : https://pacs.exemple.fr)",
        )
    # RFC 6265: some browsers drop a cookie set on a host without a dot.
    # "localhost" is the exception, "mypacs" is not.
    if "." not in parsed.hostname and parsed.hostname != "localhost":
        raise HTTPException(
            400,
            f"'{parsed.hostname}' n'a pas de point : les navigateurs "
            "refuseront le cookie de session. Utiliser un nom complet "
            "(pacs.exemple.fr) ou pacs.localhost.",
        )
    return f"https://{parsed.netloc}", parsed.hostname


# Settings file cache, invalidated by modification time.
#
# Translations read these settings for every label displayed: re-reading the
# file on each access would mean dozens of reads per page. A stat() is enough
# to tell whether it changed, and the cost of a change -- rare -- is a single
# re-read.
#
# The key includes the PATH and not just the timestamp: two distinct files
# written within the same second share an mtime, and the cache would then
# serve one file's content for the other. Harmless in production, where the
# path never changes -- but exactly the kind of shortcut that bites later,
# and a test is what caught it.
_settings_cache: dict[str, Any] = {"cle": None, "data": {}}


def _read_settings() -> dict[str, Any]:
    """Contents of the settings file. Empty dict when it does not exist yet."""
    try:
        cle = (str(SETTINGS_FILE), SETTINGS_FILE.stat().st_mtime)
    except OSError:
        # No file: fresh install, or settings never changed.
        _settings_cache["cle"] = None
        _settings_cache["data"] = {}
        return {}

    if _settings_cache["cle"] == cle:
        return _settings_cache["data"]

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # An unreadable preferences file must not stop the service from
        # answering: fall back to defaults and say so.
        logger.warning("unreadable settings (%s), falling back to defaults", e)
        data = {}

    _settings_cache["cle"] = cle
    _settings_cache["data"] = data
    return data


def _read_setting(name: str, env_var: str = "", default: Any = None) -> Any:
    """A setting's value, falling back to its former environment variable.

    `env_var` keeps existing installations working: the setting used to live
    in .env, and is still read from there until it gets redefined from the
    panel. The first write moves it into the settings file, after which the
    .env line has no effect.
    """
    settings = _read_settings()
    if name in settings:
        return settings[name]
    if env_var:
        previous = _read_env_var(env_var)
        if previous:
            return previous
    return default


def _write_setting(name: str, value: Any) -> None:
    """Write a setting, creating the file and its directory when needed."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            503,
            f"dossier de reglages inaccessible ({e}). Verifier le montage "
            f"'./data/app-settings:/host/app-settings:rw' sur auth-service.",
        ) from e

    settings = _read_settings()
    settings[name] = value
    # Same precaution as for the panel's other files: write to a temporary
    # file in the same directory, then rename. An interruption leaves the
    # previous file intact rather than a truncated JSON.
    _atomic_write(SETTINGS_FILE, json.dumps(settings, indent=2,
                                            ensure_ascii=False) + "\n")
    # Invalidate explicitly: mtime granularity is sometimes one second, which
    # would make two close writes indistinguishable.
    _settings_cache["cle"] = None


def _read_env_var(name: str) -> str:
    """Read a variable from .env. Empty string when absent or unreadable."""
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _write_env_var(name: str, value: str) -> None:
    """Remplace (ou ajoute) name=value dans le .env, en ecrivant SUR PLACE.

    No write-tmp + rename here, unlike everywhere else in this module: .env
    is a file bind-mount. The rename would fail (EBUSY) and, were it to
    succeed, docker compose would keep reading the old inode. So we rewrite
    the same file, after a backup -- an interrupted write would otherwise
    leave a truncated .env, and the stack would no longer start.
    """
    if not ENV_FILE.exists():
        raise HTTPException(
            503,
            "le fichier .env n'est pas accessible depuis le container. "
            "Ajouter le montage './.env:/host/env/.env:rw' au service "
            "auth-service, puis recreer le container.",
        )
    _backup(ENV_FILE, tag="network")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            lines[i] = f"{name}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _retarget_authelia_config(previous_origin: str, previous_host: str,
                              origin: str, host: str) -> int:
    """Point configuration.yml at the new public URL.

    Textual replacement rather than a YAML load and re-serialise: the file is
    heavily commented (access rules, warnings about the cookie port) and a
    round-trip through PyYAML would wipe all of it.

    Returns the number of substitutions made.
    """
    if not AUTHELIA_CONFIG.exists():
        raise HTTPException(503, _msg("err_authelia_config_missing",
                                "configuration.yml d'Authelia introuvable"))
    text = AUTHELIA_CONFIG.read_text(encoding="utf-8")
    total = text.count(previous_origin) + text.count(previous_host)
    if not total:
        raise HTTPException(
            500,
            f"aucune trace de '{previous_host}' dans configuration.yml : "
            "le fichier a ete modifie a la main, changement annule",
        )
    _backup(AUTHELIA_CONFIG, tag="network")
    # Full origin first: replacing the bare host would otherwise turn
    # "https://old:30443" into "https://new:30443", keeping a port that no
    # longer applies.
    text = text.replace(previous_origin, origin).replace(previous_host, host)
    _atomic_write(AUTHELIA_CONFIG, text)
    return total


async def _apply_public_url(new_url: str, actor: str) -> dict:
    """Apply a new public URL to .env and to Authelia's configuration."""
    origin, host = _normalise_public_url(new_url)
    previous_origin = _read_env_var("PUBLIC_URL").rstrip("/")
    if not previous_origin:
        raise HTTPException(500, _msg("err_public_url_absent",
                                "PUBLIC_URL absente du .env, changement annule"))
    if previous_origin == origin:
        return {"ok": True, "unchanged": True, "public_url": origin}

    _, previous_host = _normalise_public_url(previous_origin)
    substitutions = _retarget_authelia_config(
        previous_origin, previous_host, origin, host,
    )
    _write_env_var("PUBLIC_URL", origin)
    await _audit(
        "network.public_url.changed", actor=actor,
        old=previous_origin, new=origin, substitutions=substitutions,
    )
    return {
        "ok": True,
        "unchanged": False,
        "public_url": origin,
        "substitutions": substitutions,
        "restart_required": True,
        "message": (
            f"URL publique enregistree : {origin}. Relancer la pile "
            "(docker compose up -d) pour l'appliquer, puis se reconnecter "
            f"sur {origin} — la session en cours est liee a l'ancien domaine."
        ),
    }


class PublicUrlPayload(BaseModel):
    public_url: str = Field(min_length=8, max_length=255)


# ============================================================================
# Authentification admin (dependance FastAPI)
# ============================================================================

class AdminUser(BaseModel):
    username: str
    groups: list[str]


async def require_admin(request: Request) -> AdminUser:
    """
    Dependency injected into the /api/admin/* routes. Relies on the headers
    forwarded by nginx auth_request: Authelia sets Remote-User and
    Remote-Groups once it has verified the session.
    """
    username = request.headers.get("remote-user", "")
    groups_raw = request.headers.get("remote-groups", "")
    if not username:
        raise HTTPException(401, _msg("err_auth_required", "auth requise"))
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    if "admin" not in groups:
        raise HTTPException(403, _msg("err_admin_group_required", "groupe admin requis"))
    return AdminUser(username=username, groups=groups)


# ============================================================================
# Middleware : setup state machine
# ============================================================================

async def setup_gate(request: Request, call_next):
    """
    Route between the wizard and the hub based on the setup_completed flag.

    Nginx exposes the console under /console/ and proxies to /ui/... on the
    auth-service side, so the paths seen here are /ui/setup and /ui/ (the
    hub). Redirections, on the other hand, point at the URLs as the browser
    sees them, /console/ prefix included.
    """
    path = request.url.path
    if not path.startswith("/ui"):
        return await call_next(request)

    # Assets are not pages: redirecting them would break loading of the SPA
    # on the setup page.
    if path.startswith("/ui/assets"):
        return await call_next(request)

    is_setup = path.startswith("/ui/setup")
    done = (await _r().get(SETUP_KEY)) == "1"

    if is_setup and done:
        return RedirectResponse("/console/", status_code=302)
    if not is_setup and not done:
        return RedirectResponse("/console/setup", status_code=302)
    return await call_next(request)


# ============================================================================
# Middleware : CSRF (double-submit token + origin check)
# ============================================================================

async def csrf_gate(request: Request, call_next):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    if not request.url.path.startswith("/api/admin/"):
        return await call_next(request)

    # 1. Origin match
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin and origin != f"https://{host}":
        return JSONResponse({"error": "csrf.origin"}, status_code=403)

    # 2. Double-submit token
    cookie_tok = request.cookies.get(CSRF_COOKIE, "")
    header_tok = request.headers.get("x-csrf-token", "")
    if not cookie_tok or not header_tok or not pysecrets.compare_digest(cookie_tok, header_tok):
        return JSONResponse({"error": "csrf.token"}, status_code=403)

    return await call_next(request)


def issue_csrf_cookie(response: Response) -> str:
    """A appeler dans la route qui rend admin.html pour poser le cookie."""
    token = pysecrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE, token,
        secure=True, httponly=False, samesite="strict", max_age=3600,
    )
    return token


# ============================================================================
# Authelia : validation + CRUD users
# ============================================================================

def _load_authelia() -> dict:
    """
    Charge users_database.yml. Leve HTTPException 500 lisible si le YAML est
    corrompu (edite manuellement de travers) : pointe vers /api/admin/backups
    pour restaurer un backup connu bon.
    """
    if not AUTHELIA_YML.exists():
        return {"users": {}}
    try:
        raw = AUTHELIA_YML.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, _msg("err_authelia_unreadable",
                                "authelia yml illisible : {detail}", detail=e)) from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise HTTPException(
            500,
            f"authelia yml corrompu : {e}. Restaurer un backup via "
            "POST /api/admin/backups/restore.",
        ) from e
    return data or {"users": {}}


def _strip_json_comments(raw: str) -> str:
    """
    Retire les commentaires // et /* */ d'un JSON.

    Orthanc accepte les commentaires dans sa configuration, mais json.loads
    les refuse. On ne peut pas se contenter d'une expression reguliere : le
    fichier contient des URLs ("http://auth-service:8000") dont le // ne doit
    pas etre traite comme un debut de commentaire. On parcourt donc le texte
    en suivant l'etat "dans une chaine" / "hors chaine".
    """
    out = []
    i, n = 0, len(raw)
    in_string = False
    while i < n:
        ch = raw[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:      # echappement : copier la paire
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            if raw[i + 1] == "/":               # commentaire jusqu'a la ligne
                while i < n and raw[i] != "\n":
                    i += 1
                continue
            if raw[i + 1] == "*":               # commentaire bloc
                i += 2
                while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _load_orthanc_config() -> dict:
    """Idem pour orthanc.json — meme strategie d'erreur explicite."""
    try:
        raw = ORTHANC_JSON.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, _msg("err_orthanc_json_unreadable",
                                "orthanc.json illisible : {detail}", detail=e)) from e
    try:
        return json.loads(_strip_json_comments(raw))
    except json.JSONDecodeError as e:
        raise HTTPException(
            500,
            f"orthanc.json corrompu : {e}. Restaurer un backup via "
            "POST /api/admin/backups/restore.",
        ) from e


def _validate_authelia(data: dict) -> None:
    """Invariants qui empechent un YAML lockant tout le monde dehors."""
    if not isinstance(data.get("users"), dict) or not data["users"]:
        raise ValueError("users: section vide ou absente")
    active_admins = [
        u for u, info in data["users"].items()
        if not info.get("disabled") and "admin" in (info.get("groups") or [])
    ]
    if not active_admins:
        raise ValueError("au moins 1 admin actif requis (invariant lockout)")
    for name, info in data["users"].items():
        for field in ("password", "email", "displayname"):
            if not info.get(field):
                raise ValueError(f"{name}: champ {field!r} manquant")
        if not info["password"].startswith("$argon2id$"):
            raise ValueError(f"{name}: password doit etre argon2id (start with $argon2id$)")


def _write_authelia(data: dict) -> None:
    """Backup + validate + atomic write. Verrouille via FileLock."""
    lock = FileLock(str(AUTHELIA_YML) + ".lock", timeout=5)
    try:
        with lock:
            _validate_authelia(data)
            if AUTHELIA_YML.exists():
                _backup(AUTHELIA_YML)
            serialized = yaml.safe_dump(
                data, default_flow_style=False, sort_keys=False, allow_unicode=True,
            )
            # Dry-run parse pour attraper les bugs de serialisation avant remplacement
            reloaded = yaml.safe_load(serialized) or {}
            _validate_authelia(reloaded)
            _atomic_write(AUTHELIA_YML, serialized)
    except Timeout as e:
        raise HTTPException(423, _msg("err_file_locked",
                                "fichier verrouille par un autre admin, retry dans 5s")) from e
    except ValueError as e:
        # Violation d'invariant : refus deliberé, pas une panne. Sans cette
        # conversion, supprimer ou desactiver le dernier administrateur
        # renvoyait une erreur 500 au lieu d'expliquer ce qui bloque.
        raise HTTPException(400, str(e)) from e


class UserCreatePayload(BaseModel):
    username: str = Field(..., pattern=r"^[a-zA-Z0-9._-]{3,32}$")
    displayname: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=12)
    # 'doctor' au singulier : c'est ce que reconnaissent la correspondance
    # groupe -> droits (auth_service.py) et les regles d'acces d'Authelia. Le
    # pluriel qui figurait ici ne correspondait a aucun groupe : un compte
    # cree via l'API sans groupe explicite tombait en lecture seule, sans
    # message. Meme piege que le groupe 'admins' corrige auparavant.
    groups: list[str] = Field(default_factory=lambda: ["doctor"])


class UserUpdatePayload(BaseModel):
    """Modification partielle : seuls les champs fournis sont appliques.

    None signifie "ne pas toucher", ce qui permet de changer les groupes sans
    reenvoyer le nom et l'e-mail, et de desactiver un compte sans rien
    reecrire d'autre.
    """
    displayname: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr | None = None
    groups: list[str] | None = None
    disabled: bool | None = None


class ModalityPayload(BaseModel):
    """Equipement DICOM distant : scanner, IRM, station de post-traitement."""
    # Le titre AE est plafonne a 16 caracteres par la norme DICOM ; au-dela,
    # l'equipement refuse l'association sans expliquer pourquoi.
    aet: str = Field(..., min_length=1, max_length=16)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)


class PasswordChangePayload(BaseModel):
    new_password: str = Field(..., min_length=12)


# ============================================================================
# Orthanc config : validation + edit + reload
# ============================================================================

# Whitelist des chemins editables via UI. Refuse tout ce qui n'est pas ici.
ORTHANC_DEFAULTS = {
    # Valeurs livrees dans orthanc.json.example, pour qu'un champ vide
    # indique ce qui s'appliquera.
    "OrthancExplorer2.Theme": "dark",
    "OrthancExplorer2.EnableReportQuickButton": True,
    "OrthancExplorer2.Tokens.InstantLinksValidity": 200,
    "OrthancExplorer2.UiOptions.DefaultShareDuration": 15,
    "OrthancExplorer2.UiOptions.EnableShares": True,
    "OrthancExplorer2.UiOptions.EnableViewerQuickButton": True,
    "OrthancExplorer2.UiOptions.ShowOrthancName": True,
    "OrthancExplorer2.UiOptions.EnableOpenInOhifViewer3": True,
    "OrthancExplorer2.UiOptions.EnableOpenInStoneWebViewer": True,
    "OrthancExplorer2.UiOptions.EnableOpenInVolView": True,
    "OrthancExplorer2.UiOptions.StudyListColumns": [
        "PatientID", "PatientName", "StudyDate", "StudyDescription",
        "AccessionNumber", "InstitutionName", "Modality",
    ],
    "OrthancExplorer2.UiOptions.ViewersOrdering": [
        "ohif", "stone-webviewer", "volview",
    ],
    "OrthancExplorer2.UiOptions.ShareDurations": [0, 7, 15, 30, 90, 365],
    # Valeurs par defaut relevees dans le fichier de configuration de
    # reference d'Orthanc 1.12.11, la version embarquee dans l'image.
    # Elles sont affichees a titre indicatif pour les parametres absents
    # d'orthanc.json : sans elles, l'interface annonce "valeur par defaut"
    # sans dire laquelle, ce qui n'apprend rien.
    'Name': 'MyOrthanc',
    'DicomAet': 'ORTHANC',
    'RemoteAccessAllowed': False,
    'DicomServerEnabled': True,
    'DicomPort': 4242,
    'DicomCheckCalledAet': False,
    'DicomAlwaysAllowEcho': True,
    'DicomAlwaysAllowStore': True,
    'DicomAlwaysAllowFind': False,
    'DicomAlwaysAllowMove': False,
    'DicomScpTimeout': 30,
    'DicomThreadsCount': 4,
    'DicomModalitiesInDatabase': False,
    'OrthancPeersInDatabase': False,
    'StorageCompression': False,
    'MaximumStorageSize': 0,
    'MaximumPatientCount': 0,
    'MaximumStorageMode': 'Recycle',
    'StoreMD5ForAttachments': True,
    'HttpPort': 8042,
    'HttpTimeout': 60,
    'HttpCompressionEnabled': False,
    'StableAge': 60,
    'OverwriteInstances': False,
    'ConcurrentJobs': 2,
    'JobsHistorySize': 10,
    'SaveJobs': True,
    'SynchronousCMove': True,
    'DeidentifyLogs': True,
    'DefaultEncoding': 'Latin1',
    'LimitFindResults': 0,
    'LimitFindInstances': 0,
    'IngestTranscodingOfUncompressed': True,
    'AcceptedTransferSyntaxes': ['1.2.840.10008.1.*'],
}


ORTHANC_EDITABLE_PATHS = {
    # Viewer preselectionne au moment de partager un examen. C'est CE champ
    # qu'Explorer lit -- son JS fait `tokenType: this.tokens.ShareType` -- et
    # non le "default-viewer" que renvoie /settings/roles, qui n'apparait nulle
    # part dans son bundle. Se tromper de champ donne un reglage qui s'ecrit,
    # se relit, et ne change rien a l'ecran.
    "OrthancExplorer2.Tokens.ShareType": str,

    # Reglages d'apparence et d'usage d'Explorer. Sans eux, changer le theme
    # ou masquer un viewer imposait d'ouvrir orthanc.json a la main -- ce qui
    # exclut de fait quiconque n'est pas a l'aise avec un editeur de texte.
    #
    # Enable et IsDefaultOrthancUI restent volontairement absents : les
    # exposer permettrait de desactiver l'interface depuis l'interface, donc
    # de se couper l'acces sans moyen de revenir en arriere autrement qu'en
    # editant le fichier -- exactement ce qu'on cherche a eviter. Les
    # *PublicRoot non plus : ce sont les chemins servis par nginx, les
    # changer casserait les liens sans rien apporter.
    "OrthancExplorer2.Theme": str,
    "OrthancExplorer2.EnableReportQuickButton": bool,
    "OrthancExplorer2.Tokens.InstantLinksValidity": int,
    "OrthancExplorer2.UiOptions.DefaultShareDuration": int,
    "OrthancExplorer2.UiOptions.EnableShares": bool,
    "OrthancExplorer2.UiOptions.EnableViewerQuickButton": bool,
    "OrthancExplorer2.UiOptions.ShowOrthancName": bool,
    "OrthancExplorer2.UiOptions.EnableOpenInOhifViewer3": bool,
    "OrthancExplorer2.UiOptions.EnableOpenInStoneWebViewer": bool,
    "OrthancExplorer2.UiOptions.EnableOpenInVolView": bool,

    # Listes. Leur contenu n'est volontairement pas restreint a un ensemble
    # ferme : le bundle d'Explorer mentionne des viewers absents de notre
    # installation (osimis-web-viewer, wsi) et plusieurs variantes d'OHIF, et
    # les colonnes admises n'y sont pas enumerables de facon fiable. Une
    # liste inventee bloquerait une configuration valide, ce qui est pire que
    # pas de liste. On valide donc le type des elements, et l'aide de chaque
    # champ cite les valeurs courantes.
    "OrthancExplorer2.UiOptions.StudyListColumns": list,
    "OrthancExplorer2.UiOptions.ViewersOrdering": list,
    "OrthancExplorer2.UiOptions.ShareDurations": list,

    "Name": str,
    "DicomAet": str,
    "RemoteAccessAllowed": bool,
    "DicomServerEnabled": bool,
    "DicomPort": int,
    "DicomCheckCalledAet": bool,
    "DicomAlwaysAllowEcho": bool,
    "DicomAlwaysAllowStore": bool,
    "DicomAlwaysAllowFind": bool,
    "DicomAlwaysAllowMove": bool,
    "DicomScpTimeout": int,
    "DicomThreadsCount": int,
    "DicomModalitiesInDatabase": bool,
    "OrthancPeersInDatabase": bool,
    "StorageCompression": bool,
    "MaximumStorageSize": int,
    "MaximumPatientCount": int,
    "MaximumStorageMode": str,
    "StoreMD5ForAttachments": bool,
    "HttpPort": int,
    "HttpTimeout": int,
    "HttpCompressionEnabled": bool,
    "StableAge": int,
    "OverwriteInstances": str,
    "ConcurrentJobs": int,
    "JobsHistorySize": int,
    "SaveJobs": bool,
    "SynchronousCMove": bool,
    "LogLevel": str,
    "DeidentifyLogs": bool,
    "DefaultEncoding": str,
    "LimitFindResults": int,
    "LimitFindInstances": int,
    "IngestTranscoding": str,
    "IngestTranscodingOfUncompressed": bool,
    "DicomWeb.Enable": bool,
    "DicomWeb.Root": str,
    "DicomWeb.EnableWado": bool,
    "DicomWeb.StowMaxInstances": int,
    "DicomWeb.StowMaxSize": int,
    "DicomWeb.EnableMetadata": bool,
    "DicomWeb.PublicRoot": str,
    "AcceptedTransferSyntaxes": list,  # cas special : liste de strings
    # Housekeeper : entretien de la base en tache de fond (recompression du
    # stockage, mise a jour des tags principaux, cache DICOMweb). Il tourne
    # depuis le debut sans qu'aucun de ses reglages ne soit accessible.
    "Housekeeper.Enable": bool,
    "Housekeeper.Force": bool,
    "Housekeeper.ThrottleDelay": int,
    "Housekeeper.Triggers.StorageCompressionChange": bool,
    "Housekeeper.Triggers.MainDicomTagsChange": bool,
    "Housekeeper.Triggers.UnnecessaryDicomAsJsonFiles": bool,
    "Housekeeper.Triggers.DicomWebCache": bool,
}


# Contraintes de valeur, en plus du type.
#
# Le type seul ne suffit pas : DicomPort = 99999 est un entier, produit un JSON
# parfaitement valide, et reste un numero de port qui n'existe pas. Orthanc
# refuse alors de demarrer -- constate. Le retour arriere automatique du
# redemarrage rattrape ce genre de cas, mais mieux vaut refuser la valeur tout
# de suite, avec un message qui dit quoi corriger.
#
# On ne contraint que ce dont on est sur. Une borne inventee bloquerait une
# configuration valide, ce qui est pire que pas de borne du tout : les champs
# absents de cette table restent simplement typiques.
ORTHANC_RANGES: dict[str, tuple[int, int]] = {
    # Ports TCP.
    "DicomPort": (1, 65535),
    "HttpPort": (1, 65535),
    # Delais et tailles : un negatif n'a pas de sens, et zero desactive
    # lorsque c'est permis.
    "DicomScpTimeout": (0, 86400),
    "HttpTimeout": (0, 86400),
    "StableAge": (0, 86400),
    "Housekeeper.ThrottleDelay": (0, 86400),
    "JobsHistorySize": (0, 100000),
    "LimitFindResults": (0, 1000000),
    "LimitFindInstances": (0, 1000000),
    "MaximumStorageSize": (0, 10000000),
    "MaximumPatientCount": (0, 10000000),
    "DicomWeb.StowMaxInstances": (0, 1000000),
    "DicomWeb.StowMaxSize": (0, 1000000),
    # Au moins un fil d'execution, sinon Orthanc ne traite plus rien.
    "DicomThreadsCount": (1, 256),
    "ConcurrentJobs": (1, 256),
    # Duree en jours proposee par defaut sur un lien de partage ; 0 = sans
    # limite de date.
    "OrthancExplorer2.UiOptions.DefaultShareDuration": (0, 3650),
    # Validite d'un lien instantane, en secondes.
    "OrthancExplorer2.Tokens.InstantLinksValidity": (1, 86400),
}

# Type des elements d'une liste. Sans cela, ["PatientID", 42] passerait le
# controle -- c'est bien une liste -- et Orthanc buterait dessus au demarrage.
ORTHANC_ELEMENT_TYPES: dict[str, type] = {
    "OrthancExplorer2.UiOptions.StudyListColumns": str,
    "OrthancExplorer2.UiOptions.ViewersOrdering": str,
    "OrthancExplorer2.UiOptions.ShareDurations": int,
}


ORTHANC_ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    # Explorer applique cette valeur a l'attribut data-bs-theme de Bootstrap,
    # qui ne connait que ces deux modes.
    "OrthancExplorer2.Theme": ("light", "dark"),
    "LogLevel": ("default", "verbose", "trace"),
    "MaximumStorageMode": ("Recycle", "Reject"),
    "OrthancExplorer2.Tokens.ShareType": (
        "ohif-viewer-publication",
        "stone-viewer-publication",
        "volview-viewer-publication",
    ),
}


def _apply_scalar_change(config: dict, dotted: str, value: Any) -> None:
    """Set config[a][b][c] = value. Refuse si le path ecrase un dict/array."""
    if dotted not in ORTHANC_EDITABLE_PATHS:
        raise ValueError(f"{dotted}: non editable via UI")
    expected_type = ORTHANC_EDITABLE_PATHS[dotted]
    if not isinstance(value, expected_type):
        raise ValueError(f"{dotted}: attendu {expected_type.__name__}, recu {type(value).__name__}")

    # En Python un booleen EST un entier : isinstance(True, int) est vrai.
    # Sans ce refus explicite, "DicomPort": true franchit le controle de type,
    # puis vaut 1 face aux bornes -- et Orthanc se retrouve a ecouter sur le
    # port 1. Trouve par un test, apres que la garde precedente ait justement
    # dispense les booleens du controle de bornes.
    if expected_type is int and isinstance(value, bool):
        raise ValueError(f"{dotted}: attendu int, recu bool")
    if dotted == "DicomAet" and len(value) > 16:
        raise ValueError("DicomAet: max 16 caracteres (norme DICOM)")

    if dotted in ORTHANC_RANGES:
        mini, maxi = ORTHANC_RANGES[dotted]
        if not mini <= value <= maxi:
            raise ValueError(
                f"{dotted}: attendu entre {mini} et {maxi}, recu {value}")

    if dotted in ORTHANC_ELEMENT_TYPES:
        attendu_el = ORTHANC_ELEMENT_TYPES[dotted]
        for element in value:
            # Meme piege que plus haut : un booleen est un entier.
            if isinstance(element, bool) or not isinstance(element, attendu_el):
                raise ValueError(
                    f"{dotted}: chaque entree doit etre de type "
                    f"{attendu_el.__name__}, recu {element!r}")
        if attendu_el is int and any(e < 0 for e in value):
            raise ValueError(f"{dotted}: une duree negative n'a pas de sens")
        if attendu_el is str and any(not e.strip() for e in value):
            raise ValueError(f"{dotted}: une entree vide n'a pas de sens")
        if len(set(value)) != len(value):
            raise ValueError(f"{dotted}: la liste contient des doublons")

    if dotted in ORTHANC_ALLOWED_VALUES:
        admises = ORTHANC_ALLOWED_VALUES[dotted]
        if value not in admises:
            raise ValueError(
                f"{dotted}: valeur inconnue {value!r}. "
                f"Attendu : {', '.join(admises)}")

    keys = dotted.split(".")
    node = config
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


# ============================================================================
# Ecriture d'orthanc.json sans perdre ce qui l'entoure
# ============================================================================
#
# Reconstruire le fichier avec json.dumps() efface tout ce que la structure ne
# porte pas : commentaires, ordre des cles, groupements. Verifie sur une
# installation reelle -- la premiere modification faite depuis le panel a
# supprime les 44 commentaires du fichier, soit l'essentiel de sa
# documentation. Sur un PACS, ce fichier est ce qu'on relit pour comprendre ce
# qui est active et pourquoi.
#
# On edite donc le texte a la place : seule la valeur modifiee est remplacee,
# le reste du fichier est recopie a l'octet pres.


def _fin_de_chaine(texte: str, debut: int) -> int:
    """Index du guillemet fermant de la chaine ouverte a `debut`."""
    i = debut + 1
    n = len(texte)
    while i < n:
        if texte[i] == "\\":
            i += 2
            continue
        if texte[i] == '"':
            return i
        i += 1
    return n - 1


def _scan_json(texte: str) -> tuple[dict[str, tuple[int, int]],
                                        dict[str, tuple[int, int]]]:
    """Releve, par chemin pointe, ou se trouvent les valeurs et les objets.

    Parcourt le texte en tenant a jour une pile de conteneurs, en sautant
    chaines et commentaires. Renvoie deux tables : les bornes de chaque valeur
    scalaire, et celles de chaque objet -- ces dernieres servent a inserer une
    cle absente au bon endroit. L'objet racine a pour chemin la chaine vide.
    """
    positions: dict[str, tuple[int, int]] = {}
    objets: dict[str, tuple[int, int]] = {}
    pile: list[dict] = []
    n = len(texte)
    i = 0
    attend_valeur = False

    def sauter(j: int) -> int:
        """Avance jusqu'au prochain caractere significatif."""
        while j < n:
            if texte[j] in " \t\r\n":
                j += 1
            elif texte[j] == "/" and j + 1 < n and texte[j + 1] == "/":
                f = texte.find("\n", j)
                j = n if f == -1 else f + 1
            elif texte[j] == "/" and j + 1 < n and texte[j + 1] == "*":
                f = texte.find("*/", j + 2)
                j = n if f == -1 else f + 2
            else:
                return j
        return n

    def chemin() -> str:
        return ".".join(e["cle"] for e in pile if e["cle"] is not None)

    while i < n:
        i = sauter(i)
        if i >= n:
            break
        c = texte[i]

        if c in "{[":
            # Le chemin de l'objet qu'on ouvre est celui du contexte courant,
            # cle du parent comprise : il faut donc le relever avant d'empiler.
            dans_objet = bool(pile) and pile[-1]["type"] == "{"
            pile.append({"type": c, "cle": None, "chemin": chemin(),
                         "debut": i, "nomme": attend_valeur and dans_objet})
            attend_valeur = False
            i += 1
        elif c in "}]":
            if pile:
                ferme = pile.pop()
                if ferme["type"] == "{":
                    objets[ferme["chemin"]] = (ferme["debut"], i)
                elif ferme["nomme"]:
                    # Tableau porte par une cle : on retient ses bornes pour
                    # pouvoir le remplacer entierement. Les elements qu'il
                    # contient n'ont pas de chemin propre et ne sont donc pas
                    # releves -- on ne modifie jamais une case isolee.
                    positions[ferme["chemin"]] = (ferme["debut"], i + 1)
            attend_valeur = False
            i += 1
        elif c == ",":
            if pile and pile[-1]["type"] == "{":
                pile[-1]["cle"] = None
            attend_valeur = False
            i += 1
        elif c == ":":
            attend_valeur = True
            i += 1
        elif c == '"':
            fin = _fin_de_chaine(texte, i)
            if pile and pile[-1]["type"] == "{" and not attend_valeur:
                pile[-1]["cle"] = texte[i + 1:fin]
            elif attend_valeur and pile and pile[-1]["type"] == "{":
                positions[chemin()] = (i, fin + 1)
                attend_valeur = False
            i = fin + 1
        else:
            # Scalaire sans guillemets : nombre, true, false, null. On s'arrete
            # au premier separateur ou au debut d'un commentaire de fin de
            # ligne, sans quoi celui-ci serait avale avec la valeur.
            j = i
            while j < n:
                if texte[j] in ",}]\n":
                    break
                if texte[j] == "/" and j + 1 < n and texte[j + 1] in "/*":
                    break
                j += 1
            fin = j
            while fin > i and texte[fin - 1] in " \t\r":
                fin -= 1
            if attend_valeur and pile and pile[-1]["type"] == "{":
                positions[chemin()] = (i, fin)
            attend_valeur = False
            i = j

    return positions, objets


def _insert_key(texte: str, objet: tuple[int, int], cle: str, valeur: Any) -> str:
    """Ajoute `cle` a la fin de l'objet dont les bornes sont donnees.

    Se place apres la derniere valeur de l'objet, et non juste avant
    l'accolade fermante : un commentaire final resterait ainsi a sa place, au
    lieu de se retrouver coince entre la nouvelle cle et la precedente.
    """
    debut, fin = objet

    # Reculer depuis l'accolade fermante jusqu'au dernier caractere qui porte
    # du contenu, en sautant blancs et commentaires.
    j = fin - 1
    while j > debut:
        c = texte[j]
        if c in " \t\r\n":
            j -= 1
            continue
        # Fin d'un commentaire de bloc ?
        if c == "/" and j - 1 > debut and texte[j - 1] == "*":
            ouverture = texte.rfind("/*", debut, j)
            if ouverture == -1:
                break
            j = ouverture - 1
            continue
        # Ligne de commentaire ? On regarde si un // la precede sur la ligne.
        debut_ligne = texte.rfind("\n", debut, j) + 1
        marque = texte.find("//", debut_ligne, j + 1)
        if marque != -1 and '"' not in texte[debut_ligne:marque]:
            j = debut_ligne - 1
            continue
        break

    # Un cran de plus que l'accolade fermante. Celle de l'objet racine est en
    # colonne 0 : la portion de ligne qui la precede est vide, ce qui donne
    # bien une indentation de deux espaces.
    ligne = texte.rfind("\n", 0, fin) + 1
    avant_accolade = texte[ligne:fin]
    blancs = len(avant_accolade) - len(avant_accolade.lstrip())
    indentation = " " * (blancs + 2)

    rendu = f'"{cle}": {json.dumps(valeur, ensure_ascii=False)}'
    if texte[j] == "{":  # objet vide
        return texte[:j + 1] + f"\n{indentation}{rendu}\n" + texte[j + 1:]
    return texte[:j + 1] + f",\n{indentation}{rendu}" + texte[j + 1:]


def _render_value(valeur: Any, texte: str, debut: int) -> str:
    """Serialise une valeur pour l'inserer dans le texte.

    Une liste est ecrite sur plusieurs lignes, indentee comme la cle qui la
    porte : ces tableaux comptent parfois une dizaine d'entrees, et une seule
    ligne interminable serait illisible dans un fichier qu'on relit pour
    comprendre.
    """
    if not isinstance(valeur, list) or not valeur:
        return json.dumps(valeur, ensure_ascii=False)

    ligne = texte.rfind("\n", 0, debut) + 1
    avant = texte[ligne:debut]
    marge = " " * (len(avant) - len(avant.lstrip()))
    elements = ",\n".join(
        f"{marge}  {json.dumps(e, ensure_ascii=False)}" for e in valeur)
    return "[\n" + elements + f"\n{marge}]"


def _apply_text_changes(texte: str, changements: dict[str, Any]) -> str:
    """Applique les changements au texte en preservant tout le reste.

    Une cle deja presente voit sa valeur remplacee sur place. Une cle absente
    est ajoutee a la fin de son objet : le panel expose des reglages
    qu'Orthanc laisse implicites, et les definir est un cas courant, pas une
    exception.

    Leve ValueError si l'objet parent lui-meme n'existe pas -- creer une
    arborescence demanderait de deviner une mise en forme. L'appelant retombe
    alors sur une reecriture complete, en connaissance de cause.
    """
    positions, objets = _scan_json(texte)

    presentes = {c: v for c, v in changements.items() if c in positions}
    absentes = {c: v for c, v in changements.items() if c not in positions}

    # Une cle que le scanner n'a pas relevee mais qui existe bel et bien dans
    # la structure signale un type qu'il ne sait pas traiter. L'inserer
    # produirait un doublon -- deux fois la meme cle dans le meme objet --
    # que la relecture ne verrait pas, json.loads ne retenant que la
    # derniere. Mieux vaut refuser et laisser l'appelant regenerer.
    structure = json.loads(_strip_json_comments(texte))
    for chemin in absentes:
        noeud = structure
        for morceau in chemin.split("."):
            if not isinstance(noeud, dict) or morceau not in noeud:
                noeud = None
                break
            noeud = noeud[morceau]
        else:
            raise ValueError(
                f"{chemin} : deja present mais non localisable dans le texte "
                f"(type non gere par l'analyse)")

    for chemin in absentes:
        parent = chemin.rsplit(".", 1)[0] if "." in chemin else ""
        if parent not in objets:
            raise ValueError(f"{chemin} : objet parent absent du fichier")

    # De la fin vers le debut : les index releves restent valides.
    for chemin in sorted(presentes, key=lambda c: positions[c][0], reverse=True):
        debut, fin = positions[chemin]
        texte = texte[:debut] + _render_value(presentes[chemin], texte, debut) + texte[fin:]

    # Chaque insertion decale ce qui suit : on repart d'une analyse fraiche.
    for chemin, valeur in absentes.items():
        _, objets = _scan_json(texte)
        parent = chemin.rsplit(".", 1)[0] if "." in chemin else ""
        cle = chemin.rsplit(".", 1)[-1]
        texte = _insert_key(texte, objets[parent], cle, valeur)

    return texte


def _validate_orthanc(config: dict) -> None:
    """Invariants critiques a preserver."""
    # Flags de persistance sinon les modalites saisies via UI disparaissent
    if not config.get("DicomModalitiesInDatabase"):
        raise ValueError("DicomModalitiesInDatabase doit rester true (perdrait les modalites au restart)")
    if not config.get("OrthancPeersInDatabase"):
        raise ValueError("OrthancPeersInDatabase doit rester true")
    # DicomAet max 16 chars
    if len(config.get("DicomAet", "")) > 16:
        raise ValueError("DicomAet: max 16 caracteres")


async def _reload_orthanc() -> None:
    """POST /tools/reset : Orthanc re-parse le JSON et applique la nouvelle config."""
    _require_orthanc_creds()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{ORTHANC_URL}/tools/reset",
            auth=(ORTHANC_USER, ORTHANC_PASS),
            # Le plugin Authorization d'Orthanc lit Remote-User (declare dans
            # TokenHttpHeaders) et le transmet a /user/get-profile comme
            # identite. Sans cet en-tete, l'appel est vu comme anonyme : le
            # profil correspondant n'a que la permission "upload" et /tools/
            # est refuse (403). "admin" donne la permission "settings", requise
            # pour recharger la configuration.
            headers={"Remote-User": "admin"},
        )
        r.raise_for_status()


async def _orthanc(methode: str, chemin: str, **kwargs: Any) -> httpx.Response:
    """Appelle l'API d'Orthanc avec le compte de service.

    Remote-User: admin est indispensable -- le plugin Authorization le lit
    pour determiner le profil. Sans lui l'appel passe pour anonyme, profil
    qui n'a que la permission d'import.
    """
    _require_orthanc_creds()
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.request(
            methode, f"{ORTHANC_URL}{chemin}",
            auth=(ORTHANC_USER, ORTHANC_PASS),
            headers={"Remote-User": "admin"},
            **kwargs,
        )


class OrthancConfigPayload(BaseModel):
    """Body de PATCH : {"changes": {"Name": "Foo", "DicomAet": "BAR"}}"""
    changes: dict[str, Any]


# ============================================================================
# Routes : setup wizard (unauthenticated)
# ============================================================================

router = APIRouter()


@router.get("/api/admin/whoami")
async def admin_whoami(admin: AdminUser = Depends(require_admin)):
    """
    Identite et version, consommees par le hub au chargement.

    C'est aussi ici qu'est pose le cookie CSRF : le SPA etant servi comme un
    fichier statique, aucune route ne le rend cote serveur. whoami est le
    premier appel authentifie du hub, donc le bon endroit pour l'emettre.
    """
    csrf = pysecrets.token_urlsafe(32)

    # Nom du serveur, tel qu'Orthanc l'applique reellement. Le panel
    # l'affichait en dur ("Orthanc") : renommer le serveur restait donc sans
    # effet sur son propre panel, alors qu'Orthanc Explorer, lui, montrait le
    # bon nom. On lit la valeur effective plutot que le fichier de
    # configuration, qui peut avoir ete modifie sans redemarrage.
    nom_serveur = ""
    try:
        r = await _orthanc("GET", "/system")
        if r.status_code == 200:
            nom_serveur = r.json().get("Name", "")
    except Exception:  # noqa: BLE001 - Orthanc indisponible ne doit pas casser le panel
        pass

    resp = JSONResponse(
        {
            "username": admin.username,
            "image_version": IMAGE_VERSION,
            "server_name": nom_serveur,
        },
    )
    resp.set_cookie(
        CSRF_COOKIE, csrf,
        secure=True, httponly=False, samesite="strict", max_age=3600,
    )
    return resp


async def _setup_completed() -> bool:
    """L'installation a-t-elle deja eu lieu ?

    Le drapeau vit dans Redis, qui est un cache : le vider -- volume efface,
    migration, docker volume prune -- rouvrirait l'assistant d'installation
    sur un PACS en service, et un tiers pourrait s'y creer un compte
    administrateur.

    On croise donc avec une verite persistante : l'existence d'un
    administrateur actif autre que le compte d'amorcage. Elle vit dans
    users_database.yml, qui est sauvegarde a chaque ecriture et ne depend
    d'aucun cache. Tant que seul bootstrap@localhost existe, l'installation
    reste ouverte -- c'est bien le premier lancement.
    """
    try:
        if (await _r().get(SETUP_KEY)) == "1":
            return True
    except Exception:  # noqa: BLE001 - Redis indisponible : on tranche sur le fichier
        pass

    try:
        data = _load_authelia()
    except Exception:  # noqa: BLE001 - fichier illisible : ne pas ouvrir le wizard
        return True

    return any(
        not infos.get("disabled") and "admin" in (infos.get("groups") or [])
        for nom, infos in (data.get("users") or {}).items()
        if nom != BOOTSTRAP_USERNAME
    )


@router.post("/setup/create-admin")
async def setup_create_admin(payload: UserCreatePayload):
    """
    Etape 1 : cree LE premier admin. Un seul appel autorise jusqu'a finalize.

    Verrouille apres le 1er succes via SETUP_FIRST_ADMIN_KEY pour empecher un
    tiers de creer un deuxieme admin en profitant de la fenetre ouverte du wizard.
    Pour ajouter d'autres admins ensuite : POST /api/admin/users (auth requise).
    """
    # L'ordre de ces trois refus n'est pas indifferent : ils repondent tous
    # 409, mais chacun indique une suite differente a donner. Tester d'abord
    # le cas le plus avance evitait de renvoyer vers /api/admin/users
    # quelqu'un qui n'a pas encore finalise son installation.
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, _msg("err_setup_done_use_users",
                                "setup deja finalise, utiliser /api/admin/users"))
    if (await _r().get(SETUP_FIRST_ADMIN_KEY)) == "1":
        raise HTTPException(
            409,
            "un admin a deja ete cree — finaliser le setup (POST /auth/setup/finalize) "
            "puis utiliser /api/admin/users pour en ajouter d'autres",
        )
    # Dernier filet, celui qui ne depend pas du cache : un administrateur
    # reel existe deja. C'est ici qu'un tiers profiterait d'un Redis vide
    # pour se creer un compte sur une installation en service.
    if await _setup_completed():
        raise HTTPException(
            409,
            "un administrateur existe deja sur cette installation — se "
            "connecter avec ce compte pour en ajouter d'autres",
        )
    if "admin" not in payload.groups:
        payload.groups.append("admin")
    data = _load_authelia()
    if payload.username in data.get("users", {}):
        raise HTTPException(409, _msg("err_user_exists_named",
                                "user {username} existe deja",
                                username=payload.username))
    data.setdefault("users", {})[payload.username] = {
        "disabled": False,
        "displayname": payload.displayname,
        "email": str(payload.email),
        "password": _hasher.hash(payload.password),
        "groups": payload.groups,
    }
    _write_authelia(data)
    # Verrouille la fenetre : plus qu'un finalize acceptable maintenant
    await _r().set(SETUP_FIRST_ADMIN_KEY, "1")
    await _audit("setup.admin.created", actor="wizard", target=payload.username)
    return {"ok": True, "username": payload.username}


@router.post("/setup/finalize")
async def setup_finalize():
    """Etape finale : verifie l'invariant admin actif, supprime le compte
    d'amorcage, puis marque le setup comme termine."""
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, _msg("err_setup_done", "setup deja finalise"))
    data = _load_authelia()
    admins = [
        u for u, i in data.get("users", {}).items()
        if not i.get("disabled") and "admin" in (i.get("groups") or [])
    ]
    if not admins:
        raise HTTPException(400, _msg("err_create_admin_first",
                                "creer d'abord un admin (POST /auth/setup/create-admin)"))

    # Un vrai administrateur existe : le compte d'amorcage n'a plus de raison
    # d'etre et n'a pas a rester visible dans le panel. Suppression par nom
    # exact — si l'exploitant l'a renomme ou s'en est servi comme compte
    # reel, rien n'est touche. L'ecriture precede le flip du drapeau : si
    # elle echoue, le setup n'est pas marque comme termine.
    bootstrap_removed = None
    if BOOTSTRAP_USERNAME in data.get("users", {}):
        del data["users"][BOOTSTRAP_USERNAME]
        _write_authelia(data)
        bootstrap_removed = BOOTSTRAP_USERNAME

    await _r().set(SETUP_KEY, "1")
    await _r().delete(SETUP_FIRST_ADMIN_KEY)  # verrou setup levee, ne sert plus
    await _audit(
        "setup.finalized", actor="wizard", admin_count=len(admins),
        bootstrap_removed=bootstrap_removed,
    )
    return {"ok": True, "admins": admins, "bootstrap_removed": bootstrap_removed}


@router.get("/setup/network")
async def setup_network_get():
    """URL publique actuelle, pour preremplir le champ du wizard."""
    return {
        "public_url": _read_env_var("PUBLIC_URL"),
        "editable": ENV_FILE.exists(),
    }


@router.post("/setup/network")
async def setup_network(payload: PublicUrlPayload):
    """Etape optionnelle du wizard : declarer l'URL publique definitive.

    A appeler AVANT finalize. Le changement ne prend effet qu'au redemarrage
    de la pile, et invalide la session en cours puisque le cookie est lie a
    l'ancien domaine.
    """
    if await _setup_completed():
        raise HTTPException(
            409, "setup deja finalise, utiliser /api/admin/network",
        )
    return await _apply_public_url(payload.public_url, actor="wizard")


# ============================================================================
# Route : /api/admin/sharing (viewer par defaut des liens de partage)
# ============================================================================

# Les libelles restent cote frontend, qui les traduit ; ici on ne garde que ce
# qui doit etre valide au serveur.
SHARE_VIEWERS = (
    "ohif-viewer-publication",
    "stone-viewer-publication",
    "volview-viewer-publication",
)


# Langues pour lesquelles un fichier de traduction est livre.
AVAILABLE_LANGUAGES = ("en", "fr")


def _read_share_type() -> str:
    """Viewer preselectionne au partage, tel qu'il figure dans orthanc.json.

    Lu dans le fichier et non dans Orthanc : c'est la valeur qui s'appliquera,
    y compris quand un redemarrage reste a faire. Le panel signale par
    ailleurs qu'il est necessaire.
    """
    try:
        config = _load_orthanc_config()
    except Exception:  # noqa: BLE001 - fichier absent ou illisible
        return SHARE_VIEWERS[0]
    valeur = (config.get("OrthancExplorer2", {})
              .get("Tokens", {})
              .get("ShareType", ""))
    return valeur if valeur in SHARE_VIEWERS else SHARE_VIEWERS[0]


class LanguagePayload(BaseModel):
    language: str


@router.get("/api/admin/preferences")
async def admin_preferences_get(admin: AdminUser = Depends(require_admin)):
    """Preferences d'interface : celles qui ne vivent pas dans orthanc.json.

    Le viewer de partage n'est plus ici. C'est un champ de configuration
    Orthanc comme un autre (OrthancExplorer2.Tokens.ShareType), edite depuis
    l'onglet Configuration : l'exposer aussi ici donnait deux chemins pour
    ecrire la meme valeur, dans deux onglets differents.
    """
    language = (_read_setting("language", "LANGUAGE")
                or _read_setting("langue"))
    return {
        "language": language if language in AVAILABLE_LANGUAGES else AVAILABLE_LANGUAGES[0],
        "languages": list(AVAILABLE_LANGUAGES),
        "editable": True,
    }


@router.put("/api/admin/language")
async def admin_language_put(
    payload: LanguagePayload, admin: AdminUser = Depends(require_admin),
):
    """Change la langue de l'interface.

    Prend effet a la requete suivante : les translations sont resolues a
    l'affichage, et non chargees une fois pour toutes au demarrage.
    """
    if payload.language not in AVAILABLE_LANGUAGES:
        raise HTTPException(
            400,
            f"langue inconnue : {payload.language}. "
            f"Attendu : {', '.join(AVAILABLE_LANGUAGES)}",
        )
    _write_setting("language", payload.language)
    await _audit("interface.language.updated", admin.username,
                 language=payload.language)
    return {"ok": True, "language": payload.language}


@router.get("/api/admin/network")
async def admin_network_get(admin: AdminUser = Depends(require_admin)):
    """URL publique actuelle et possibilite de la modifier."""
    return {
        "public_url": _read_env_var("PUBLIC_URL"),
        "editable": ENV_FILE.exists(),
    }


@router.post("/api/admin/network")
async def admin_network(
    payload: PublicUrlPayload, admin: AdminUser = Depends(require_admin),
):
    """Change l'URL publique apres l'installation.

    Meme consequence que pendant le wizard : redemarrage necessaire, et
    reconnexion sur la nouvelle adresse.
    """
    return await _apply_public_url(payload.public_url, actor=admin.username)


# ============================================================================
# Routes : /api/admin/users/* (auth requise)
# ============================================================================

@router.get("/api/admin/users")
async def list_users(admin: AdminUser = Depends(require_admin)):
    data = _load_authelia()
    # Ne jamais renvoyer les hashes
    return {
        "users": [
            {
                "username": u,
                "displayname": i.get("displayname"),
                "email": i.get("email"),
                "groups": i.get("groups", []),
                "disabled": i.get("disabled", False),
            }
            for u, i in data.get("users", {}).items()
        ]
    }


@router.post("/api/admin/users")
async def add_user(payload: UserCreatePayload, admin: AdminUser = Depends(require_admin)):
    data = _load_authelia()
    if payload.username in data.get("users", {}):
        raise HTTPException(409, _msg("err_user_exists", "user existe deja"))
    data.setdefault("users", {})[payload.username] = {
        "disabled": False,
        "displayname": payload.displayname,
        "email": str(payload.email),
        "password": _hasher.hash(payload.password),
        "groups": payload.groups,
    }
    _write_authelia(data)
    await _audit("authelia.user.added", admin.username, target=payload.username)
    return {"ok": True, "reload": "auto (~2s via watch)"}


@router.patch("/api/admin/users/{username}")
async def update_user(
    username: str,
    payload: UserUpdatePayload,
    admin: AdminUser = Depends(require_admin),
):
    """Modifie un compte existant sans toucher a son mot de passe.

    Jusqu'ici le panel ne savait que creer et supprimer : changer le groupe de
    quelqu'un imposait de detruire son compte et de le recreer, ce qui lui
    faisait perdre son mot de passe au passage.

    L'invariant "au moins un administrateur actif" est verifie a l'ecriture :
    se retirer du groupe admin ou se desactiver soi-meme alors qu'on est le
    dernier est donc refuse avec un message explicite.
    """
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, _msg("err_user_unknown", "user inconnu"))

    infos = data["users"][username]
    modifies = []
    if payload.displayname is not None:
        infos["displayname"] = payload.displayname
        modifies.append("displayname")
    if payload.email is not None:
        infos["email"] = str(payload.email)
        modifies.append("email")
    if payload.groups is not None:
        infos["groups"] = payload.groups
        modifies.append("groups")
    if payload.disabled is not None:
        infos["disabled"] = payload.disabled
        modifies.append("disabled")

    if not modifies:
        raise HTTPException(400, _msg("err_no_field_to_change", "aucun champ a modifier"))

    _write_authelia(data)
    await _audit(
        "authelia.user.updated", admin.username, target=username,
        fields=",".join(modifies),
    )
    return {"ok": True, "modified": modifies}


@router.patch("/api/admin/users/{username}/password")
async def change_password(
    username: str,
    payload: PasswordChangePayload,
    admin: AdminUser = Depends(require_admin),
):
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, _msg("err_user_unknown", "user inconnu"))
    data["users"][username]["password"] = _hasher.hash(payload.new_password)
    _write_authelia(data)
    await _audit("authelia.password.changed", admin.username, target=username)
    return {"ok": True}


@router.delete("/api/admin/users/{username}")
async def delete_user(username: str, admin: AdminUser = Depends(require_admin)):
    if username == admin.username:
        raise HTTPException(400, _msg("err_cannot_delete_self",
                                "impossible de te supprimer toi-meme"))
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, _msg("err_user_unknown", "user inconnu"))
    del data["users"][username]
    _write_authelia(data)  # valide invariant "au moins 1 admin actif"
    await _audit("authelia.user.deleted", admin.username, target=username)
    return {"ok": True}


# ============================================================================
# Routes : /api/admin/orthanc/config
# ============================================================================

@router.get("/api/admin/orthanc/config")
async def read_orthanc_config(admin: AdminUser = Depends(require_admin)):
    config = _load_orthanc_config()

    # Le type est celui DECLARE, pas celui de la valeur lue : plus de la
    # moitie des parametres sont absents d'orthanc.json (Orthanc applique
    # alors ses valeurs par defaut) et ressortaient donc a None. L'interface,
    # qui deduisait le type de la valeur, affichait un champ texte pour un
    # booleen -- inutilisable, et refuse a l'enregistrement puisque le
    # serveur attend un vrai booleen et non la chaine "true".
    noms = {bool: "bool", int: "int", str: "str", list: "list"}

    result, meta = {}, {}
    for dotted, attendu in ORTHANC_EDITABLE_PATHS.items():
        node = config
        present = True
        for k in dotted.split("."):
            if not isinstance(node, dict) or k not in node:
                node, present = None, False
                break
            node = node[k]
        result[dotted] = node
        bornes = ORTHANC_RANGES.get(dotted)
        meta[dotted] = {
            "type": noms.get(attendu, "str"),
            # Transmises a l'interface pour qu'elle propose une liste plutot
            # qu'un champ libre, et signale une borne avant l'envoi.
            "min": bornes[0] if bornes else None,
            "max": bornes[1] if bornes else None,
            "choices": list(ORTHANC_ALLOWED_VALUES.get(dotted, ())) or None,
            # Distingue "absent du fichier" de "present et vide" : dans le
            # premier cas Orthanc applique sa valeur par defaut.
            "present": present,
            # La valeur par defaut, quand elle est connue. Annoncer "valeur
            # par defaut" sans la montrer n'apprend rien : l'exploitant ne
            # sait pas ce qui s'applique reellement.
            "default": ORTHANC_DEFAULTS.get(dotted),
        }

    return {"editable": result, "fields": meta}


@router.patch("/api/admin/orthanc/config")
async def update_orthanc_config(
    payload: OrthancConfigPayload,
    admin: AdminUser = Depends(require_admin),
):
    """Applique une batch de changements, backup, /tools/reset, audit."""
    lock = FileLock(str(ORTHANC_JSON) + ".lock", timeout=5)
    try:
        with lock:
            config = _load_orthanc_config()  # gere JSON corrompu
            for path, value in payload.changes.items():
                _apply_scalar_change(config, path, value)
            _validate_orthanc(config)
            backup = _backup(ORTHANC_JSON)

            # On edite le texte plutot que de le regenerer, pour ne pas
            # effacer les commentaires qui documentent chaque reglage.
            #
            # Le resultat est relu et compare a la structure attendue : une
            # edition textuelle qui produirait autre chose que le JSON voulu
            # doit etre detectee ici, jamais decouverte par Orthanc au
            # demarrage suivant.
            brut = ORTHANC_JSON.read_text(encoding="utf-8")
            try:
                serialized = _apply_text_changes(brut, payload.changes)
                relu = json.loads(_strip_json_comments(serialized))
                if relu != config:
                    raise ValueError("relecture divergente")
            except ValueError as raison:
                # Cle absente du fichier, ou relecture inattendue : on
                # regenere. Les commentaires sont perdus, ce que l'appelant
                # apprend dans la reponse plutot que de le decouvrir plus tard.
                logger.warning(
                    "orthanc.json regenere (%s) : les commentaires seront perdus",
                    raison,
                )
                serialized = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
                commentaires_perdus = True
            else:
                commentaires_perdus = False

            _atomic_write(ORTHANC_JSON, serialized)
    except Timeout as e:
        raise HTTPException(423, _msg("err_orthanc_json_locked",
                                "orthanc.json verrouille, retry")) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    reset_error = None
    try:
        await _reload_orthanc()
    except httpx.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)

        # Un refus explicite du plugin Authorization ne remet pas en cause
        # l'ecriture : le fichier est valide, seul le rechargement a chaud est
        # indisponible. /tools/reset n'est couvert par aucun motif de
        # permission du plugin, qui le rejette sans meme consulter
        # auth-service. Conserver la modification et indiquer la marche a
        # suivre vaut mieux que de la perdre.
        if status in (401, 403):
            await _audit(
                "orthanc.config.updated_pending_restart",
                admin.username,
                fields=",".join(payload.changes.keys()),
                backup=backup.name,
            )
            return {
                "ok": True,
                "backup": backup.name,
                "restart_required": True,
                "commentaires_perdus": commentaires_perdus,
                "message": (
                    "Configuration enregistree. Elle ne prendra effet qu'apres "
                    "redemarrage d'Orthanc : utiliser le bouton Redemarrer, ou "
                    "docker compose restart orthanc"
                ),
            }

        # Tout autre echec (panne reseau, erreur serveur) laisse Orthanc dans
        # un etat incertain : on restaure le fichier precedent.
        reset_error = str(e)
        try:
            shutil.copy2(backup, ORTHANC_JSON)
            await _reload_orthanc()
        except (httpx.HTTPError, OSError) as rollback_err:
            await _audit(
                "orthanc.config.rollback_failed",
                admin.username,
                original_error=reset_error,
                rollback_error=str(rollback_err),
                backup=backup.name,
            )
            raise HTTPException(
                502,
                f"reload Orthanc echoue ({reset_error}). Rollback auto echoue aussi "
                f"({rollback_err}). Etat incoherent, restauration manuelle requise : "
                f"backup={backup.name}",
            ) from e
        await _audit(
            "orthanc.config.rolled_back",
            admin.username,
            reason=reset_error,
            backup=backup.name,
        )
        raise HTTPException(
            502,
            f"reload Orthanc echoue ({reset_error}). Rollback automatique effectue "
            f"depuis {backup.name}. Config restee dans l'etat precedent.",
        ) from e

    await _audit(
        "orthanc.config.updated",
        admin.username,
        fields=",".join(payload.changes.keys()),
        backup=backup.name,
    )
    reponse = {"ok": True, "backup": backup.name}
    if commentaires_perdus:
        reponse["message"] = (
            "Configuration enregistree, mais le fichier a du etre regenere : "
            f"ses commentaires ont ete perdus. Copie intacte : {backup.name}"
        )
    return reponse


# ============================================================================
# Route : /api/admin/orthanc/restart
# ============================================================================

# Ce qu'on ecrit dans orthanc.json et ce qu'Orthanc applique reellement sont
# deux choses differentes. Trois causes possibles d'ecart :
#
#   - une variable ORTHANC__* du compose ecrase la valeur du fichier, en
#     silence et definitivement ;
#   - le champ est declare au mauvais endroit de l'arborescence, et Orthanc
#     applique sa valeur par defaut sans rien signaler. C'est arrive :
#     StudyListColumns vivait sous OrthancExplorer2 alors qu'Explorer le lit
#     sous UiOptions -- le reglage n'a jamais eu d'effet, et rien ne le
#     disait ;
#   - le redemarrage n'a pas eu lieu depuis la derniere modification.
#
# D'ou cette table : pour chaque reglage verifiable, ou aller chercher ce
# qu'Orthanc en dit. Tous ne le sont pas -- Orthanc n'expose pas sa
# configuration complete -- mais ceux-ci couvrent l'essentiel de ce que le
# panel modifie.
#
# EnableShares et EnableViewerQuickButton en sont volontairement absents :
# verifie, leur valeur depend du profil qui interroge -- vraie pour un
# administrateur, fausse pour un utilisateur externe. Ce sont des droits
# calcules, pas des reglages, et les comparer au fichier produirait une
# alerte permanente. Un verificateur qui crie au loup sur une valeur
# legitime ne sert plus a rien.
ORTHANC_VERIFIABLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "Name": ("/system", ("Name",)),
    "DicomAet": ("/system", ("DicomAet",)),
    "DicomPort": ("/system", ("DicomPort",)),
    "HttpPort": ("/system", ("HttpPort",)),
    "StorageCompression": ("/system", ("StorageCompression",)),
    "IngestTranscoding": ("/system", ("IngestTranscoding",)),
    "OrthancExplorer2.Tokens.ShareType": (
        "/ui/api/configuration", ("Tokens", "ShareType")),
    "OrthancExplorer2.Tokens.InstantLinksValidity": (
        "/ui/api/configuration", ("Tokens", "InstantLinksValidity")),
    "OrthancExplorer2.UiOptions.DefaultShareDuration": (
        "/ui/api/configuration", ("UiOptions", "DefaultShareDuration")),
    "OrthancExplorer2.UiOptions.ShareDurations": (
        "/ui/api/configuration", ("UiOptions", "ShareDurations")),
    "OrthancExplorer2.UiOptions.StudyListColumns": (
        "/ui/api/configuration", ("UiOptions", "StudyListColumns")),
    "OrthancExplorer2.UiOptions.ViewersOrdering": (
        "/ui/api/configuration", ("UiOptions", "ViewersOrdering")),
}


async def _check_effective_config() -> list[dict[str, Any]]:
    """Compare ce que declare orthanc.json a ce qu'Orthanc applique.

    Ne renvoie que les ecarts. Un champ absent du fichier n'en est pas un :
    Orthanc applique alors sa valeur par defaut, ce qui est le comportement
    attendu.
    """
    try:
        config = _load_orthanc_config()
    except Exception:  # noqa: BLE001 - fichier illisible, deja signale ailleurs
        return []

    reponses: dict[str, dict] = {}
    for endpoint in {e for e, _ in ORTHANC_VERIFIABLE.values()}:
        try:
            r = await _orthanc("GET", endpoint)
            reponses[endpoint] = r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001 - Orthanc muet : rien a comparer
            reponses[endpoint] = {}

    ecarts = []
    for chemin, (endpoint, acces) in ORTHANC_VERIFIABLE.items():
        voulu = config
        for morceau in chemin.split("."):
            if not isinstance(voulu, dict) or morceau not in voulu:
                voulu = None
                break
            voulu = voulu[morceau]
        if voulu is None:
            continue  # non declare : la valeur par defaut s'applique

        applique = reponses.get(endpoint) or {}
        for morceau in acces:
            if not isinstance(applique, dict) or morceau not in applique:
                applique = None
                break
            applique = applique[morceau]
        if applique is None:
            continue  # Orthanc ne l'expose pas dans cette version

        if voulu != applique:
            ecarts.append({
                "champ": chemin,
                "dans_le_fichier": voulu,
                "applique_par_orthanc": applique,
            })

    return ecarts


async def _wait_for_orthanc(tentatives: int = 30, pause: int = 2) -> str:
    """Attend qu'Orthanc reponde. Renvoie sa version, ou "" s'il reste muet.

    Orthanc ouvre son port avant d'avoir fini de charger ses plugins : on
    interroge /system, qui ne repond qu'une fois le serveur reellement pret.
    """
    for _ in range(tentatives):
        await asyncio.sleep(pause)
        try:
            sonde = await _orthanc("GET", "/system")
            if sonde.status_code == 200:
                return sonde.json().get("Version", "inconnue")
        except Exception:  # noqa: BLE001 - normal pendant le redemarrage
            pass
    return ""


def _latest_orthanc_backup() -> Path | None:
    """Sauvegarde d'orthanc.json la plus recente, si elle existe.

    Les noms portent un horodatage (orthanc.json.bak.AAAAMMJJ-HHMMSS), donc
    l'ordre alphabetique est l'ordre chronologique.
    """
    prefixe = ORTHANC_JSON.name + ".bak."
    sauvegardes = sorted(BACKUPS_DIR.glob(prefixe + "*"), reverse=True)
    return sauvegardes[0] if sauvegardes else None


async def _request_restart() -> None:
    """Demande le redemarrage du container au proxy Docker."""
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{DOCKER_PROXY_URL}/containers/{ORTHANC_CONTAINER}/restart",
        )
    if r.status_code == 404:
        raise HTTPException(
            502,
            f"Conteneur '{ORTHANC_CONTAINER}' introuvable. Verifier "
            f"ORTHANC_CONTAINER dans le fichier .env.",
        )
    if r.status_code not in (204, 304):
        raise HTTPException(
            502,
            f"Le proxy Docker a refuse le redemarrage (HTTP {r.status_code}). "
            f"Verifier ALLOW_RESTARTS sur le service socket-proxy.",
        )


@router.post("/api/admin/orthanc/restart")
async def restart_orthanc(admin: AdminUser = Depends(require_admin)):
    """Redemarre le conteneur Orthanc et attend qu'il reponde a nouveau.

    Un changement de configuration n'a d'effet qu'apres redemarrage : l'image
    orthancteam GENERE /tmp/orthanc.json au demarrage (defauts de l'image +
    /etc/orthanc/*.json + variables ORTHANC__*), et c'est ce fichier que le
    processus lit. /tools/reset ne relit que le fichier genere, donc ne voit
    aucune de nos modifications.

    A distance, un acces SSH n'est pas toujours disponible : sans cette route,
    modifier la configuration depuis le panel laisse l'exploitant bloque.

    On attend le retour effectif d'Orthanc plutot que de repondre des que
    Docker a rendu la main. Une configuration acceptee a l'ecriture peut tres
    bien empecher Orthanc de redemarrer ; l'exploitant doit l'apprendre ici, et
    non en decouvrant plus tard un PACS eteint.
    """
    if not DOCKER_PROXY_URL:
        raise HTTPException(
            503,
            "Redemarrage indisponible : DOCKER_PROXY_URL n'est pas defini. "
            "Activer le service socket-proxy, ou redemarrer manuellement "
            "(docker compose restart orthanc).",
        )

    await _audit("orthanc.restart.requested", admin.username,
                 container=ORTHANC_CONTAINER)

    try:
        await _request_restart()
    except httpx.HTTPError as e:
        await _audit("orthanc.restart.failed", admin.username, error=str(e))
        raise HTTPException(502, _msg("err_docker_proxy_unreachable",
                                "Proxy Docker injoignable : {detail}", detail=e)) from e
    except HTTPException:
        await _audit("orthanc.restart.failed", admin.username,
                     container=ORTHANC_CONTAINER)
        raise

    version = await _wait_for_orthanc()
    if version:
        await _audit("orthanc.restarted", admin.username,
                     container=ORTHANC_CONTAINER)
        # Orthanc repond : cela ne dit pas encore qu'il applique ce qu'on a
        # ecrit. On compare, plutot que d'annoncer un succes sur la foi d'un
        # simple redemarrage.
        ecarts = await _check_effective_config()
        if ecarts:
            await _audit("orthanc.config.divergente", admin.username,
                         champs=",".join(e["champ"] for e in ecarts))
            return {
                "ok": True,
                "version": version,
                "ecarts": ecarts,
                "message": (
                    f"Orthanc a redemarre, mais {len(ecarts)} reglage(s) ne "
                    f"sont pas appliques tels qu'ecrits. Une variable "
                    f"ORTHANC__* du compose les ecrase peut-etre."
                ),
            }
        return {
            "ok": True,
            "message": "Orthanc a redemarre, la configuration est appliquee.",
            "version": version,
        }

    # Orthanc ne revient pas. Le plus probable est que la configuration qu'on
    # vient d'ecrire l'empeche de demarrer : une valeur peut etre du bon type,
    # produire un JSON parfaitement valide, et rester inacceptable pour lui --
    # un numero de port hors bornes, par exemple. Laisser un PACS eteint en
    # renvoyant l'exploitant vers les journaux n'est pas une reponse : on
    # restaure la derniere sauvegarde et on relance.
    await _audit("orthanc.restart.no_response", admin.username,
                 container=ORTHANC_CONTAINER)

    sauvegarde = _latest_orthanc_backup()
    if sauvegarde is None:
        raise HTTPException(
            504,
            "Orthanc ne repond pas apres 60 s et aucune sauvegarde de sa "
            "configuration n'est disponible. Consulter ses journaux "
            "(docker compose logs orthanc).",
        )

    try:
        shutil.copy2(sauvegarde, ORTHANC_JSON)
        await _request_restart()
    except Exception as e:  # noqa: BLE001 - on est deja dans le pire des cas
        await _audit("orthanc.rollback.failed", admin.username,
                     backup=sauvegarde.name, error=str(e))
        raise HTTPException(
            500,
            f"Orthanc ne repond pas, et la restauration de {sauvegarde.name} "
            f"a echoue ({e}). Intervention manuelle requise.",
        ) from e

    version = await _wait_for_orthanc()
    if version:
        await _audit("orthanc.rolled_back", admin.username,
                     backup=sauvegarde.name)
        raise HTTPException(
            500,
            f"Orthanc n'a pas redemarre avec la nouvelle configuration : "
            f"elle a ete annulee et {sauvegarde.name} restauree. Le serveur "
            f"fonctionne a nouveau. Verifier les valeurs saisies, puis "
            f"consulter les journaux (docker compose logs orthanc).",
        )

    await _audit("orthanc.rollback.no_response", admin.username,
                 backup=sauvegarde.name)
    raise HTTPException(
        504,
        f"Orthanc ne repond toujours pas apres restauration de "
        f"{sauvegarde.name}. La cause est donc ailleurs que dans la derniere "
        f"modification : consulter ses journaux "
        f"(docker compose logs orthanc).",
    )


# ============================================================================
# Route : /api/admin/health (verifie Redis + Orthanc + fichiers config)
# ============================================================================

@router.get("/api/admin/config-effective")
async def config_effective(admin: AdminUser = Depends(require_admin)):
    """Ecarts entre la configuration ecrite et celle qu'Orthanc applique.

    Utile hors redemarrage : un ecart persistant signale une variable
    d'environnement qui prend le pas sur le fichier, ou un champ place au
    mauvais endroit de l'arborescence.
    """
    ecarts = await _check_effective_config()
    return {"ok": not ecarts, "ecarts": ecarts, "verifies": len(ORTHANC_VERIFIABLE)}


@router.get("/api/admin/health")
async def admin_health(admin: AdminUser = Depends(require_admin)):
    """
    Diagnostic pour l'onglet Health : etat des dependances de auth-service.

    Retourne 200 avec un dict par composant ({ok: bool, detail: str}), meme
    si certains composants sont KO — c'est le job de l'UI de decider quoi
    montrer. On evite 503 global qui masquerait quel composant est en cause.
    """
    checks = {}

    # Redis
    try:
        pong = await _r().ping()
        checks["redis"] = {"ok": bool(pong), "detail": "PONG"}
    except RedisError as e:
        checks["redis"] = {"ok": False, "detail": f"RedisError: {e}"}

    # Fichiers config lisibles + parseables
    try:
        _load_authelia()
        checks["authelia_yml"] = {"ok": True, "detail": str(AUTHELIA_YML)}
    except FileNotFoundError:
        checks["authelia_yml"] = {"ok": False, "detail": "fichier absent"}
    except (yaml.YAMLError, OSError) as e:
        checks["authelia_yml"] = {"ok": False, "detail": f"parse error: {e}"}

    try:
        if ORTHANC_JSON.exists():
            # Meme tolerance aux commentaires que _load_orthanc_config, sinon
            # le health check signale a tort une config corrompue.
            json.loads(_strip_json_comments(ORTHANC_JSON.read_text(encoding="utf-8")))
            checks["orthanc_json"] = {"ok": True, "detail": str(ORTHANC_JSON)}
        else:
            checks["orthanc_json"] = {"ok": False, "detail": "fichier absent"}
    except (json.JSONDecodeError, OSError) as e:
        checks["orthanc_json"] = {"ok": False, "detail": f"parse error: {e}"}

    # Orthanc API accessible (endpoint /system, moins invasif que /tools/reset)
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{ORTHANC_URL}/system", auth=(ORTHANC_USER, ORTHANC_PASS))
            checks["orthanc_api"] = {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        checks["orthanc_api"] = {"ok": False, "detail": f"HTTPError: {e}"}

    return {"checks": checks}


# ============================================================================
# Route : rollback backup
# ============================================================================

@router.get("/api/admin/modalities")
async def list_modalities(admin: AdminUser = Depends(require_admin)):
    """Equipements DICOM declares, avec leur configuration.

    Orthanc ne renvoie que les noms ; la configuration de chacun demande un
    appel supplementaire. On les rassemble ici pour que l'affichage n'ait pas
    a enchainer les requetes.
    """
    r = await _orthanc("GET", "/modalities")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"Orthanc: {r.text[:200]}")

    equipements = []
    for nom in r.json():
        detail = await _orthanc("GET", f"/modalities/{nom}/configuration")
        cfg = detail.json() if detail.status_code == 200 else {}
        equipements.append({
            "name": nom,
            "aet": cfg.get("AET", ""),
            "host": cfg.get("Host", ""),
            "port": cfg.get("Port", 0),
        })
    equipements.sort(key=lambda e: e["name"].lower())
    return {"modalities": equipements}


@router.put("/api/admin/modalities/{name}")
async def upsert_modality(
    name: str,
    payload: ModalityPayload,
    admin: AdminUser = Depends(require_admin),
):
    """Declare ou met a jour un equipement.

    DicomModalitiesInDatabase etant actif, la declaration est enregistree en
    base et prend effet immediatement : ni redemarrage, ni reecriture de
    orthanc.json.
    """
    if "/" in name or not name.strip():
        raise HTTPException(400, _msg("err_invalid_name", "nom invalide"))

    r = await _orthanc(
        "PUT", f"/modalities/{name}",
        json={"AET": payload.aet, "Host": payload.host, "Port": payload.port},
    )
    if r.status_code not in (200, 201):
        raise HTTPException(r.status_code, f"Orthanc: {r.text[:200]}")

    await _audit(
        "orthanc.modality.saved", admin.username,
        target=name, aet=payload.aet, host=payload.host, port=payload.port,
    )
    return {"ok": True}


@router.delete("/api/admin/modalities/{name}")
async def delete_modality(name: str, admin: AdminUser = Depends(require_admin)):
    r = await _orthanc("DELETE", f"/modalities/{name}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"Orthanc: {r.text[:200]}")
    await _audit("orthanc.modality.deleted", admin.username, target=name)
    return {"ok": True}


@router.post("/api/admin/modalities/{name}/echo")
async def echo_modality(name: str, admin: AdminUser = Depends(require_admin)):
    """Test de connectivite (C-ECHO).

    Declarer un equipement ne dit pas s'il repond. Cet appel evite d'avoir a
    diagnostiquer plus tard un transfert qui echoue faute d'adresse ou de port
    corrects.
    """
    r = await _orthanc("POST", f"/modalities/{name}/echo", json={})
    joignable = r.status_code == 200
    await _audit(
        "orthanc.modality.echo", admin.username,
        target=name, result="ok" if joignable else "echec",
    )
    return {
        "reachable": joignable,
        "detail": "" if joignable else r.text[:200],
    }


@router.get("/api/admin/audit")
async def read_audit(
    limit: int = 100,
    admin: AdminUser = Depends(require_admin),
):
    """Journal d'audit, l'evenement le plus recent en premier.

    Le flux etait alimente depuis le debut mais rien ne le lisait : les
    creations de comptes, les changements de configuration et les tentatives
    CSRF rejetees s'accumulaient sans que personne puisse les consulter.
    """
    limit = max(1, min(limit, 500))
    try:
        brut = await _r().xrevrange(AUDIT_STREAM, count=limit)
    except Exception as e:  # noqa: BLE001 - Redis indisponible ne doit pas casser le panel
        raise HTTPException(503, _msg("err_audit_unreadable",
                                "journal illisible : {detail}", detail=e)) from e

    entrees = []
    for identifiant, champs in brut:
        # event, actor et ts sont systematiques ; le reste depend du type
        # d'evenement (cible, champs modifies, sauvegarde concernee...) et est
        # regroupe pour que l'affichage n'ait pas a les connaitre.
        details = {
            k: v for k, v in champs.items()
            if k not in ("event", "actor", "ts")
        }
        entrees.append({
            "id": identifiant,
            "event": champs.get("event", "?"),
            "actor": champs.get("actor", "?"),
            "ts": int(champs.get("ts", 0) or 0),
            "details": details,
        })

    return {"entries": entrees, "count": len(entrees)}


@router.get("/api/admin/backups")
async def list_backups(admin: AdminUser = Depends(require_admin)):
    """Sauvegardes disponibles, la plus recente en premier.

    Sans cette route, la restauration existait sans moyen de savoir quoi
    restaurer : le nom exact du fichier devait etre devine.
    """
    if not BACKUPS_DIR.exists():
        return {"backups": []}

    connus = {
        "orthanc.json.bak.": "orthanc",
        "users_database.yml.bak.": "authelia",
        ".env.bak.": "env",
        "configuration.yml.bak.": "authelia-config",
    }

    items = []
    for f in BACKUPS_DIR.iterdir():
        if not f.is_file() or ".bak." not in f.name:
            continue
        cible = next((v for k, v in connus.items() if f.name.startswith(k)), None)
        if cible is None:
            # Fichier non restaurable par la route de restauration : l'exposer
            # laisserait croire le contraire.
            continue
        st = f.stat()
        items.append({
            "name": f.name,
            "target": cible,
            "size": st.st_size,
            "modified": int(st.st_mtime),
        })

    items.sort(key=lambda i: i["modified"], reverse=True)
    return {"backups": items}


@router.post("/api/admin/backups")
async def create_backup(admin: AdminUser = Depends(require_admin)):
    """Sauvegarde volontaire des fichiers de configuration.

    Jusqu'ici les copies n'etaient creees qu'en reaction a une ecriture du
    panel : impossible de prendre un point de reprise avant une manipulation
    risquee -- une montee de version, une edition manuelle d'un fichier --
    alors que c'est precisement le moment ou on en veut un.
    """
    fichiers = [
        (AUTHELIA_YML, "comptes"),
        (ORTHANC_JSON, "configuration Orthanc"),
        (ENV_FILE, "variables d'environnement"),
        (AUTHELIA_CONFIG, "configuration Authelia"),
    ]

    faits, ignores = [], []
    for chemin, libelle in fichiers:
        if chemin and chemin.exists():
            try:
                dest = _backup(chemin, tag="manuel")
                faits.append(dest.name)
            except OSError as e:  # disque plein, droits insuffisants
                ignores.append(f"{libelle} : {e}")
        else:
            ignores.append(f"{libelle} : fichier absent")

    if not faits:
        raise HTTPException(500, "aucun fichier n'a pu etre sauvegarde : " + " ; ".join(ignores))

    await _audit("backup.created", admin.username, files=",".join(faits))
    return {"ok": True, "created": faits, "skipped": ignores}


@router.post("/api/admin/backups/restore")
async def restore_backup(
    backup_name: str,
    admin: AdminUser = Depends(require_admin),
):
    """Restaure un backup depuis /host/backups/ vers son fichier d'origine."""
    # Le nom vient du client : il ne doit designer qu'un fichier du dossier de
    # backups. Un nom comme "orthanc.json.bak.../../../etc/passwd" satisfait
    # les controles de forme plus bas tout en pointant hors du dossier, d'ou
    # cette verification sur le chemin resolu.
    if "/" in backup_name or "\\" in backup_name or ".." in backup_name:
        raise HTTPException(400, _msg("err_invalid_backup_name", "nom de backup invalide"))

    src = (BACKUPS_DIR / backup_name).resolve()
    if not src.is_relative_to(BACKUPS_DIR.resolve()):
        raise HTTPException(400, _msg("err_invalid_backup_name", "nom de backup invalide"))

    if not src.exists() or ".bak." not in backup_name:
        raise HTTPException(404, _msg("err_backup_not_found",
                                "backup introuvable ou nom invalide"))

    if backup_name.startswith("orthanc.json.bak."):
        dest = ORTHANC_JSON
        reload = _reload_orthanc
    elif backup_name.startswith("users_database.yml.bak."):
        dest = AUTHELIA_YML
        reload = None  # Authelia relit son fichier tout seul
    elif backup_name.startswith(".env.bak."):
        # Restauree telle quelle : les variables ne sont relues qu'a la
        # recreation des conteneurs, ce que l'interface signale.
        dest = ENV_FILE
        reload = None
    elif backup_name.startswith("configuration.yml.bak."):
        dest = AUTHELIA_CONFIG
        reload = None  # Authelia surveille aussi ce fichier
    else:
        raise HTTPException(400, _msg("err_backup_type", "type de backup non gere"))

    _backup(dest, tag="pre-restore")
    shutil.copy2(src, dest)
    if reload:
        await reload()

    await _audit("backup.restored", admin.username, backup=backup_name)
    return {"ok": True}
