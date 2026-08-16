"""
Admin/setup module for auth-service (FastAPI).

Mount it in the main auth_service.py with:
    from admin_module import router as admin_router, setup_gate
    app.include_router(admin_router)
    app.middleware("http")(setup_gate)

Depends: fastapi, redis.asyncio, pyyaml, argon2-cffi, httpx, filelock, pydantic
Required env vars: ORTHANC_ADMIN_USER, ORTHANC_ADMIN_PASS, ORTHANC_URL, REDIS_URL
"""

import copy
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
# These credentials are only REALLY needed by the endpoints that talk to
# Orthanc (reload, health check). Read leniently to avoid crashing at import
# time if the container starts before the compose file has been updated.
# The endpoints that use them check and return 503 when they are empty.
ORTHANC_USER = os.environ.get("ORTHANC_ADMIN_USER", "")
ORTHANC_PASS = os.environ.get("ORTHANC_ADMIN_PASS", "")

# Orthanc's Authorization plugin identifies the caller through a token carried
# by one of its TokenHttpHeaders (X-Auth-User, Remote-User, auth-token), then
# asks auth-service -- that is, us -- for the matching profile. Without that
# header our calls are seen as anonymous, a profile that only holds the
# "upload" permission: POST /tools/reset then answers 403 and every config
# change fails at reload time.
# The default value mirrors what nginx injects for the admin group
# (map $groups -> $auth_token in nginx.ssl.conf).
ORTHANC_AUTH_TOKEN = os.environ.get("ORTHANC_AUTH_TOKEN", "admin-token")
ORTHANC_AUTH_HEADERS = {"auth-token": ORTHANC_AUTH_TOKEN}


def _require_orthanc_creds():
    if not ORTHANC_USER or not ORTHANC_PASS:
        raise HTTPException(
            503,
            "ORTHANC_ADMIN_USER/ORTHANC_ADMIN_PASS not configured in .env — "
            "the endpoint is available but cannot call Orthanc",
        )

AUTHELIA_YML = Path(os.getenv("ADMIN_AUTHELIA_PATH", "/host/authelia.yml"))
ORTHANC_JSON = Path(os.getenv("ADMIN_ORTHANC_PATH", "/host/orthanc.json"))
BACKUPS_DIR = Path(os.getenv("ADMIN_BACKUPS_DIR", "/host/backups"))

