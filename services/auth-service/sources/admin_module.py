"""
Admin/setup module for auth-service (FastAPI).

Mount it in the main auth_service.py with:
    from admin_module import router as admin_router, setup_gate
    app.include_router(admin_router)
    app.middleware("http")(setup_gate)

Depends: fastapi, redis.asyncio, pyyaml, argon2-cffi, httpx, filelock, pydantic
Required env vars: ORTHANC_ADMIN_USER, ORTHANC_ADMIN_PASS, ORTHANC_URL, REDIS_URL
"""

import asyncio
import copy
import json
import logging
import os
import re
import secrets as pysecrets
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis
import yaml
from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from filelock import FileLock, Timeout
from pydantic import BaseModel, EmailStr, Field, model_validator
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

# How a configuration change reaches Orthanc.
#
#   "restart" (default) -- write the file and say so. On orthancteam images
#       Orthanc does not run on the mounted file: the entrypoint merges it with
#       the environment into /tmp/orthanc.json at container start, and that copy
#       is what POST /tools/reset re-reads. Calling the reset there answers 200
#       and changes nothing, which is worse than not calling it: it reports a
#       success that did not happen. The container has to be restarted.
#
#   "reset" -- also POST /tools/reset. Only meaningful where Orthanc is started
#       directly on this file (command: ["Orthanc", "/etc/orthanc/orthanc.json"]),
#       which additionally needs a permission pattern for that route, absent from
#       the plugin's StandardConfigurations:
#           "Permissions": [["post", "^/tools/reset$", "all|settings"]]
ORTHANC_APPLY_MODE = os.environ.get("ORTHANC_APPLY_MODE", "restart")

# Restarting Orthanc from the panel, through a Docker socket proxy.
#
# Remotely, SSH is not always available: with no way out from the interface,
# a configuration change leaves the operator stuck inside their own panel,
# reading "restart required" with no means of doing it.
#
# The Docker socket is deliberately NOT mounted into auth-service: handing it
# to a web-facing service grants the equivalent of root on the host. The proxy
# in front of it must expose the bare minimum -- POST=1, ALLOW_RESTARTS=1, and
# everything else at 0, CONTAINERS included. That last point is not cosmetic:
# with CONTAINERS=1, POST=1 opens the whole of /containers/*, POST
# /containers/create included, and a privileged container mounting the host
# root is then accepted -- exactly the escape this arrangement must prevent.
#
# Left empty, the feature is simply unavailable and the panel says so.
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "").rstrip("/")
ORTHANC_CONTAINER = os.getenv("ORTHANC_CONTAINER", "orthanc-server")


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
# Authelia's own configuration, where the session durations live. Separate from
# AUTHELIA_YML, which is the accounts file.
AUTHELIA_CONFIG = Path(os.getenv("ADMIN_AUTHELIA_CONFIG", "/host/authelia/configuration.yml"))

# The .env file, at the project root. Mounted as a FILE rather than through
# its directory: mounting the root would give the container write access to
# docker-compose.yml and to the scripts. Consequence: writes happen in place,
# see _write_env_var.
ENV_FILE = Path(os.getenv("ADMIN_ENV_PATH", "/host/env/.env"))

# Application settings, as opposed to bootstrap variables.
#
# A setting that only auth-service reads, once running, has no business in the
# compose file: changing it there means editing a file and recreating the
# container, for something the panel ought to offer. This file holds those
# settings, and the panel writes it while the stack runs.
#
# It defaults into the backups directory because that one is ALREADY
# bind-mounted read-write on every install -- so the store works without
# anyone having to touch their compose file first. list_backups ignores it:
# the listing keeps only the names _backup_target recognises.
SETTINGS_FILE = Path(
    os.getenv("ADMIN_SETTINGS_PATH", str(BACKUPS_DIR / "settings.json"))
)

# Hooked into auth_service's logger hierarchy, so LOG_LEVEL applies here too.
logger = logging.getLogger("auth-service.admin")

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


_settings_cache: dict[str, Any] = {"key": None, "data": {}}


def _read_settings() -> dict[str, Any]:
    """Contents of the settings file. Empty dict when it does not exist yet.

    Cached on (path, mtime): a setting is read on nearly every request, and
    re-reading the file each time would mean dozens of reads per page.
    """
    try:
        key = (str(SETTINGS_FILE), SETTINGS_FILE.stat().st_mtime)
    except OSError:
        # No file: fresh install, or no setting ever changed.
        _settings_cache["key"] = None
        _settings_cache["data"] = {}
        return {}

    if _settings_cache["key"] == key:
        return _settings_cache["data"]

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # An unreadable settings file must not stop the service from
        # answering: fall back to the defaults, and say so in the log rather
        # than degrading in silence.
        logger.warning("unreadable settings (%s), falling back to defaults", e)
        data = {}

    _settings_cache["key"] = key
    _settings_cache["data"] = data
    return data


def _read_setting(name: str, env_var: str = "", default: Any = None) -> Any:
    """A setting's value, falling back to the environment variable it replaces.

    `env_var` keeps existing installations working: these settings used to be
    passed through the compose file, and are still read from there until they
    get redefined from the panel. The first write moves the value into the
    settings file, after which the compose line has no further effect.
    """
    settings = _read_settings()
    if name in settings:
        return settings[name]
    if env_var:
        previous = os.getenv(env_var, "")
        if previous:
            return previous
    return default


