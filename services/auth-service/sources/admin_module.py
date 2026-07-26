"""
Module admin/setup pour auth-service (FastAPI).

A monter dans le auth_service.py principal via :
    from admin_module import router as admin_router, setup_gate
    app.include_router(admin_router)
    app.middleware("http")(setup_gate)

Depends : fastapi, redis.asyncio, pyyaml, argon2-cffi, httpx, filelock, pydantic
Prerequis env vars : ORTHANC_ADMIN_USER, ORTHANC_ADMIN_PASS, ORTHANC_URL, REDIS_URL
"""

import json
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
from filelock import FileLock, Timeout
from pydantic import BaseModel, EmailStr, Field
from redis.exceptions import RedisError


# ============================================================================
# Config + globals
# ============================================================================

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")
# Ces creds ne sont VRAIMENT necessaires que pour les endpoints qui parlent
# a Orthanc (reload, health check). Lecture non-stricte pour eviter un crash
# a l'import si le container demarre sans que le compose n'ait ete mis a jour.
# Les endpoints qui les utilisent verifient et retournent 503 si vides.
ORTHANC_USER = os.environ.get("ORTHANC_ADMIN_USER", "")
ORTHANC_PASS = os.environ.get("ORTHANC_ADMIN_PASS", "")


def _require_orthanc_creds():
    if not ORTHANC_USER or not ORTHANC_PASS:
        raise HTTPException(
            503,
            "ORTHANC_ADMIN_USER/ORTHANC_ADMIN_PASS non configures dans .env — "
            "l'endpoint est disponible mais ne peut pas appeler Orthanc",
        )

# Les dossiers parents sont bind-mountes (pas les fichiers) : les ecritures
# atomiques font un rename, impossible sur un mount de fichier (EBUSY), et
# le nouvel inode ne serait pas vu par les autres containers.
AUTHELIA_YML = Path(
    os.getenv("ADMIN_AUTHELIA_PATH", "/host/authelia-config/users_database.yml")
)
ORTHANC_JSON = Path(
    os.getenv("ADMIN_ORTHANC_PATH", "/host/orthanc-config/orthanc.json")
)
BACKUPS_DIR = Path(os.getenv("ADMIN_BACKUPS_DIR", "/host/backups"))

SETUP_KEY = "orthanc_authelia:setup_completed"
SETUP_FIRST_ADMIN_KEY = "orthanc_authelia:setup_first_admin_created"
AUDIT_STREAM = "admin:audit"
CSRF_COOKIE = "orthanc_admin_csrf"

IMAGE_VERSION = os.getenv("IMAGE_VERSION", "dev")

# argon2id parametres = defaults Authelia (compatibles avec ce qu'il verifie)
_hasher = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, salt_len=16,
)

# Client Redis global (a injecter depuis auth_service.py)
_redis: aioredis.Redis | None = None


def set_redis(client: aioredis.Redis) -> None:
    """Appelé au startup de auth_service.py pour injecter la connexion Redis."""
    global _redis
    _redis = client


def _r() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis pas initialise. Appeler set_redis() au startup.")
    return _redis


# ============================================================================
# Helpers : backups + audit + atomic write
# ============================================================================

def _backup(path: Path, tag: str = "") -> Path:
    """Copie path vers backups/{name}.bak.{ts}[.tag], rotation 10 derniers."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f".bak.{ts}" + (f".{tag}" if tag else "")
    dest = BACKUPS_DIR / (path.name + suffix)
    shutil.copy2(path, dest)
    # Rotation : garder les 10 derniers backups de ce fichier
    prefix = path.name + ".bak."
    backups = sorted(BACKUPS_DIR.glob(prefix + "*"), reverse=True)
    for old in backups[10:]:
        old.unlink(missing_ok=True)
    return dest


def _atomic_write(path: Path, content: str) -> None:
    """Ecrit content dans path via un fichier temporaire + rename atomique."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