# Name of the Authelia group that grants access to the panel. Configurable
# because the name is not standardised: this repo ships its examples with
# "admins", but an existing install may well use "admin" -- it then has to
# match users_database.yml, the `subject: "group:..."` of the Authelia
# configuration and nginx's $groups map, otherwise the whole panel answers
# 403.
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "admins")

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
    Minimal {placeholder} -> value rendering, same convention as auth_service.py.
    Unknown placeholders are left as-is (handy for JS using {}).
    """
    kwargs.setdefault("asset_version", ASSET_VERSION)
    kwargs.setdefault("image_version", IMAGE_VERSION)
    content = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    return _PLACEHOLDER_RE.sub(
        lambda m: str(kwargs[m.group(1)]) if m.group(1) in kwargs else m.group(0),
        content,
    )

# argon2id parameters = Authelia defaults (compatible with what it verifies)
_hasher = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, salt_len=16,
)

# Global Redis client (injected from auth_service.py)
_redis: aioredis.Redis | None = None


def set_redis(client: aioredis.Redis) -> None:
    """Called at auth_service.py startup to inject the Redis connection."""
    global _redis
    _redis = client


def _r() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call set_redis() at startup.")
    return _redis


# ============================================================================
# Helpers: backups + audit + atomic write
# ============================================================================

def _backup(path: Path, tag: str = "") -> Path:
    """Copy path to backups/{name}.bak.{ts}[.tag], keeping the last 10."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f".bak.{ts}" + (f".{tag}" if tag else "")
    dest = BACKUPS_DIR / (path.name + suffix)
    shutil.copy2(path, dest)
    # Rotation: keep the 10 most recent backups of this file
    prefix = path.name + ".bak."
    backups = sorted(BACKUPS_DIR.glob(prefix + "*"), reverse=True)
    for old in backups[10:]:
        old.unlink(missing_ok=True)
    return dest


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path while keeping the inode.

    Definitely no tmp.replace(path) here: these files are bind-mounted into
    the other containers, and Docker mounts by inode.
      - orthanc.json is mounted :ro on /etc/orthanc/orthanc.json on the
        orthanc side; a rename would create a new inode and orthanc would
        keep reading the old one, even after /tools/reset (silent failure);
      - if the target is the mount point itself, the rename plainly fails:
        OSError [Errno 16] Device or resource busy.

    So we write into the existing inode. Going through a temporary file is
    still worth it: it proves the content writes out in full before we touch
    the real file, which avoids leaving a truncated config behind if the disk
    is full.
    """
    data = content.encode("utf-8")

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())

        mode = "r+b" if path.exists() else "wb"
        with open(path, mode) as f:
            f.write(data)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
    finally:
        tmp.unlink(missing_ok=True)


async def _audit(event: str, actor: str, **fields: Any) -> None:
    """Append an entry to the admin:audit Redis stream."""
    entry = {"event": event, "actor": actor, "ts": str(int(time.time()))}
    for k, v in fields.items():
        entry[k] = str(v)
    await _r().xadd(AUDIT_STREAM, entry, maxlen=10000)


# ============================================================================
# Admin authentication (FastAPI dependency)
# ============================================================================

class AdminUser(BaseModel):
    username: str
    groups: list[str]


async def require_admin(request: Request) -> AdminUser:
    """
    Dependency injected into the /api/admin/* routes. Uses the headers relayed
    by nginx auth_request (Authelia sets Remote-User + Remote-Groups after it
    has verified the session).
    """
    username = request.headers.get("remote-user", "")
    groups_raw = request.headers.get("remote-groups", "")
    if not username:
        raise HTTPException(401, "authentication required")
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    if ADMIN_GROUP not in groups:
        raise HTTPException(403, f"group {ADMIN_GROUP} required")
    return AdminUser(username=username, groups=groups)


# ============================================================================
# Middleware : setup state machine
# ============================================================================

async def _setup_is_done() -> bool:
    """Has first-time setup already happened?

    The Redis flag is authoritative, but it is absent from any install predating
    the panel: the key did not exist. Without a catch-up, deploying this version
    onto a stack already in service reopens the first-run wizard -- which is
    deliberately outside SSO, since at the very first launch no account exists
    to authenticate with -- and thus lets anyone create an admin account.

    A users_database.yml that already holds an active admin therefore counts as
    setup done. We then freeze the flag so we do not re-read the file on every
    request.

    When the YAML is present but unreadable we close the wizard (fail-closed):
    a genuine first install has no file at all, whereas a broken file is an
    incident to repair through /api/admin/backups/restore, not a reason to
    reopen the door.
    """
    if (await _r().get(SETUP_KEY)) == "1":
        return True

    # Wizard in progress: the admin present in the YAML is the one create-admin
    # just wrote, not the trace of an earlier install. Without this guard the
    # wizard would close on its own pass and /auth/setup/finalize would become
    # unreachable.
    if await _r().get(SETUP_FIRST_ADMIN_KEY):
        return False

    if not AUTHELIA_YML.exists():
        return False

    try:
        admins = _active_admins(_load_authelia())
    except HTTPException:
        return True

    if not admins:
        return False

    await _r().set(SETUP_KEY, "1")
    await _audit("setup.adopted_existing", actor="system", admin_count=len(admins))
    return True


async def setup_gate(request: Request, call_next):
    """
    - /auth/setup/* reachable only while setup_completed is absent
    - /auth/admin/* reachable only once setup_completed is present (+ admin auth)
    - any other path: bypass
    """
    path = request.url.path
    if not (path.startswith("/auth/setup") or path.startswith("/auth/admin")):
        return await call_next(request)

    done = await _setup_is_done()
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
    """Call from the route rendering admin.html to set the cookie."""
    token = pysecrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE, token,
        secure=True, httponly=False, samesite="strict", max_age=3600,
    )
    return token


# ============================================================================
# Authelia: validation + user CRUD
# ============================================================================

def _load_authelia() -> dict:
    """
    Load users_database.yml. Raises a readable HTTPException 500 when the YAML
    is corrupt (hand-edited badly): points at /api/admin/backups to restore a
    known-good backup.
    """
    if not AUTHELIA_YML.exists():
        return {"users": {}}
    try:
        raw = AUTHELIA_YML.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"authelia yml unreadable: {e}") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise HTTPException(
            500,
            f"authelia yml corrupt: {e}. Restore a backup through "
            "POST /api/admin/backups/restore.",
        ) from e
    return data or {"users": {}}


# ============================================================================
# JSONC: orthanc.json is commented JSON
# ============================================================================
# Orthanc accepts // and /* */ comments in its configuration, and its reference
# configuration is full of them -- this repo's own file carries 128 comment
# lines. json.loads refuses them.
#
# So we mask them to read, and above all we NEVER re-serialise the whole file
# to write: a json.dumps(config) would produce a valid file but would wipe out
# every bit of documentation the administrator wrote in it. Writing replaces
# only the text of the modified value, in place.