def _write_setting(name: str, value: Any) -> None:
    """Write one setting, creating the file and its directory when needed."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            503,
            f"settings directory not writable ({e}). Check that "
            f"'{SETTINGS_FILE.parent}' is bind-mounted read-write on "
            f"auth-service.",
        ) from e

    settings = _read_settings()
    settings[name] = value
    _atomic_write(SETTINGS_FILE,
                  json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    # Invalidate explicitly: mtime granularity is sometimes one second, which
    # would make two writes in the same second indistinguishable.
    _settings_cache["key"] = None


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
    """Replace (or add) name=value in .env, writing IN PLACE.

    No write-tmp + rename here, unlike everywhere else in this module: .env is
    a file bind-mount. The rename would fail (EBUSY) and, were it to succeed,
    docker compose would keep reading the old inode. So we rewrite the same
    file, after a backup -- an interrupted write would otherwise leave a
    truncated .env, and the stack would no longer start.
    """
    if not ENV_FILE.exists():
        raise HTTPException(
            503,
            "the .env file is not reachable from the container. Add the mount "
            "'./.env:/host/env/.env:rw' to the auth-service service, then "
            "recreate the container.",
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


def _normalise_public_url(raw: str) -> tuple[str, str]:
    """Validate the public URL. Returns (origin, host without port).

    The origin drives redirections, port included; the host is what session
    cookies are scoped to -- a cookie never carries a port.
    """
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https":
        raise HTTPException(400, "the public URL must start with https://")
    if not parsed.hostname:
        raise HTTPException(400, "host missing from the public URL")
    if parsed.path.strip("/"):
        raise HTTPException(
            400,
            "give the origin alone, with no path "
            "(for example https://pacs.example.org)",
        )
    # RFC 6265: some browsers drop a cookie set on a host without a dot.
    # "localhost" is the exception, "mypacs" is not.
    if "." not in parsed.hostname and parsed.hostname != "localhost":
        raise HTTPException(
            400,
            f"'{parsed.hostname}' has no dot: browsers will refuse the session "
            f"cookie. Use a fully qualified name (pacs.example.org) or "
            f"pacs.localhost.",
        )
    return f"https://{parsed.netloc}", parsed.hostname


def _retarget_authelia_config(previous_origin: str, previous_host: str,
                              origin: str, host: str) -> int:
    """Point configuration.yml at the new public URL.

    Textual replacement rather than a YAML load and re-serialise: the file is
    heavily commented -- access rules, warnings about the cookie port -- and a
    round-trip through PyYAML would wipe all of it.

    The host appears in every access_control rule as well as in the session
    cookie block, which is precisely why hand-editing it is error-prone: miss
    one occurrence and Authelia answers 401 on everything, with the login page
    itself unreachable.

    Returns the number of substitutions made.
    """
    if not AUTHELIA_CONFIG.exists():
        raise HTTPException(503, "Authelia's configuration.yml not found")
    text = AUTHELIA_CONFIG.read_text(encoding="utf-8")
    total = text.count(previous_origin) + text.count(previous_host)
    if not total:
        raise HTTPException(
            500,
            f"no trace of '{previous_host}' in configuration.yml: the file was "
            f"edited by hand, change aborted",
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
        raise HTTPException(
            500, "PUBLIC_URL absent from .env, change aborted")
    if previous_origin == origin:
        return {"ok": True, "unchanged": True, "public_url": origin}

    _, previous_host = _normalise_public_url(previous_origin)
    substitutions = _retarget_authelia_config(
        previous_origin, previous_host, origin, host,
    )
    _write_env_var("PUBLIC_URL", origin)
    _write_env_var("DOMAIN", host)
    await _audit(
        "network.public_url.changed", actor=actor,
        old=previous_origin, new=origin, substitutions=substitutions,
    )
    return {
        "ok": True,
        "unchanged": False,
        "public_url": origin,
        "substitutions": substitutions,
    }


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

    Once setup is done the wizard answers 404, it does not redirect. It is the
    only route of the panel that sits outside SSO -- it has to, since at first
    run no account exists to authenticate with -- so once it has served its
    purpose the right thing is for it to stop existing as far as the outside
    world can tell. A redirect would confirm the endpoint is there and would
    leave the POST routes one middleware decision away from being reachable.
    """
    path = request.url.path
    if not (path.startswith("/auth/setup") or path.startswith("/auth/admin")):
        return await call_next(request)

    done = await _setup_is_done()
    is_setup = path.startswith("/auth/setup")

    if is_setup and done:
        return Response(status_code=404)
    if not is_setup and not done:
        return RedirectResponse("/auth/setup", status_code=302)
    return await call_next(request)


# ============================================================================
# Middleware : CSRF (double-submit token + origin check)
# ============================================================================

# Extra origins accepted by the check, comma separated. Needed when the panel is
# reached under a name the proxy does not forward, e.g. a LAN address or an
# alternate domain.
CSRF_ALLOWED_ORIGINS = {
    o.strip().rstrip("/")
    for o in os.getenv("CSRF_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
}


def _acceptable_origins(request: Request) -> set[str]:
    """Origins that count as same-site for this request.

    Comparing Origin against the Host header alone is not enough here: nginx
    rewrites Host to the configured DOMAIN, so a browser reaching the panel
    under any other name (a LAN address, a second domain) sends an Origin that
    can never match the rewritten Host, and every write is refused. We therefore
    accept the forwarded host as well, plus anything listed explicitly.
    """
    origins = set(CSRF_ALLOWED_ORIGINS)
    for header in ("host", "x-forwarded-host"):
        host = request.headers.get(header, "").split(",")[0].strip()
        if host:
            origins.add(f"https://{host}")
            origins.add(f"http://{host}")
    return origins


async def csrf_gate(request: Request, call_next):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    if not request.url.path.startswith("/api/admin/"):
        return await call_next(request)

    # 1. Origin match. Absent Origin is allowed through: non-browser clients do
    #    not send it, and the double-submit token below is the real barrier.
    origin = request.headers.get("origin", "").rstrip("/")
    if origin:
        acceptable = _acceptable_origins(request)
        if origin not in acceptable:
            # The detail is spelled out because this route is already behind
            # Authelia and the group check: only an administrator sees it, and
            # without it a proxy misconfiguration is undiagnosable from the UI.
            return JSONResponse(
                {
                    "error": "csrf.origin",
                    "detail": (
                        f"Origin {origin} is not accepted. Expected one of "
                        f"{sorted(acceptable)}. Add it to CSRF_ALLOWED_ORIGINS "
                        "if the panel is legitimately reached under that name."
                    ),
                },
                status_code=403,
            )

    # 2. Double-submit token
    cookie_tok = request.cookies.get(CSRF_COOKIE, "")
    header_tok = request.headers.get("x-csrf-token", "")
    if not cookie_tok or not header_tok or not pysecrets.compare_digest(cookie_tok, header_tok):
        return JSONResponse(
            {
                "error": "csrf.token",
                "detail": (
                    "CSRF cookie and header do not match. Reload the page: the "
                    f"{CSRF_COOKIE} cookie is set when /auth/admin is rendered "
                    "and expires after one hour."
                ),
            },
            status_code=403,
        )

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


# An account's identity is the key it has in users_database.yml -- that is what
# Authelia matches at login. Many deployments, this one included, use e-mail
# addresses as those keys, so the pattern has to allow a domain part. The old
# expression forbade "@" outright, which made it impossible to create an account
# in the very format the file already used.
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._%+-]{3,64}(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})?$")