async def _audit(event: str, actor: str, **fields: Any) -> None:
    """Ajoute une entree au stream Redis admin:audit."""
    entry = {"event": event, "actor": actor, "ts": str(int(time.time()))}
    for k, v in fields.items():
        entry[k] = str(v)
    await _r().xadd(AUDIT_STREAM, entry, maxlen=10000)


# ============================================================================
# Authentification admin (dependance FastAPI)
# ============================================================================

class AdminUser(BaseModel):
    username: str
    groups: list[str]


async def require_admin(request: Request) -> AdminUser:
    """
    Depends injecte dans les routes /api/admin/*. Utilise les headers propages
    par nginx auth_request (Authelia met Remote-User + Remote-Groups apres
    verification de la session).
    """
    username = request.headers.get("remote-user", "")
    groups_raw = request.headers.get("remote-groups", "")
    if not username:
        raise HTTPException(401, "auth requise")
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    if "admins" not in groups:
        raise HTTPException(403, "groupe admins requis")
    return AdminUser(username=username, groups=groups)


# ============================================================================
# Middleware : setup state machine
# ============================================================================

async def setup_gate(request: Request, call_next):
    """
    Aiguille entre le wizard et le hub selon le flag Redis setup_completed.

    Nginx expose la console sous /console/ et proxifie vers /ui/... cote
    auth-service, donc les chemins vus ici sont /ui/setup et /ui/ (le hub).
    Les redirections, elles, pointent sur les URLs telles que le navigateur
    les voit, prefixe /console/ inclus.
    """
    path = request.url.path
    if not path.startswith("/ui"):
        return await call_next(request)

    # Les assets ne sont pas des pages : les rediriger casserait le chargement
    # du SPA sur la page de setup.
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
        raise HTTPException(500, f"authelia yml illisible : {e}") from e
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
        raise HTTPException(500, f"orthanc.json illisible : {e}") from e
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
        if not info.get("disabled") and "admins" in (info.get("groups") or [])
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
        raise HTTPException(423, "fichier verrouille par un autre admin, retry dans 5s") from e


class UserCreatePayload(BaseModel):
    username: str = Field(..., pattern=r"^[a-zA-Z0-9._-]{3,32}$")
    displayname: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=12)
    groups: list[str] = Field(default_factory=lambda: ["doctors"])


class PasswordChangePayload(BaseModel):
    new_password: str = Field(..., min_length=12)


# ============================================================================
# Orthanc config : validation + edit + reload
# ============================================================================

# Whitelist des chemins editables via UI. Refuse tout ce qui n'est pas ici.
ORTHANC_EDITABLE_PATHS = {
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
}


def _apply_scalar_change(config: dict, dotted: str, value: Any) -> None:
    """Set config[a][b][c] = value. Refuse si le path ecrase un dict/array."""
    if dotted not in ORTHANC_EDITABLE_PATHS:
        raise ValueError(f"{dotted}: non editable via UI")
    expected_type = ORTHANC_EDITABLE_PATHS[dotted]
    if not isinstance(value, expected_type):
        raise ValueError(f"{dotted}: attendu {expected_type.__name__}, recu {type(value).__name__}")
    if dotted == "DicomAet" and len(value) > 16:
        raise ValueError("DicomAet: max 16 caracteres (norme DICOM)")

    keys = dotted.split(".")
    node = config
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


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
    resp = JSONResponse(
        {"username": admin.username, "image_version": IMAGE_VERSION},
    )
    resp.set_cookie(
        CSRF_COOKIE, csrf,
        secure=True, httponly=False, samesite="strict", max_age=3600,
    )
    return resp