_JSON_DECODER = json.JSONDecoder()
_JSON_WS = " \t\r\n"


def _mask_jsonc_comments(raw: str) -> str:
    """Replace comments with spaces, preserving offsets.

    The masked text has exactly the same length as the original: an index
    found in one designates the same character in the other, which lets us
    locate a value while ignoring comments, then edit the original text.
    Newlines are kept so line numbers in parsing errors stay accurate.
    """
    out = list(raw)
    i, n = 0, len(raw)
    in_string = False
    while i < n:
        c = raw[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == "/" and i + 1 < n and raw[i + 1] == "/":
            while i < n and raw[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and raw[i + 1] == "*":
            while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                if raw[i] != "\n":
                    out[i] = " "
                i += 1
            out[i] = out[min(i + 1, n - 1)] = " "
            i += 2
            continue
        i += 1
    return "".join(out)


def _jsonc_member_spans(masked: str, obj_start: int) -> dict[str, tuple[int, int]]:
    """Text bounds of every value of the object opening at obj_start."""
    spans: dict[str, tuple[int, int]] = {}
    i, n = obj_start + 1, len(masked)
    while i < n:
        while i < n and masked[i] in _JSON_WS + ",":
            i += 1
        if i >= n or masked[i] == "}":
            break
        key, i = _JSON_DECODER.raw_decode(masked, i)
        while i < n and masked[i] in _JSON_WS:
            i += 1
        if i >= n or masked[i] != ":":
            break
        i += 1
        while i < n and masked[i] in _JSON_WS:
            i += 1
        start = i
        _, i = _JSON_DECODER.raw_decode(masked, i)
        spans[str(key)] = (start, i)
    return spans


def _jsonc_locate(masked: str, dotted: str) -> tuple[int, int] | None:
    """Bounds of a dotted path's value, None when the path is absent."""
    try:
        spans = _jsonc_member_spans(masked, masked.index("{"))
    except (ValueError, json.JSONDecodeError):
        return None
    keys = dotted.split(".")
    for depth, key in enumerate(keys):
        if key not in spans:
            return None
        start, end = spans[key]
        if depth == len(keys) - 1:
            return start, end
        if masked[start] != "{":
            return None  # a scalar blocks the way down the path
        spans = _jsonc_member_spans(masked, start)
    return None


def _jsonc_insert(raw: str, dotted: str, value: Any) -> str:
    """Add a missing key at the head of its parent object."""
    keys = dotted.split(".")
    masked = _mask_jsonc_comments(raw)
    if len(keys) == 1:
        obj_start = masked.index("{")
    else:
        parent = _jsonc_locate(masked, ".".join(keys[:-1]))
        if parent is None or masked[parent[0]] != "{":
            raise ValueError(
                f"{dotted}: section {'.'.join(keys[:-1])!r} missing from the "
                "file, add it manually first"
            )
        obj_start = parent[0]

    line_start = raw.rfind("\n", 0, obj_start) + 1
    indent = " " * (len(raw[line_start:obj_start]) - len(raw[line_start:obj_start].lstrip()) + 2)
    member = f'\n{indent}{json.dumps(keys[-1])}: {json.dumps(value, ensure_ascii=False)},'
    return raw[: obj_start + 1] + member + raw[obj_start + 1 :]


def _patch_jsonc(raw: str, changes: dict[str, Any]) -> str:
    """Rewrite only the modified values, comments and layout untouched."""
    masked = _mask_jsonc_comments(raw)
    found, missing = [], {}
    for dotted, value in changes.items():
        span = _jsonc_locate(masked, dotted)
        if span is None:
            missing[dotted] = value
        else:
            found.append((span, value))

    # Backwards: a substitution late in the file cannot shift the earlier ones.
    for (start, end), value in sorted(found, key=lambda item: item[0][0], reverse=True):
        raw = raw[:start] + json.dumps(value, ensure_ascii=False) + raw[end:]

    for dotted, value in missing.items():
        raw = _jsonc_insert(raw, dotted, value)
    return raw


def _load_orthanc_config() -> dict:
    """Same for orthanc.json -- same explicit error strategy."""
    try:
        raw = ORTHANC_JSON.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"orthanc.json unreadable: {e}") from e
    try:
        return json.loads(_mask_jsonc_comments(raw))
    except json.JSONDecodeError as e:
        raise HTTPException(
            500,
            f"orthanc.json corrupt: {e}. Restore a backup through "
            "POST /api/admin/backups/restore.",
        ) from e


def _active_admins(data: dict) -> list[str]:
    """Non-disabled accounts of the admin group present in the YAML."""
    return [
        u for u, info in (data.get("users") or {}).items()
        if not info.get("disabled") and ADMIN_GROUP in (info.get("groups") or [])
    ]


def _validate_authelia(data: dict) -> None:
    """Invariants preventing a YAML that would lock everybody out."""
    if not isinstance(data.get("users"), dict) or not data["users"]:
        raise ValueError("users: section empty or missing")
    if not _active_admins(data):
        raise ValueError("at least 1 active admin required (lockout invariant)")
    for name, info in data["users"].items():
        for field in ("password", "email", "displayname"):
            if not info.get(field):
                raise ValueError(f"{name}: field {field!r} missing")
        if not info["password"].startswith("$argon2id$"):
            raise ValueError(f"{name}: password must be argon2id (start with $argon2id$)")


def _write_authelia(data: dict) -> None:
    """Backup + validate + atomic write. Guarded by a FileLock."""
    lock = FileLock(str(AUTHELIA_YML) + ".lock", timeout=5)
    try:
        with lock:
            _validate_authelia(data)
            if AUTHELIA_YML.exists():
                _backup(AUTHELIA_YML)
            serialized = yaml.safe_dump(
                data, default_flow_style=False, sort_keys=False, allow_unicode=True,
            )
            # Dry-run parse to catch serialisation bugs before replacing anything
            reloaded = yaml.safe_load(serialized) or {}
            _validate_authelia(reloaded)
            _atomic_write(AUTHELIA_YML, serialized)
    except Timeout as e:
        raise HTTPException(423, "file locked by another admin, retry in 5s") from e


class UserCreatePayload(BaseModel):
    username: str = Field(..., pattern=r"^[a-zA-Z0-9._-]{3,32}$")
    displayname: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=12)
    groups: list[str] = Field(default_factory=lambda: ["doctors"])