class UserCreatePayload(BaseModel):
    # Optional: with no explicit login, the e-mail address is the identity.
    username: str | None = Field(default=None, max_length=100)
    displayname: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=12)
    groups: list[str] = Field(default_factory=lambda: ["doctors"])

    @model_validator(mode="after")
    def _resolve_identity(self):
        if not self.username:
            self.username = str(self.email)
        if not _USERNAME_RE.match(self.username):
            raise ValueError(
                "username: 3 to 64 characters among letters, digits and . _ % + - "
                "optionally followed by an e-mail domain"
            )
        return self


class UserUpdatePayload(BaseModel):
    """Partial update: only the fields provided are applied.

    None means "leave alone", which allows changing groups without resending
    the display name and e-mail, and disabling an account without rewriting
    anything else.
    """
    displayname: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr | None = None
    groups: list[str] | None = None
    disabled: bool | None = None


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
    r = await _orthanc("POST", "/tools/reset", timeout=10)
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
        r = await _orthanc("GET", "/system", timeout=5)
        r.raise_for_status()
        return r.json().get("Name") == expected
    except (httpx.HTTPError, ValueError):
        return None


# Settings whose applied value Orthanc exposes, and where to read it.
#
# Writing a value proves nothing about Orthanc applying it. Three ways to
# diverge with nothing to signal it: an ORTHANC__* variable from the compose
# file overriding the file, a field declared at the wrong place in the tree,
# or a restart that never happened. The second is not theoretical --
# StudyListColumns sat under OrthancExplorer2 while Explorer reads it under
# UiOptions, so the setting had never had any effect since it existed.
#
# Only settings Orthanc reports back can appear here: the rest cannot be
# checked, and pretending otherwise would be worse than saying nothing.
ORTHANC_VERIFIABLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "Name": ("/system", ("Name",)),
    "DicomAet": ("/system", ("DicomAet",)),
    "DicomPort": ("/system", ("DicomPort",)),
    "HttpPort": ("/system", ("HttpPort",)),
    "StorageCompression": ("/system", ("StorageCompression",)),
    "IngestTranscoding": ("/system", ("IngestTranscoding",)),
}


async def _check_effective_config() -> list[dict[str, Any]]:
    """Compare what orthanc.json declares with what Orthanc applies.

    Only returns divergences. A field absent from the file is not one:
    Orthanc then applies its default, which is the expected behaviour. Nor is
    a field Orthanc does not expose in this version.
    """
    try:
        config = _load_orthanc_config()
    except Exception:  # noqa: BLE001 - unreadable file, reported elsewhere
        return []

    responses: dict[str, dict] = {}
    for endpoint in {e for e, _ in ORTHANC_VERIFIABLE.values()}:
        try:
            r = await _orthanc("GET", endpoint, timeout=5)
            responses[endpoint] = r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001 - Orthanc mute: nothing to compare
            responses[endpoint] = {}

    mismatches = []
    for path, (endpoint, access) in ORTHANC_VERIFIABLE.items():
        wanted = config
        for part in path.split("."):
            if not isinstance(wanted, dict) or part not in wanted:
                wanted = None
                break
            wanted = wanted[part]
        if wanted is None:
            continue  # not declared: the default applies

        effective = responses.get(endpoint) or {}
        for part in access:
            if not isinstance(effective, dict) or part not in effective:
                effective = None
                break
            effective = effective[part]
        if effective is None:
            continue  # Orthanc does not expose it in this version

        if wanted != effective:
            mismatches.append({
                "field": path,
                "in_file": wanted,
                "applied_by_orthanc": effective,
            })

    return mismatches


async def _wait_for_orthanc(attempts: int = 30, pause: int = 2) -> str:
    """Wait for Orthanc to answer. Returns its version, or "" if it stays mute.

    Orthanc opens its port before it has finished loading its plugins, so we
    query /system, which only answers once the server is genuinely ready.
    """
    for _ in range(attempts):
        await asyncio.sleep(pause)
        try:
            probe = await _orthanc("GET", "/system", timeout=5)
            if probe.status_code == 200:
                return probe.json().get("Version", "unknown")
        except Exception:  # noqa: BLE001 - expected while restarting
            pass
    return ""


def _latest_orthanc_backup() -> Path | None:
    """The most recent orthanc.json backup, if any.

    Names carry a timestamp (orthanc.json.bak.YYYYMMDD-HHMMSS), so
    alphabetical order is chronological order.
    """
    prefix = ORTHANC_JSON.name + ".bak."
    backups = sorted(BACKUPS_DIR.glob(prefix + "*"), reverse=True)
    return backups[0] if backups else None


async def _request_restart() -> None:
    """Ask the Docker proxy to restart the container."""
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{DOCKER_PROXY_URL}/containers/{ORTHANC_CONTAINER}/restart",
        )
    if r.status_code == 404:
        raise HTTPException(
            502,
            f"Container '{ORTHANC_CONTAINER}' not found. Check "
            f"ORTHANC_CONTAINER in the compose file.",
        )
    if r.status_code not in (204, 304):
        raise HTTPException(
            502,
            f"The Docker proxy refused the restart (HTTP {r.status_code}). "
            f"Check ALLOW_RESTARTS on the socket-proxy service.",
        )