@router.post("/setup/create-admin")
async def setup_create_admin(payload: UserCreatePayload):
    """
    Etape 1 : cree LE premier admin. Un seul appel autorise jusqu'a finalize.

    Verrouille apres le 1er succes via SETUP_FIRST_ADMIN_KEY pour empecher un
    tiers de creer un deuxieme admin en profitant de la fenetre ouverte du wizard.
    Pour ajouter d'autres admins ensuite : POST /api/admin/users (auth requise).
    """
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, "setup deja finalise, utiliser /api/admin/users")
    if (await _r().get(SETUP_FIRST_ADMIN_KEY)) == "1":
        raise HTTPException(
            409,
            "un admin a deja ete cree — finaliser le setup (POST /auth/setup/finalize) "
            "puis utiliser /api/admin/users pour en ajouter d'autres",
        )
    if "admins" not in payload.groups:
        payload.groups.append("admins")
    data = _load_authelia()
    if payload.username in data.get("users", {}):
        raise HTTPException(409, f"user {payload.username} existe deja")
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
    """Etape finale : verifie invariant admin actif puis flip le flag."""
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, "setup deja finalise")
    data = _load_authelia()
    admins = [
        u for u, i in data.get("users", {}).items()
        if not i.get("disabled") and "admins" in (i.get("groups") or [])
    ]
    if not admins:
        raise HTTPException(400, "creer d'abord un admin (POST /auth/setup/create-admin)")
    await _r().set(SETUP_KEY, "1")
    await _r().delete(SETUP_FIRST_ADMIN_KEY)  # verrou setup levee, ne sert plus
    await _audit("setup.finalized", actor="wizard", admin_count=len(admins))
    return {"ok": True, "admins": admins}


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
        raise HTTPException(409, "user existe deja")
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


@router.patch("/api/admin/users/{username}/password")
async def change_password(
    username: str,
    payload: PasswordChangePayload,
    admin: AdminUser = Depends(require_admin),
):
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, "user inconnu")
    data["users"][username]["password"] = _hasher.hash(payload.new_password)
    _write_authelia(data)
    await _audit("authelia.password.changed", admin.username, target=username)
    return {"ok": True}


@router.delete("/api/admin/users/{username}")
async def delete_user(username: str, admin: AdminUser = Depends(require_admin)):
    if username == admin.username:
        raise HTTPException(400, "impossible de te supprimer toi-meme")
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, "user inconnu")
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
    # Renvoie uniquement les valeurs editables (whitelist)
    result = {}
    for dotted in ORTHANC_EDITABLE_PATHS:
        node = config
        for k in dotted.split("."):
            if not isinstance(node, dict) or k not in node:
                node = None
                break
            node = node[k]
        result[dotted] = node
    return {"editable": result}


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
            serialized = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
            _atomic_write(ORTHANC_JSON, serialized)
    except Timeout as e:
        raise HTTPException(423, "orthanc.json verrouille, retry") from e
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
                "message": (
                    "Configuration enregistree. Orthanc ne peut pas la recharger "
                    "a chaud (le plugin Authorization refuse /tools/reset) : "
                    "redemarrer le conteneur pour l'appliquer — "
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
    return {"ok": True, "backup": backup.name}


# ============================================================================
# Route : /api/admin/health (verifie Redis + Orthanc + fichiers config)
# ============================================================================

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
        raise HTTPException(400, "nom de backup invalide")

    src = (BACKUPS_DIR / backup_name).resolve()
    if not src.is_relative_to(BACKUPS_DIR.resolve()):
        raise HTTPException(400, "nom de backup invalide")

    if not src.exists() or ".bak." not in backup_name:
        raise HTTPException(404, "backup introuvable ou nom invalide")

    if backup_name.startswith("orthanc.json.bak."):
        dest = ORTHANC_JSON
        reload = _reload_orthanc
    elif backup_name.startswith("users_database.yml.bak."):
        dest = AUTHELIA_YML
        reload = None  # Authelia watch
    else:
        raise HTTPException(400, "type de backup non gere")

    _backup(dest, tag="pre-restore")
    shutil.copy2(src, dest)
    if reload:
        await reload()

    await _audit("backup.restored", admin.username, backup=backup_name)
    return {"ok": True}