class PasswordChangePayload(BaseModel):
    new_password: str = Field(..., min_length=12)


# ============================================================================
# Orthanc config: validation + edit + reload
# ============================================================================

# Whitelist of paths editable through the UI. Anything absent is refused.
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
    "AcceptedTransferSyntaxes": list,  # special case: list of strings
}


def _apply_scalar_change(config: dict, dotted: str, value: Any) -> None:
    """Set config[a][b][c] = value. Refuses if the path overwrites a dict/array."""
    if dotted not in ORTHANC_EDITABLE_PATHS:
        raise ValueError(f"{dotted}: not editable through the UI")
    expected_type = ORTHANC_EDITABLE_PATHS[dotted]
    if not isinstance(value, expected_type):
        raise ValueError(f"{dotted}: expected {expected_type.__name__}, got {type(value).__name__}")
    if dotted == "DicomAet" and len(value) > 16:
        raise ValueError("DicomAet: 16 characters max (DICOM standard)")

    keys = dotted.split(".")
    node = config
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def _validate_orthanc(config: dict, before: dict | None = None) -> None:
    """Critical invariants to preserve.

    The two persistence flags are not required in absolute terms: they are
    absent from many existing configurations, and imposing them would fail any
    unrelated change with a 400. What we forbid is turning them off when they
    were on -- modalities and peers entered through the UI would then fall back
    into the file and be lost on restart.
    """
    before = before or {}
    for flag, perte in (
        ("DicomModalitiesInDatabase", "les modalites"),
        ("OrthancPeersInDatabase", "les peers"),
    ):
        if before.get(flag) and not config.get(flag):
            raise ValueError(
                f"{flag} ne peut pas repasser a false : {perte} saisis via l'UI "
                "seraient perdus au redemarrage"
            )
    # DicomAet max 16 chars
    if len(config.get("DicomAet", "")) > 16:
        raise ValueError("DicomAet: 16 characters max")