class OrthancConfigPayload(BaseModel):
    """PATCH body: {"changes": {"Name": "Foo", "DicomAet": "BAR"}}"""
    changes: dict[str, Any]


class ModalityPayload(BaseModel):
    """A declared DICOM device: an AE title, a host and a port."""
    aet: str = Field(min_length=1, max_length=16)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)


def _require_orthanc_creds() -> None:
    if not ORTHANC_USER or not ORTHANC_PASS:
        raise HTTPException(
            503,
            "ORTHANC_ADMIN_USER/ORTHANC_ADMIN_PASS are not set in .env -- the "
            "endpoint is reachable but cannot call Orthanc",
        )


async def _orthanc(method: str, path: str, timeout: float = 15,
                   **kwargs: Any) -> httpx.Response:
    """Call Orthanc's API with the service account.

    ORTHANC_AUTH_HEADERS carries the group token, which is what the
    Authorization plugin resolves a profile from. Not Remote-User: the
    plugin's TokenHttpHeaders lists ["X-Auth-User", "Remote-User",
    "auth-token"] and the LAST recognised header wins, so sending Remote-User
    alone resolves the profile of a *user* named admin rather than the admin
    group -- a call that then passes for anonymous, a profile holding only
    the upload permission.
    """
    _require_orthanc_creds()
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(
            method, f"{ORTHANC_URL}{path}",
            auth=(ORTHANC_USER, ORTHANC_PASS),
            headers=ORTHANC_AUTH_HEADERS,
            **kwargs,
        )


# ============================================================================
# CF Access : verify (auth_request) + rotate + test
# ============================================================================

# Whether nginx actually gates /api-upload/ on this pair, i.e. whether the
# auth_request /_verify-cf line is enabled in nginx.ssl.conf. The panel cannot
# see nginx's configuration, and storing a pair nothing reads while announcing
# an immediate effect is the kind of false success this project keeps running
# into -- so the state is declared here and shown as-is in the UI.
CF_ACCESS_ENFORCED = os.getenv("CF_ACCESS_ENFORCED", "false").lower() == "true"

# Cloudflare Access, origin-side verification. The team domain pins the issuer
# and provides the signing keys; the audience identifies this application and is
# the cf-access-aud header Cloudflare returns on a refused request.
CF_ACCESS_TEAM_DOMAIN = os.getenv("CF_ACCESS_TEAM_DOMAIN", "")
CF_ACCESS_AUD = os.getenv("CF_ACCESS_AUD", "")


# The three values above are read once, at import: changing them meant editing
# the compose file and recreating the container. They are now resolved per
# request, from the settings file, and fall back to the value the environment
# supplied at startup -- so an installation that never touches the panel keeps
# behaving exactly as before.
def _cf_team_domain() -> str:
    return _read_setting("cf_access_team_domain", default=CF_ACCESS_TEAM_DOMAIN)


def _cf_aud() -> str:
    return _read_setting("cf_access_aud", default=CF_ACCESS_AUD)


def _cf_enforced() -> bool:
    value = _read_setting("cf_access_enforced", default=CF_ACCESS_ENFORCED)
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"



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
    # Close the window: only one finalize is acceptable from now on
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


@router.patch("/api/admin/users/{username}")
async def update_user(
    username: str,
    payload: UserUpdatePayload,
    admin: AdminUser = Depends(require_admin),
):
    """Modify an existing account without touching its password.

    The panel could only create and delete: changing someone's group meant
    destroying their account and recreating it, losing their password on the
    way. And an account that should simply stop working -- someone gone, a
    device retired -- had to be deleted outright, taking its history with it.

    The "at least one active administrator" invariant is checked BEFORE
    writing. _validate_authelia catches the case too, but only once the write
    is under way, and it raises a bare ValueError: the operator would get a
    500 with no reason.
    """
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, "unknown user")

    info = data["users"][username]
    modified = []
    if payload.displayname is not None:
        info["displayname"] = payload.displayname
        modified.append("displayname")
    if payload.email is not None:
        info["email"] = str(payload.email)
        modified.append("email")
    if payload.groups is not None:
        info["groups"] = payload.groups
        modified.append("groups")
    if payload.disabled is not None:
        info["disabled"] = payload.disabled
        modified.append("disabled")

    if not modified:
        raise HTTPException(400, "no field to change")

    if not _active_admins(data):
        raise HTTPException(
            400,
            f"this change would leave no active administrator: {username} is "
            f"the last one. Removing it from the admin group, or disabling "
            f"it, would leave the stack with nobody able to administer it.",
        )

    _write_authelia(data)
    await _audit(
        "authelia.user.updated", admin.username, target=username,
        fields=",".join(modified),
    )
    return {"ok": True, "modified": modified}


@router.delete("/api/admin/users/{username}")
async def delete_user(username: str, admin: AdminUser = Depends(require_admin)):
    if username == admin.username:
        raise HTTPException(400, "you cannot delete yourself")
    data = _load_authelia()
    if username not in data.get("users", {}):
        raise HTTPException(404, "unknown user")

    # Refuse BEFORE touching anything. _validate_authelia does catch the case,
    # but only once _write_authelia is under way, and it raises a bare
    # ValueError: the operator got a 500 instead of a reason. The account
    # survived -- the write aborts before persisting -- yet an invariant that
    # holds by accident of validation, and reports itself as a server error,
    # is not a safeguard.
    if not [u for u in _active_admins(data) if u != username]:
        raise HTTPException(
            400,
            f"{username} is the last active administrator: deleting it would "
            f"leave the stack with nobody able to administer it, and the only "
            f"way back would be editing users_database.yml by hand. Create "
            f"another administrator first.",
        )

    del data["users"][username]
    _write_authelia(data)
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

    if ORTHANC_APPLY_MODE != "reset":
        await _audit(
            "orthanc.config.written",
            admin.username,
            fields=",".join(payload.changes.keys()),
            backup=backup.name,
        )
        return {
            "ok": True,
            "backup": backup.name,
            "applied": False,
            "restart_required": True,
            "detail": (
                "Configuration written. Orthanc starts on a copy its entrypoint "
                "builds from this file, so the change applies once the container "
                "is restarted: docker compose restart orthanc"
            ),
        }

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

    # The reset answered 200, which proves Orthanc reloaded something -- not
    # that it reloaded OUR file. When the two disagree, saying "saved" and
    # stopping there sends the operator looking for the fault everywhere
    # except where it is.
    applied = await _orthanc_runs_our_file(config)
    if applied is False:
        return {
            "ok": True,
            "backup": backup.name,
            "warning": (
                "Saved, but Orthanc is not running on this file: its image "
                "merges /etc/orthanc/*.json with the ORTHANC__* variables into "
                "a copy at startup, and that copy is what a reload re-reads. "
                "Restart the orthanc container for the change to take effect."
            ),
        }

    return {"ok": True, "backup": backup.name}



