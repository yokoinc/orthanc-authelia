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
import re
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
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from filelock import FileLock, Timeout
from pydantic import BaseModel, EmailStr, Field
from redis.exceptions import RedisError


# ============================================================================
# Config + globals
# ============================================================================

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")
ORTHANC_USER = os.environ["ORTHANC_ADMIN_USER"]
ORTHANC_PASS = os.environ["ORTHANC_ADMIN_PASS"]

AUTHELIA_YML = Path(os.getenv("ADMIN_AUTHELIA_PATH", "/host/authelia.yml"))
ORTHANC_JSON = Path(os.getenv("ADMIN_ORTHANC_PATH", "/host/orthanc.json"))
BACKUPS_DIR = Path(os.getenv("ADMIN_BACKUPS_DIR", "/host/backups"))

SETUP_KEY = "orthanc_authelia:setup_completed"
SETUP_FIRST_ADMIN_KEY = "orthanc_authelia:setup_first_admin_created"
AUDIT_STREAM = "admin:audit"
CSRF_COOKIE = "orthanc_admin_csrf"

TEMPLATES_DIR = Path(os.getenv("ADMIN_TEMPLATES_DIR", "/app/templates"))
ASSET_VERSION = os.getenv("ASSET_VERSION", str(int(time.time())))
IMAGE_VERSION = os.getenv("IMAGE_VERSION", "dev")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _render(template_name: str, **kwargs) -> str:
    """
    Rendu minimal {placeholder} → valeur, meme convention que auth_service.py.
    Les placeholders inconnus sont laisses tels quels (utile pour du JS avec {}).
    """
    kwargs.setdefault("asset_version", ASSET_VERSION)
    kwargs.setdefault("image_version", IMAGE_VERSION)
    content = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    return _PLACEHOLDER_RE.sub(
        lambda m: str(kwargs[m.group(1)]) if m.group(1) in kwargs else m.group(0),
        content,
    )

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
    - /auth/setup/* accessible seulement si setup_completed absent
    - /auth/admin/* accessible seulement si setup_completed present (+ auth admin)
    - autres chemins : bypass
    """
    path = request.url.path
    if not (path.startswith("/auth/setup") or path.startswith("/auth/admin")):
        return await call_next(request)

    done = (await _r().get(SETUP_KEY)) == "1"
    is_setup = path.startswith("/auth/setup")

    if is_setup and done:
        return RedirectResponse("/auth/admin", status_code=302)
    if not is_setup and not done:
        return RedirectResponse("/auth/setup", status_code=302)
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


def _load_orthanc_config() -> dict:
    """Idem pour orthanc.json — meme strategie d'erreur explicite."""
    try:
        raw = ORTHANC_JSON.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"orthanc.json illisible : {e}") from e
    try:
        return json.loads(raw)
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
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{ORTHANC_URL}/tools/reset",
            auth=(ORTHANC_USER, ORTHANC_PASS),
        )
        r.raise_for_status()


class OrthancConfigPayload(BaseModel):
    """Body de PATCH : {"changes": {"Name": "Foo", "DicomAet": "BAR"}}"""
    changes: dict[str, Any]


# ============================================================================
# CF Access : verify (auth_request) + rotate + test
# ============================================================================

CF_ID_KEY = "cf_access:client_id"
CF_SECRET_KEY = "cf_access:secret"
CF_HISTORY_KEY = "cf_access:history"


class CFRotatePayload(BaseModel):
    client_id: str = Field(..., min_length=10, max_length=200)
    client_secret: str = Field(..., min_length=32, max_length=200)


# ============================================================================
# Routes : setup wizard (unauthenticated)
# ============================================================================

router = APIRouter()


@router.get("/auth/setup", response_class=HTMLResponse)
async def setup_page():
    """Wizard HTML. setup_gate bloque si deja finalise."""
    return HTMLResponse(_render("setup.html"))


@router.get("/auth/admin", response_class=HTMLResponse)
async def admin_page(response: Response, admin: AdminUser = Depends(require_admin)):
    """Hub admin HTML. Pose le cookie CSRF au meme moment."""
    csrf = pysecrets.token_urlsafe(32)
    html = _render("admin.html", admin_username=admin.username)
    resp = HTMLResponse(html)
    resp.set_cookie(
        CSRF_COOKIE, csrf,
        secure=True, httponly=False, samesite="strict", max_age=3600,
    )
    return resp


@router.post("/auth/setup/create-admin")
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


@router.post("/auth/setup/finalize")
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
        # Auto-rollback : restaurer le backup et retenter le reset
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
# Routes : /api/admin/cf-access
# ============================================================================

@router.get("/api/admin/cf-access")
async def cf_status(admin: AdminUser = Depends(require_admin)):
    cid = await _r().get(CF_ID_KEY) or ""
    secret_exists = bool(await _r().get(CF_SECRET_KEY))
    history_len = await _r().llen(CF_HISTORY_KEY)
    return {
        "client_id_masked": (cid[:8] + "…" + cid[-6:]) if len(cid) > 20 else cid,
        "secret_configured": secret_exists,
        "history_length": history_len,
    }


@router.post("/api/admin/cf-access/rotate")
async def cf_rotate(
    payload: CFRotatePayload,
    admin: AdminUser = Depends(require_admin),
):
    """Rotation atomique : snapshot old vers history, set new, audit."""
    old_id = await _r().get(CF_ID_KEY) or ""
    old_secret = await _r().get(CF_SECRET_KEY) or ""
    if old_secret:
        entry = f"{int(time.time())}|{old_id}|{old_secret}"
        await _r().lpush(CF_HISTORY_KEY, entry)
        await _r().ltrim(CF_HISTORY_KEY, 0, 9)

    async with _r().pipeline(transaction=True) as pipe:
        pipe.set(CF_ID_KEY, payload.client_id)
        pipe.set(CF_SECRET_KEY, payload.client_secret)
        await pipe.execute()

    await _audit("cf_access.rotated", admin.username, id_prefix=payload.client_id[:8])
    return {"ok": True, "rotated_at": int(time.time())}


# ============================================================================
# Route interne : verify-cf (appelee par nginx auth_request)
# ============================================================================

@router.get("/api/internal/verify-cf", include_in_schema=False)
async def verify_cf(
    x_cf_client_id: str = Header(default=""),
    x_cf_client_secret: str = Header(default=""),
):
    """
    Compare les headers CF avec les valeurs stockees en Redis.

    Fail closed : si Redis est indisponible, on renvoie 403 (pas 500) pour que
    nginx bloque l'upload. Mieux vaut refuser un upload legitime pendant une
    panne Redis que laisser passer un secret pendant un blackout.
    """
    try:
        expected_id = await _r().get(CF_ID_KEY)
        expected_secret = await _r().get(CF_SECRET_KEY)
    except RedisError:
        return Response(status_code=403)  # fail closed

    if not expected_id or not expected_secret:
        return Response(status_code=503)  # pas configure

    if not pysecrets.compare_digest(x_cf_client_id, expected_id):
        return Response(status_code=403)
    if not pysecrets.compare_digest(x_cf_client_secret, expected_secret):
        return Response(status_code=403)

    # Metrique compte-tour — echec silencieux si Redis flaky ici, pas critique
    try:
        await _r().incr("cf_access:checks_ok:24h")
    except RedisError:
        pass
    return Response(status_code=204)


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
            json.loads(ORTHANC_JSON.read_text(encoding="utf-8"))
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
    src = BACKUPS_DIR / backup_name
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