async def _reload_orthanc() -> None:
    """POST /tools/reset: Orthanc re-parses the JSON and applies the new config."""
    _require_orthanc_creds()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{ORTHANC_URL}/tools/reset",
            auth=(ORTHANC_USER, ORTHANC_PASS),
            headers=ORTHANC_AUTH_HEADERS,
        )
        r.raise_for_status()


async def _orthanc_runs_our_file(config: dict) -> bool | None:
    """Is Orthanc really running on the file we just wrote?

    orthancteam images do not start on /etc/orthanc/orthanc.json: their
    entrypoint merges that file with the environment variables into
    /tmp/orthanc.json when the container starts, and that copy is what POST
    /tools/reset re-reads. An edit to the mounted file is therefore only
    applied when the container restarts -- and the reset answers 200 without
    changing anything, which would have the panel report a misleading success.

    The witness: /system exposes the effective Name. If it does not match the
    file's after a reset, Orthanc is reading another source.
    Returns None when the comparison is inconclusive (no Name, or /system
    unreachable).
    """
    expected = config.get("Name")
    if not isinstance(expected, str):
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{ORTHANC_URL}/system",
                auth=(ORTHANC_USER, ORTHANC_PASS),
                headers=ORTHANC_AUTH_HEADERS,
            )
            r.raise_for_status()
            return r.json().get("Name") == expected
    except (httpx.HTTPError, ValueError):
        return None


class OrthancConfigPayload(BaseModel):
    """PATCH body: {"changes": {"Name": "Foo", "DicomAet": "BAR"}}"""
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
# Routes: setup wizard (unauthenticated)
# ============================================================================

router = APIRouter()


@router.get("/auth/setup", response_class=HTMLResponse)
async def setup_page():
    """Wizard HTML. setup_gate blocks it once setup is finalised."""
    return HTMLResponse(_render("setup.html"))