@router.get("/api/admin/config-effective")
async def config_effective(admin: AdminUser = Depends(require_admin)):
    """Which settings Orthanc does not apply as written.

    An empty list is the good answer: what the file declares is what runs.
    """
    return {"mismatches": await _check_effective_config()}

@router.post("/api/admin/orthanc/restart")
async def restart_orthanc(admin: AdminUser = Depends(require_admin)):
    """Restart the Orthanc container and wait for it to answer again.

    A configuration change only takes effect after a restart: the orthancteam
    image GENERATES /tmp/orthanc.json at startup, merging its defaults, the
    files under /etc/orthanc/ and the ORTHANC__* variables, and that generated
    file is what the process reads.

    We wait for Orthanc to actually come back rather than answering as soon as
    Docker hands control back. A configuration accepted on write may well stop
    Orthanc from starting, and the operator must learn it here -- not by
    finding a dead PACS later on.
    """
    if not DOCKER_PROXY_URL:
        raise HTTPException(
            503,
            "Restart unavailable: DOCKER_PROXY_URL is not set. Enable the "
            "socket-proxy service, or restart by hand with "
            "'docker compose restart orthanc'.",
        )

    await _audit("orthanc.restart.requested", admin.username,
                 container=ORTHANC_CONTAINER)

    try:
        await _request_restart()
    except httpx.HTTPError as e:
        await _audit("orthanc.restart.failed", admin.username, error=str(e))
        raise HTTPException(502, f"Docker proxy unreachable: {e}") from e
    except HTTPException:
        await _audit("orthanc.restart.failed", admin.username,
                     container=ORTHANC_CONTAINER)
        raise

    version = await _wait_for_orthanc()
    if version:
        await _audit("orthanc.restarted", admin.username,
                     container=ORTHANC_CONTAINER)
        # Answering is not the same as applying what we wrote: an ORTHANC__*
        # variable from the compose file overrides the file silently.
        mismatches = await _check_effective_config()
        if mismatches:
            await _audit("orthanc.config.divergent", admin.username,
                         fields=",".join(m["field"] for m in mismatches))
            return {
                "ok": True,
                "version": version,
                "mismatches": mismatches,
                "warning": (
                    f"Orthanc restarted, but {len(mismatches)} setting(s) are "
                    f"not applied as written. An ORTHANC__* variable in the "
                    f"compose file is probably overriding them."
                ),
            }
        return {"ok": True, "version": version,
                "message": "Orthanc restarted, configuration applied."}

    # Orthanc is not coming back. The likeliest cause is the configuration
    # just written: a value can be of the right type, produce perfectly valid
    # JSON, and still be unacceptable to it -- an out-of-range port, say.
    # Leaving a PACS down while pointing at the logs is not an answer.
    await _audit("orthanc.restart.no_response", admin.username,
                 container=ORTHANC_CONTAINER)

    backup = _latest_orthanc_backup()
    if backup is None:
        raise HTTPException(
            504,
            "Orthanc has not answered for 60 s and no backup of its "
            "configuration is available. Check its logs "
            "(docker compose logs orthanc).",
        )

    try:
        shutil.copy2(backup, ORTHANC_JSON)
        await _request_restart()
    except Exception as e:  # noqa: BLE001 - we are already in the worst case
        await _audit("orthanc.rollback.failed", admin.username,
                     backup=backup.name, error=str(e))
        raise HTTPException(
            500,
            f"Orthanc is not answering, and restoring {backup.name} failed "
            f"({e}). Manual intervention required.",
        ) from e

    if await _wait_for_orthanc():
        await _audit("orthanc.rolled_back", admin.username, backup=backup.name)
        raise HTTPException(
            502,
            f"Orthanc did not restart with the new configuration: "
            f"{backup.name} was restored, and it is answering again. The "
            f"change was refused, the PACS is back up.",
        )

    await _audit("orthanc.rollback.no_response", admin.username,
                 backup=backup.name)
    raise HTTPException(
        504,
        f"Orthanc is still not answering after {backup.name} was restored. "
        f"The cause therefore lies elsewhere than in the last change. Check "
        f"its logs (docker compose logs orthanc).",
    )


# ============================================================================
# Routes: /api/admin/modalities (declared DICOM devices)
# ============================================================================
#
# DicomModalitiesInDatabase is enforced as true by _validate_orthanc, so a
# device declared here is stored in the database and takes effect at once:
# no restart, and no rewrite of orthanc.json.


@router.get("/api/admin/modalities")
async def list_modalities(admin: AdminUser = Depends(require_admin)):
    """Declared DICOM devices, with their configuration.

    Orthanc only returns the names; each device's configuration takes an
    extra call. We gather them here so the display does not have to chain
    requests.
    """
    r = await _orthanc("GET", "/modalities")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"Orthanc: {r.text[:200]}")

    devices = []
    for name in r.json():
        detail = await _orthanc("GET", f"/modalities/{name}/configuration")
        cfg = detail.json() if detail.status_code == 200 else {}
        devices.append({
            "name": name,
            "aet": cfg.get("AET", ""),
            "host": cfg.get("Host", ""),
            "port": cfg.get("Port", 0),
        })
    devices.sort(key=lambda device: device["name"].lower())
    return {"modalities": devices}


