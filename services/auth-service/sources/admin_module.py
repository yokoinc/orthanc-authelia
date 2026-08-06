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
import asyncio
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

# configuration.yml vit dans le meme dossier que users_database.yml, deja
# bind-monte : rien de plus a monter pour le lire ou l'ecrire.
AUTHELIA_CONFIG = Path(
    os.getenv("ADMIN_AUTHELIA_CONFIG_PATH", "/host/authelia-config/configuration.yml")
)
# Le .env, lui, vit a la racine du projet. Il est monte en tant que FICHIER,
# pas via son dossier : monter la racine donnerait au container un acces en
# ecriture au docker-compose.yml et aux scripts. Consequence, les ecritures
# se font sur place (cf. _write_env_var).
ENV_FILE = Path(os.getenv("ADMIN_ENV_PATH", "/host/env/.env"))

SETUP_KEY = "orthanc_authelia:setup_completed"
SETUP_FIRST_ADMIN_KEY = "orthanc_authelia:setup_first_admin_created"

# Compte present dans users_database.yml.example au seul titre qu'Authelia
# refuse de demarrer sur une base vide ("users: non zero value required").
# Desactive et sans groupe, il est supprime a la finalisation du wizard.
BOOTSTRAP_USERNAME = "bootstrap@localhost"
AUDIT_STREAM = "admin:audit"
CSRF_COOKIE = "orthanc_admin_csrf"

IMAGE_VERSION = os.getenv("IMAGE_VERSION", "dev")

# Redemarrage d'Orthanc. Le socket Docker n'est pas monte ici : on passe par un
# proxy qui n'autorise que le redemarrage d'un conteneur (voir socket-proxy
# dans docker-compose). Vide = la fonction est indisponible et le panel le dit,
# plutot que d'echouer a l'usage.
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "").rstrip("/")
ORTHANC_CONTAINER = os.getenv("ORTHANC_CONTAINER", "orthanc-server")

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
# Helpers : URL publique
# ============================================================================

def _normalise_public_url(raw: str) -> tuple[str, str]:
    """Valide l'URL publique. Renvoie (origine, hote sans port).

    L'origine sert aux redirections (port compris), l'hote au domaine des
    cookies de session -- un cookie ne porte jamais de port.
    """
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https":
        raise HTTPException(400, "l'URL publique doit commencer par https://")
    if not parsed.hostname:
        raise HTTPException(400, "hote manquant dans l'URL publique")
    if parsed.path.strip("/"):
        raise HTTPException(
            400,
            "indiquer l'origine seule, sans chemin "
            "(exemple : https://pacs.exemple.fr)",
        )
    # RFC 6265 : un cookie pose sur un hote sans point n'est pas conservé par
    # certains navigateurs. "localhost" fait exception, pas "monpacs".
    if "." not in parsed.hostname and parsed.hostname != "localhost":
        raise HTTPException(
            400,
            f"'{parsed.hostname}' n'a pas de point : les navigateurs "
            "refuseront le cookie de session. Utiliser un nom complet "
            "(pacs.exemple.fr) ou pacs.localhost.",
        )
    return f"https://{parsed.netloc}", parsed.hostname