@router.get("/auth/admin", response_class=HTMLResponse)
async def admin_page(response: Response, admin: AdminUser = Depends(require_admin)):
    """Admin hub HTML. Sets the CSRF cookie at the same time."""
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
    Step 1: create THE first admin. A single call is allowed until finalize.

    Locked after the first success through SETUP_FIRST_ADMIN_KEY, to stop a
    third party creating a second admin through the wizard's open window.
    To add further admins afterwards: POST /api/admin/users (auth required).
    """
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, "setup already finalised, use /api/admin/users")
    if (await _r().get(SETUP_FIRST_ADMIN_KEY)) == "1":
        raise HTTPException(
            409,
            "an admin has already been created — finalise the setup (POST "
            "/auth/setup/finalize) then use /api/admin/users to add more",
        )
    if ADMIN_GROUP not in payload.groups:
        payload.groups.append(ADMIN_GROUP)
    data = _load_authelia()
    if payload.username in data.get("users", {}):
        raise HTTPException(409, f"user {payload.username} already exists")
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
    """Final step: check the active-admin invariant, then flip the flag."""
    if (await _r().get(SETUP_KEY)) == "1":
        raise HTTPException(409, "setup already finalised")
    admins = _active_admins(_load_authelia())
    if not admins:
        raise HTTPException(400, "create an admin first (POST /auth/setup/create-admin)")
    await _r().set(SETUP_KEY, "1")
    await _r().delete(SETUP_FIRST_ADMIN_KEY)  # setup lock lifted, no longer useful
    await _audit("setup.finalized", actor="wizard", admin_count=len(admins))
    return {"ok": True, "admins": admins}


# ============================================================================
# Routes: /api/admin/users/* (auth required)
# ============================================================================

@router.get("/api/admin/users")
async def list_users(admin: AdminUser = Depends(require_admin)):
    data = _load_authelia()
    # Never return the hashes
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
        raise HTTPException(409, "user already exists")
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
        raise HTTPException(404, "unknown user")
    data["users"][username]["password"] = _hasher.hash(payload.new_password)
    _write_authelia(data)
    await _audit("authelia.password.changed", admin.username, target=username)
    return {"ok": True}


@router.delete("/api/admin/users/{username}")
async def delete_user(username: str, admin: AdminUser = Depends(require_admin)):
    if username == admin.username:
        raise HTTPException(400, "you cannot delete yourself")
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, "unknown user")
    del data["users"][username]
    _write_authelia(data)  # enforces the "at least 1 active admin" invariant
    await _audit("authelia.user.deleted", admin.username, target=username)
    return {"ok": True}


# ============================================================================
# Routes: /api/admin/orthanc/config
# ============================================================================

@router.get("/api/admin/orthanc/config")
async def read_orthanc_config(admin: AdminUser = Depends(require_admin)):
    config = _load_orthanc_config()
    # Return only the editable values (whitelist)
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
    """Apply a batch of changes: backup, /tools/reset, audit."""
    lock = FileLock(str(ORTHANC_JSON) + ".lock", timeout=5)
    try:
        with lock:
            before = _load_orthanc_config()  # gere JSON corrompu
            config = copy.deepcopy(before)
            for path, value in payload.changes.items():
                _apply_scalar_change(config, path, value)
            _validate_orthanc(config, before)
            backup = _backup(ORTHANC_JSON)

            # In-place text edit: re-serialising the whole file would wipe out
            # the administrator's comments.
            raw = ORTHANC_JSON.read_text(encoding="utf-8")
            serialized = _patch_jsonc(raw, payload.changes)

            # Guard: the rewritten text must re-parse to exactly the expected
            # config. An offset slip or a substitution in the wrong section
            # would show up here, before anything is written.
            try:
                reparsed = json.loads(_mask_jsonc_comments(serialized))
            except json.JSONDecodeError as e:
                raise HTTPException(
                    500, f"invalid orthanc.json edit, nothing written: {e}",
                ) from e
            if reparsed != config:
                raise HTTPException(
                    500,
                    "orthanc.json edit inconsistent with the requested changes, "
                    "nothing written",
                )

            _atomic_write(ORTHANC_JSON, serialized)
    except Timeout as e:
        raise HTTPException(423, "orthanc.json locked, retry") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    reset_error = None
    try:
        await _reload_orthanc()
    except httpx.HTTPError as e:
        # Auto-rollback: restore the backup and retry the reset
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
                f"Orthanc reload failed ({reset_error}). Auto-rollback failed too "
                f"({rollback_err}). Inconsistent state, manual restore required: "
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
            f"Orthanc reload failed ({reset_error}). Automatic rollback performed "
            f"from {backup.name}. Config left in its previous state.",
        ) from e

    await _audit(
        "orthanc.config.updated",
        admin.username,
        fields=",".join(payload.changes.keys()),
        backup=backup.name,
    )
    return {"ok": True, "backup": backup.name}


# ============================================================================
# Routes: /api/admin/cf-access
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
    """Atomic rotation: snapshot the old pair to history, set the new, audit."""
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
# Internal route: verify-cf (called by nginx auth_request)
# ============================================================================

@router.get("/api/internal/verify-cf", include_in_schema=False)
async def verify_cf(
    x_cf_client_id: str = Header(default=""),
    x_cf_client_secret: str = Header(default=""),
):
    """
    Compare the CF headers against the values stored in Redis.

    Fail closed: if Redis is unavailable we return 403 (not 500) so that nginx
    blocks the upload. Better to refuse a legitimate upload during a Redis
    outage than to let a secret through during a blackout.
    """
    try:
        expected_id = await _r().get(CF_ID_KEY)
        expected_secret = await _r().get(CF_SECRET_KEY)
    except RedisError:
        return Response(status_code=403)  # fail closed

    if not expected_id or not expected_secret:
        return Response(status_code=503)  # not configured

    if not pysecrets.compare_digest(x_cf_client_id, expected_id):
        return Response(status_code=403)
    if not pysecrets.compare_digest(x_cf_client_secret, expected_secret):
        return Response(status_code=403)

    # Hit counter metric — silent failure if Redis is flaky here, not critical
    try:
        await _r().incr("cf_access:checks_ok:24h")
    except RedisError:
        pass
    return Response(status_code=204)


# ============================================================================
# Route: /api/admin/health (checks Redis + Orthanc + config files)
# ============================================================================

@router.get("/api/admin/health")
async def admin_health(admin: AdminUser = Depends(require_admin)):
    """
    Diagnostics for the Health tab: state of auth-service's dependencies.

    Returns 200 with one dict per component ({ok: bool, detail: str}), even
    when some components are down — it is the UI's job to decide what to show.
    We avoid a global 503, which would hide which component is at fault.
    """
    checks = {}

    # Redis
    try:
        pong = await _r().ping()
        checks["redis"] = {"ok": bool(pong), "detail": "PONG"}
    except RedisError as e:
        checks["redis"] = {"ok": False, "detail": f"RedisError: {e}"}

    # Config files readable + parseable
    try:
        _load_authelia()
        checks["authelia_yml"] = {"ok": True, "detail": str(AUTHELIA_YML)}
    except FileNotFoundError:
        checks["authelia_yml"] = {"ok": False, "detail": "file missing"}
    except (yaml.YAMLError, OSError) as e:
        checks["authelia_yml"] = {"ok": False, "detail": f"parse error: {e}"}

    try:
        if ORTHANC_JSON.exists():
            json.loads(_mask_jsonc_comments(ORTHANC_JSON.read_text(encoding="utf-8")))
            checks["orthanc_json"] = {"ok": True, "detail": str(ORTHANC_JSON)}
        else:
            checks["orthanc_json"] = {"ok": False, "detail": "file missing"}
    except (json.JSONDecodeError, OSError) as e:
        checks["orthanc_json"] = {"ok": False, "detail": f"parse error: {e}"}

    # Orthanc API reachable (/system endpoint, less invasive than /tools/reset)
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(
                f"{ORTHANC_URL}/system",
                auth=(ORTHANC_USER, ORTHANC_PASS),
                headers=ORTHANC_AUTH_HEADERS,
            )
            checks["orthanc_api"] = {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        checks["orthanc_api"] = {"ok": False, "detail": f"HTTPError: {e}"}

    return {"checks": checks}


# ============================================================================
# Route: backup rollback
# ============================================================================

@router.post("/api/admin/backups/restore")
async def restore_backup(
    backup_name: str,
    admin: AdminUser = Depends(require_admin),
):
    """Restore a backup from /host/backups/ onto its original file."""
    src = BACKUPS_DIR / backup_name
    if not src.exists() or ".bak." not in backup_name:
        raise HTTPException(404, "backup not found or invalid name")

    if backup_name.startswith("orthanc.json.bak."):
        dest = ORTHANC_JSON
        reload = _reload_orthanc
    elif backup_name.startswith("users_database.yml.bak."):
        dest = AUTHELIA_YML
        reload = None  # Authelia watch
    else:
        raise HTTPException(400, "unsupported backup type")

    _backup(dest, tag="pre-restore")
    shutil.copy2(src, dest)
    if reload:
        await reload()

    await _audit("backup.restored", admin.username, backup=backup_name)
    return {"ok": True}