@router.put("/api/admin/modalities/{name}")
async def upsert_modality(
    name: str,
    payload: ModalityPayload,
    admin: AdminUser = Depends(require_admin),
):
    """Declare a device, or update an existing one."""
    if "/" in name or not name.strip():
        raise HTTPException(400, "invalid name")

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
    """Connectivity test (C-ECHO).

    Declaring a device says nothing about whether it answers. This call
    spares having to diagnose, later on, a transfer failing for want of a
    correct address or port.

    A silent device is a result, not a failure: we answer 200 while
    reporting it in the body, so the interface can show it rather than
    presenting it as a server error.
    """
    r = await _orthanc("POST", f"/modalities/{name}/echo", json={})
    reachable = r.status_code == 200
    await _audit(
        "orthanc.modality.echo", admin.username,
        target=name, result="ok" if reachable else "failed",
    )
    return {
        "reachable": reachable,
        "detail": "" if reachable else r.text[:200],
    }


# ============================================================================
# Routes: /api/admin/cf-access
# ============================================================================

@router.get("/api/admin/cf-access")
async def cf_status(admin: AdminUser = Depends(require_admin)):
    """State of the Cloudflare Access verification.

    The service token itself is not settable here. Cloudflare validates it at
    its edge and relays a signed assertion; the origin checks that signature
    against the team's published keys. Rotating a token is done in the
    Cloudflare dashboard, and nothing on this side has to follow.

    What IS settable is what the origin pins: the team domain, the audience,
    and whether verification is enforced at all.
    """
    checks = 0
    try:
        checks = int(await _r().get("cf_access:checks_ok:24h") or 0)
    except (RedisError, ValueError):
        pass

    return {
        "team_domain": _cf_team_domain(),
        "aud_masked": (
            _cf_aud()[:8] + "…" + _cf_aud()[-6:]
            if len(_cf_aud()) > 20 else _cf_aud()
        ),
        "configured": bool(_cf_team_domain() and _cf_aud()),
        "enforced": _cf_enforced(),
        "checks_ok": checks,
    }


class CFAccessPayload(BaseModel):
    """What the origin pins. The service token is not part of it."""
    team_domain: str = Field(default="", max_length=255)
    aud: str = Field(default="", max_length=128)
    enforced: bool = False


@router.put("/api/admin/cf-access")
async def update_cf_access(
    payload: CFAccessPayload,
    admin: AdminUser = Depends(require_admin),
):
    """Pin the team domain, the audience and whether to enforce.

    Written to the settings file and re-read on the next request: no restart,
    and no editing of the compose file, which is the whole point -- these
    three values used to be reachable only by recreating the container.

    Enforcing without both values would answer 503 on every upload, so we
    refuse the combination rather than let the operator lock the endpoint by
    ticking a box.
    """
    team_domain = payload.team_domain.strip()
    aud = payload.aud.strip()

    if team_domain.startswith(("http://", "https://")):
        team_domain = team_domain.split("//", 1)[1].rstrip("/")

    if payload.enforced and not (team_domain and aud):
        raise HTTPException(
            400,
            "enforcing requires both the team domain and the audience: "
            "without them every upload would answer 503",
        )

    _write_setting("cf_access_team_domain", team_domain)
    _write_setting("cf_access_aud", aud)
    _write_setting("cf_access_enforced", payload.enforced)

    await _audit(
        "cf_access.updated", admin.username,
        team_domain=team_domain, enforced=payload.enforced,
    )
    return {"ok": True, "enforced": payload.enforced}


# ============================================================================
# Internal route: verify-cf (called by nginx auth_request)
# ============================================================================

# Cloudflare's signing keys, cached. Refetched when a token carries an unknown
# kid (Cloudflare rotates them) and, failing that, once the TTL has run out.
_jwks_cache: dict[str, Any] = {"keys": {}, "fetched_at": 0.0}
_JWKS_TTL = 3600


async def _cf_signing_key(kid: str):
    """Public key for a kid, fetched from the team's JWKS endpoint.

    The issuer is NOT taken from the token: a forged assertion would simply
    name its own team, whose JWKS would then validate it happily. It comes from
    the pinned team domain, which is the whole point of pinning it.
    """
    import jwt  # noqa: PLC0415 -- optional at import time, see verify_cf

    fresh = (time.time() - _jwks_cache["fetched_at"]) < _JWKS_TTL
    if kid in _jwks_cache["keys"] and fresh:
        return _jwks_cache["keys"][kid]

    url = f"https://{_cf_team_domain()}/cdn-cgi/access/certs"
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(url)
        r.raise_for_status()
        jwks = r.json()

    _jwks_cache["keys"] = {
        k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
        for k in jwks.get("keys", [])
        if k.get("kid")
    }
    _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"].get(kid)