def _read_env_var(name: str) -> str:
    """Lit une variable du .env. Chaine vide si absente ou fichier illisible."""
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _write_env_var(name: str, value: str) -> None:
    """Remplace (ou ajoute) name=value dans le .env, en ecrivant SUR PLACE.

    Pas de write-tmp + rename ici, contrairement au reste du module : le .env
    est un bind-mount de fichier. Le rename echouerait (EBUSY) et, s'il
    aboutissait, docker compose relirait l'ancien inode. On reecrit donc le
    meme fichier, apres sauvegarde -- une ecriture interrompue laisserait
    sinon un .env tronque, et la pile ne redemarrerait plus.
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
    remplace = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            lines[i] = f"{name}={value}"
            remplace = True
            break
    if not remplace:
        lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _retarget_authelia_config(ancienne_origine: str, ancien_hote: str,
                              origine: str, hote: str) -> int:
    """Repointe configuration.yml vers la nouvelle URL publique.

    Remplacement textuel, et non chargement/re-serialisation YAML : le
    fichier est abondamment commente (regles d'acces, avertissements sur le
    port des cookies) et un aller-retour via PyYAML effacerait tout.

    Renvoie le nombre de substitutions effectuees.
    """
    if not AUTHELIA_CONFIG.exists():
        raise HTTPException(503, "configuration.yml d'Authelia introuvable")
    texte = AUTHELIA_CONFIG.read_text(encoding="utf-8")
    total = texte.count(ancienne_origine) + texte.count(ancien_hote)
    if not total:
        raise HTTPException(
            500,
            f"aucune trace de '{ancien_hote}' dans configuration.yml : "
            "le fichier a ete modifie a la main, changement annule",
        )
    _backup(AUTHELIA_CONFIG, tag="network")
    # L'origine complete d'abord : sinon le remplacement de l'hote seul
    # casserait "https://ancien:30443" en "https://nouveau:30443" avec un
    # port qui n'a plus lieu d'etre.
    texte = texte.replace(ancienne_origine, origine).replace(ancien_hote, hote)
    _atomic_write(AUTHELIA_CONFIG, texte)
    return total


async def _apply_public_url(nouvelle: str, acteur: str) -> dict:
    """Applique une nouvelle URL publique au .env et a la config Authelia."""
    origine, hote = _normalise_public_url(nouvelle)
    ancienne_origine = _read_env_var("PUBLIC_URL").rstrip("/")
    if not ancienne_origine:
        raise HTTPException(500, "PUBLIC_URL absente du .env, changement annule")
    if ancienne_origine == origine:
        return {"ok": True, "unchanged": True, "public_url": origine}

    _, ancien_hote = _normalise_public_url(ancienne_origine)
    substitutions = _retarget_authelia_config(
        ancienne_origine, ancien_hote, origine, hote,
    )
    _write_env_var("PUBLIC_URL", origine)
    await _audit(
        "network.public_url.changed", actor=acteur,
        old=ancienne_origine, new=origine, substitutions=substitutions,
    )
    return {
        "ok": True,
        "unchanged": False,
        "public_url": origine,
        "substitutions": substitutions,
        "restart_required": True,
        "message": (
            f"URL publique enregistree : {origine}. Relancer la pile "
            "(docker compose up -d) pour l'appliquer, puis se reconnecter "
            f"sur {origine} — la session en cours est liee a l'ancien domaine."
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
    Depends injecte dans les routes /api/admin/*. Utilise les headers propages
    par nginx auth_request (Authelia met Remote-User + Remote-Groups apres
    verification de la session).
    """
    username = request.headers.get("remote-user", "")
    groups_raw = request.headers.get("remote-groups", "")
    if not username:
        raise HTTPException(401, "auth requise")
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    if "admin" not in groups:
        raise HTTPException(403, "groupe admin requis")
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
        raise HTTPException(423, "fichier verrouille par un autre admin, retry dans 5s") from e
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
    if "admin" not in payload.groups:
        payload.groups.append("admin")
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
    """Etape finale : verifie l'invariant admin actif, supprime le compte
    d'amorcage, puis marque le setup comme termine."""
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, "setup deja finalise")
    data = _load_authelia()
    admins = [
        u for u, i in data.get("users", {}).items()
        if not i.get("disabled") and "admin" in (i.get("groups") or [])
    ]
    if not admins:
        raise HTTPException(400, "creer d'abord un admin (POST /auth/setup/create-admin)")

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
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(
            409, "setup deja finalise, utiliser /api/admin/network",
        )
    return await _apply_public_url(payload.public_url, acteur="wizard")


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
    return await _apply_public_url(payload.public_url, acteur=admin.username)


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
        raise HTTPException(404, "user inconnu")

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
        raise HTTPException(400, "aucun champ a modifier")

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
        meta[dotted] = {
            "type": noms.get(attendu, "str"),
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
    return {"ok": True, "backup": backup.name}


# ============================================================================
# Route : /api/admin/orthanc/restart
# ============================================================================

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
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                f"{DOCKER_PROXY_URL}/containers/{ORTHANC_CONTAINER}/restart",
            )
    except httpx.HTTPError as e:
        await _audit("orthanc.restart.failed", admin.username, error=str(e))
        raise HTTPException(
            502, f"Proxy Docker injoignable : {e}",
        ) from e

    if r.status_code == 404:
        await _audit("orthanc.restart.failed", admin.username,
                     error="conteneur introuvable")
        raise HTTPException(
            502,
            f"Conteneur '{ORTHANC_CONTAINER}' introuvable. Verifier "
            f"ORTHANC_CONTAINER dans le fichier .env.",
        )
    if r.status_code not in (204, 304):
        await _audit("orthanc.restart.failed", admin.username,
                     error=f"HTTP {r.status_code}")
        raise HTTPException(
            502,
            f"Le proxy Docker a refuse le redemarrage (HTTP {r.status_code}). "
            f"Verifier ALLOW_RESTARTS sur le service socket-proxy.",
        )

    # Orthanc reouvre son port avant d'avoir fini de charger ses plugins : on
    # interroge /system, qui ne repond qu'une fois le serveur reellement pret.
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            sonde = await _orthanc("GET", "/system")
            if sonde.status_code == 200:
                await _audit("orthanc.restarted", admin.username,
                             container=ORTHANC_CONTAINER)
                return {
                    "ok": True,
                    "message": "Orthanc a redemarre, la configuration est appliquee.",
                    "version": sonde.json().get("Version", ""),
                }
        except Exception:  # noqa: BLE001 - normal pendant le redemarrage
            pass

    await _audit("orthanc.restart.no_response", admin.username,
                 container=ORTHANC_CONTAINER)
    raise HTTPException(
        504,
        "Orthanc a ete redemarre mais ne repond pas apres 60 s. Sa "
        "configuration l'empeche peut-etre de demarrer : consulter ses "
        "journaux (docker compose logs orthanc) et, au besoin, restaurer une "
        "sauvegarde depuis l'onglet Maintenance.",
    )


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
        raise HTTPException(400, "nom invalide")

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
        raise HTTPException(503, f"journal illisible : {e}") from e

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
        raise HTTPException(400, "type de backup non gere")

    _backup(dest, tag="pre-restore")
    shutil.copy2(src, dest)
    if reload:
        await reload()

    await _audit("backup.restored", admin.username, backup=backup_name)
    return {"ok": True}