@router.get("/api/internal/verify-cf", include_in_schema=False)
async def verify_cf(
    cf_access_jwt_assertion: str = Header(default=""),
):
    """
    Verify the assertion Cloudflare Access puts on a request it let through.

    Cloudflare consumes the service token at its edge and does NOT relay
    CF-Access-Client-Id / CF-Access-Client-Secret to the origin -- measured on
    this stack, the headers simply never arrive. What it does relay is a signed
    assertion, so that is what gets checked here: RS256 signature against the
    team's published keys, expiry, issuer and audience.

    Pinning both issuer and audience is what makes this worth anything. A token
    signed by any other Cloudflare team would otherwise verify perfectly well
    against that team's own keys.

    Fail closed throughout: anything unexpected answers 403 so nginx refuses
    the upload. 503 is reserved for "not configured", which nginx must never
    reach in the first place -- the auth_request line and the pinned team
    domain belong together.
    """
    if not _cf_team_domain() or not _cf_aud():
        return Response(status_code=503)  # not configured
    if not cf_access_jwt_assertion:
        return Response(status_code=403)

    try:
        import jwt
    except ImportError:
        # The image was built without the dependency: refuse rather than let
        # everything through unverified.
        return Response(status_code=403)

    try:
        kid = jwt.get_unverified_header(cf_access_jwt_assertion).get("kid", "")
        key = await _cf_signing_key(kid)
        if key is None:
            return Response(status_code=403)
        jwt.decode(
            cf_access_jwt_assertion,
            key=key,
            algorithms=["RS256"],
            audience=_cf_aud(),
            issuer=f"https://{_cf_team_domain()}",
        )
    except (jwt.InvalidTokenError, httpx.HTTPError, ValueError, KeyError):
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
        r = await _orthanc("GET", "/system", timeout=3)
        checks["orthanc_api"] = {"ok": r.status_code == 200,
                                 "detail": f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        checks["orthanc_api"] = {"ok": False, "detail": f"HTTPError: {e}"}

    return {"checks": checks}


# ============================================================================
# Routes: /api/admin/session (Authelia session durations)
# ============================================================================

# Authelia durations: a sequence of value+unit, e.g. "15m", "1h", "1h30m".
_DURATION_RE = re.compile(r"^(\d+[smhdwMy])+$")

SESSION_KEYS = {
    "expiration": "Durée maximale d'une session, même active",
    "inactivity": "Déconnexion automatique après cette durée sans activité",
    "remember_me": "Durée de l'option « se souvenir de moi »",
}


class SessionPayload(BaseModel):
    expiration: str | None = None
    inactivity: str | None = None
    remember_me: str | None = None


def _session_block_bounds(lines: list[str]) -> tuple[int, int]:
    """Line range of the top-level `session:` block, end exclusive."""
    start = next(
        (i for i, line in enumerate(lines) if line.rstrip() == "session:"),
        None,
    )
    if start is None:
        raise HTTPException(500, "no top-level 'session:' block in the Authelia configuration")
    for i in range(start + 1, len(lines)):
        line = lines[i]
        # A non-indented, non-blank, non-comment line ends the block.
        if line.strip() and not line.startswith((" ", "\t")) and not line.lstrip().startswith("#"):
            return start, i
    return start, len(lines)


def _read_session_durations() -> dict[str, str | None]:
    try:
        raw = AUTHELIA_CONFIG.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"Authelia configuration unreadable: {e}") from e

    lines = raw.split("\n")
    start, end = _session_block_bounds(lines)
    values: dict[str, str | None] = dict.fromkeys(SESSION_KEYS)
    for line in lines[start + 1:end]:
        for key in SESSION_KEYS:
            m = re.match(rf"^  {key}:\s*(\S+)", line)
            if m:
                values[key] = m.group(1)
    return values


def _patch_session_durations(raw: str, changes: dict[str, str]) -> str:
    """Rewrite the session durations in place, comments and layout untouched.

    Same reasoning as orthanc.json: a yaml.safe_dump round-trip would produce a
    valid file stripped of every comment the administrator wrote. Only the value
    on the matching line is replaced, and only inside the session block -- other
    sections carry keys of the same name.
    """
    lines = raw.split("\n")
    start, end = _session_block_bounds(lines)

    remaining = dict(changes)
    for i in range(start + 1, end):
        for key, value in list(remaining.items()):
            m = re.match(rf"^(  {key}:\s*)(\S+)(.*)$", lines[i])
            if m:
                lines[i] = f"{m.group(1)}{value}{m.group(3)}"
                del remaining[key]
    if remaining:
        raise HTTPException(
            500,
            "keys absent from the session block, nothing written: "
            + ", ".join(sorted(remaining)),
        )
    return "\n".join(lines)


@router.get("/api/admin/session")
async def read_session(admin: AdminUser = Depends(require_admin)):
    """Current session durations, with what each one governs."""
    return {
        "durations": _read_session_durations(),
        "labels": SESSION_KEYS,
        "restart_required": True,
    }


@router.patch("/api/admin/session")
async def update_session(
    payload: SessionPayload,
    admin: AdminUser = Depends(require_admin),
):
    """Change the session durations in Authelia's configuration.

    Authelia only watches its accounts file, never its own configuration, so the
    change takes effect when the container restarts. Said plainly rather than
    implied, for the same reason as the Orthanc tab.
    """
    changes = {k: v for k, v in payload.model_dump().items() if v}
    if not changes:
        raise HTTPException(400, "no duration supplied")
    for key, value in changes.items():
        if not _DURATION_RE.match(value):
            raise HTTPException(
                400,
                f"{key}: '{value}' is not an Authelia duration — a number "
                "followed by s, m, h, d, w, M or y, e.g. 15m or 1h30m",
            )

    lock = FileLock(str(AUTHELIA_CONFIG) + ".lock", timeout=5)
    try:
        with lock:
            raw = AUTHELIA_CONFIG.read_text(encoding="utf-8")
            patched = _patch_session_durations(raw, changes)

            # Guard: the rewrite must still parse, and must carry exactly the
            # requested values, before it reaches the disk.
            try:
                reparsed = yaml.safe_load(patched) or {}
            except yaml.YAMLError as e:
                raise HTTPException(500, f"invalid edit, nothing written: {e}") from e
            session = reparsed.get("session") or {}
            for key, value in changes.items():
                if str(session.get(key)) != value:
                    raise HTTPException(
                        500,
                        f"{key} re-read as {session.get(key)!r} instead of {value!r}, "
                        "nothing written",
                    )

            backup = _backup(AUTHELIA_CONFIG)
            _atomic_write(AUTHELIA_CONFIG, patched)
    except Timeout as e:
        raise HTTPException(423, "Authelia configuration locked, retry") from e
    except OSError as e:
        raise HTTPException(500, f"Authelia configuration unwritable: {e}") from e

    await _audit(
        "authelia.session.updated",
        admin.username,
        fields=",".join(sorted(changes)),
        backup=backup.name,
    )
    return {
        "ok": True,
        "backup": backup.name,
        "applied": False,
        "restart_required": True,
        "detail": (
            "Durations written. Authelia only re-reads its accounts file, not "
            "its own configuration: restart it to apply — "
            "docker compose restart authelia"
        ),
    }


# ============================================================================
# Route: backup rollback
# ============================================================================

def _backup_target(name: str) -> Path | None:
    """The file a backup can be restored onto, None if it is not one of ours.

    Derived from the configured paths rather than from hardcoded file names:
    they are all settable through ADMIN_* variables, and a fixed name would
    silently refuse every restore on an install that renamed them. This is also
    what bounds a restore to the files the panel owns.
    """
    for path in (ORTHANC_JSON, AUTHELIA_YML, AUTHELIA_CONFIG):
        if name.startswith(path.name + ".bak."):
            return path
    return None


@router.get("/api/admin/network")
async def admin_network_get(admin: AdminUser = Depends(require_admin)):
    """Current public URL, and whether it can be changed from here."""
    return {
        "public_url": _read_env_var("PUBLIC_URL"),
        "editable": ENV_FILE.exists(),
    }


class PublicUrlPayload(BaseModel):
    public_url: str = Field(min_length=8, max_length=255)


@router.post("/api/admin/network")
async def admin_network(
    payload: PublicUrlPayload,
    admin: AdminUser = Depends(require_admin),
):
    """Change the public URL, in .env and in Authelia's configuration.

    The domain appears once in .env and eleven times in configuration.yml --
    every access_control rule plus the session cookie block. Changing it by
    hand means getting all of them right; missing one leaves Authelia
    answering 401 on everything, login page included, with nothing in the
    interface able to repair it.

    Takes effect once the stack restarts, and requires logging back in at the
    new address: the session cookie is bound to the previous domain.
    """
    result = await _apply_public_url(payload.public_url, admin.username)
    if not result.get("unchanged"):
        result["restart_required"] = True
    return result


@router.get("/api/admin/audit")
async def read_audit(
    limit: int = 100,
    admin: AdminUser = Depends(require_admin),
):
    """Audit log, most recent event first.

    The stream had been fed since day one but nothing read it: account
    changes, configuration writes and rejected CSRF attempts piled up with no
    way for anyone to consult them. On a PACS that traceability matters
    beyond convenience.
    """
    limit = max(1, min(limit, 500))
    try:
        raw = await _r().xrevrange(AUDIT_STREAM, count=limit)
    except Exception as e:  # noqa: BLE001 - Redis down must not break the panel
        raise HTTPException(503, f"audit log unreadable: {e}") from e

    entries = []
    for identifier, fields in raw:
        # event, actor and ts are always there; the rest depends on the event
        # type (target, changed fields, backup involved) and is grouped so the
        # display does not have to know about them.
        details = {k: v for k, v in fields.items()
                   if k not in ("event", "actor", "ts")}
        entries.append({
            "id": identifier,
            "event": fields.get("event", "?"),
            "actor": fields.get("actor", "?"),
            "ts": int(fields.get("ts", 0) or 0),
            "details": details,
        })

    return {"entries": entries, "count": len(entries)}


@router.post("/api/admin/backups")
async def create_backup(admin: AdminUser = Depends(require_admin)):
    """Deliberate backup of the configuration files.

    Copies were only ever created in reaction to a panel write: taking a
    restore point before a risky operation -- a version upgrade, an edit made
    by hand -- was impossible, although that is precisely when one wants it.
    """
    files = [
        (AUTHELIA_YML, "accounts"),
        (ORTHANC_JSON, "Orthanc configuration"),
        (AUTHELIA_CONFIG, "Authelia configuration"),
    ]

    created, skipped = [], []
    for path, label in files:
        if path and path.exists():
            try:
                dest = _backup(path, tag="manual")
                created.append(dest.name)
            except OSError as e:  # disk full, insufficient rights
                skipped.append(f"{label}: {e}")
        else:
            skipped.append(f"{label}: file missing")

    if not created:
        raise HTTPException(
            500, "no file could be backed up: " + "; ".join(skipped))

    await _audit("backup.created", admin.username, files=",".join(created))
    return {"ok": True, "created": created, "skipped": skipped}


@router.get("/api/admin/backups")
async def list_backups(admin: AdminUser = Depends(require_admin)):
    """List the restorable backups, most recent first.

    Backups were being written on every change but nothing exposed them, so
    from the panel they might as well not have existed. For account backups the
    number of accounts is counted: picking the right one to restore hinges on
    that, not on the file name.
    """
    if not BACKUPS_DIR.exists():
        return {"backups": []}

    items = []
    for path in sorted(BACKUPS_DIR.iterdir(), reverse=True):
        target = _backup_target(path.name)
        if target is None or not path.is_file():
            continue
        entry = {
            "name": path.name,
            "target": target.name,
            "size": path.stat().st_size,
            "modified": int(path.stat().st_mtime),
            "detail": "",
        }
        if target == AUTHELIA_YML:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                users = data.get("users") or {}
                entry["detail"] = f"{len(users)} compte(s) : " + ", ".join(sorted(users))
            except (OSError, yaml.YAMLError) as e:
                entry["detail"] = f"illisible : {e}"
        items.append(entry)

    return {"backups": items}


@router.post("/api/admin/backups/restore")
async def restore_backup(
    backup_name: str,
    admin: AdminUser = Depends(require_admin),
):
    """Restore a backup from /host/backups/ onto its original file."""
    src = BACKUPS_DIR / backup_name
    if not src.exists() or ".bak." not in backup_name:
        raise HTTPException(404, "backup not found or invalid name")

    dest = _backup_target(backup_name)
    if dest is None:
        raise HTTPException(400, "unsupported backup type")

    # The state being replaced is itself backed up first: a restore aimed at the
    # wrong file stays undoable.
    _backup(dest, tag="pre-restore")
    # copyfile writes into the existing inode rather than replacing it, which is
    # what the bind-mounts require -- see _atomic_write.
    shutil.copyfile(src, dest)

    restart_required = False
    if dest == ORTHANC_JSON:
        if ORTHANC_APPLY_MODE == "reset":
            await _reload_orthanc()
        else:
            restart_required = True

    await _audit("backup.restored", admin.username, backup=backup_name)
    return {"ok": True, "target": dest.name, "restart_required": restart_required}
