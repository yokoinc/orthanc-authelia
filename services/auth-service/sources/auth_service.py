from collections.abc import Mapping
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import secrets
import uuid
import time
import datetime
import json
import redis
import os
import logging
import re
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

import i18n

app = FastAPI(title="PACS Auth Service", description="Authentication and token management for PACS")
security = HTTPBasic()


# Labels of the menu injected into Orthanc Explorer 2 (oe2-menu.js), in the
# installation's language. Declared BEFORE the /static mount: a route added
# afterwards would be shadowed by it. Public like the rest of /auth/static/ --
# three labels and a language code, nothing sensitive -- and not cached, so
# that a language change in the panel shows on reload.
@app.get("/static/oe2-menu-i18n.json")
def oe2_menu_i18n():
    langue = _langue()
    return JSONResponse(
        {"langue": langue, "textes": i18n.section("oe2", langue)},
        headers={"Cache-Control": "no-store"},
    )


# Mount static files
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# Token configuration
DEFAULT_TOKEN_MAX_USES = int(os.getenv("DEFAULT_TOKEN_MAX_USES", "50"))

# Grace period granted to a token whose opening quota has just run out. Without
# it, the last opening would delete the token and the viewer that has just
# appeared would lose its images halfway. Two hours: enough to review an exam
# without rushing, short enough that an exhausted link does not serve all day.
SURSIS_DERNIERE_OUVERTURE = int(os.getenv("SURSIS_DERNIERE_OUVERTURE", "7200"))
DEFAULT_TOKEN_VALIDITY_SECONDS = int(os.getenv("DEFAULT_TOKEN_VALIDITY_SECONDS", str(7 * 24 * 3600)))  # 7 days
CACHE_VALIDITY_USER_SESSION = int(os.getenv("CACHE_VALIDITY_USER_SESSION", "300"))  # 5 minutes  
CACHE_VALIDITY_SHARE_TOKEN = int(os.getenv("CACHE_VALIDITY_SHARE_TOKEN", "60"))    # 1 minute

# Audit configuration
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))  # 90 days
UNLIMITED_TOKEN_DURATION = int(os.getenv("UNLIMITED_TOKEN_DURATION", str(365 * 24 * 3600)))  # 1 year

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("auth-service")

# Language configuration
LANGUAGE = os.getenv("LANGUAGE", "en")

# Orthanc API configuration (for patient name resolution)
ORTHANC_API_URL = os.getenv("ORTHANC_API_URL", "http://orthanc:8042").rstrip("/")
ORTHANC_API_TIMEOUT = float(os.getenv("ORTHANC_API_TIMEOUT", "3"))
PATIENT_NAME_CACHE_TTL = int(os.getenv("PATIENT_NAME_CACHE_TTL", "300"))  # 5 minutes
_resource_info_cache = {}  # {key: (info_dict, timestamp)}


# Orthanc is not governed by its HTTP credentials but by its authorization
# plugin: even with ORTHANC_ADMIN_USER/PASS, a GET /studies answers 403
# (measured on 2026-08-29). The plugin does however accept a token in the
# `auth-token` header (TokenHttpHeaders in orthanc.json), which it then sends
# back to us for validation: the value "admin" opens the administrator profile.
#
# Without this header, EVERY call from this module silently got a 403 --
# _orthanc_get returned None and the caller made do with an empty value. That
# is why the share manager showed no patient name: the lookup failed, without a
# word in the logs.
#
# Harmless from the Internet: nginx runs auth_request BEFORE proxying, and a
# client that injects auth-token / X-Auth-User / Remote-User itself is
# redirected to authentication (checked on all four headers). This value is
# only used between containers, on the closed Docker network.
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "admin")
ORTHANC_INTERNAL_TOKEN = ADMIN_GROUP


def _orthanc_get(path):
    """GET helper for Orthanc REST API."""
    url = f"{ORTHANC_API_URL}{path}"
    try:
        req = urllib.request.Request(url)
        req.add_header("auth-token", ORTHANC_INTERNAL_TOKEN)
        with urllib.request.urlopen(req, timeout=ORTHANC_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as err:
        logger.debug(f"Orthanc GET {path} failed: {err}")
        return None


def _orthanc_post(path, body):
    """POST helper for Orthanc REST API."""
    url = f"{ORTHANC_API_URL}{path}"
    try:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "auth-token": ORTHANC_INTERNAL_TOKEN},  # cf. _orthanc_get
        )
        with urllib.request.urlopen(req, timeout=ORTHANC_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as err:
        logger.debug(f"Orthanc POST {path} failed: {err}")
        return None


def _format_patient_name(raw):
    """Keep DICOM format LAST^FIRST^MIDDLE (trim trailing empty components)."""
    if not raw or not isinstance(raw, str):
        return None
    parts = raw.split("^")
    while parts and not parts[-1].strip():
        parts.pop()
    cleaned = "^".join(p.strip() for p in parts)
    return cleaned or None


def _format_study_date(raw):
    """DICOM StudyDate is YYYYMMDD. Return YYYY-MM-DD or None."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or None


def _collect_modalities(study_info):
    """Return a comma-separated list of modalities present in the study."""
    mods = study_info.get("RequestedTags", {}).get("ModalitiesInStudy")
    if mods:
        return mods
    series_mods = study_info.get("ModalitiesInStudy") or []
    if isinstance(series_mods, list) and series_mods:
        return "/".join(sorted(set(series_mods)))
    return None


def resolve_resource_info(resource):
    """Resolve a token resource to {patient_name, study_date, study_description, modality}."""
    empty = {"patient_name": None, "study_date": None,
             "study_description": None, "modality": None}
    if not isinstance(resource, dict):
        return empty
    dicom_uid = resource.get("DicomUid")
    orthanc_id = resource.get("OrthancId")
    level = (resource.get("Level") or "study").lower()
    cache_key = f"{level}:{orthanc_id or dicom_uid}"
    if not cache_key or cache_key == "study:None":
        return empty
    now = time.time()
    cached = _resource_info_cache.get(cache_key)
    if cached and (now - cached[1]) < PATIENT_NAME_CACHE_TTL:
        return cached[0]

    info = dict(empty)
    try:
        # Resolve to an Orthanc study id if needed
        study_id = None
        if level == "study" and orthanc_id:
            study_id = orthanc_id
        elif level == "study" and dicom_uid:
            lookup = _orthanc_post("/tools/lookup", dicom_uid)
            if isinstance(lookup, list):
                for item in lookup:
                    if item.get("Type") == "Study":
                        study_id = item.get("ID")
                        break
        elif level in ("series", "instance"):
            target_id = orthanc_id
            if not target_id and dicom_uid:
                lookup = _orthanc_post("/tools/lookup", dicom_uid)
                if isinstance(lookup, list) and lookup:
                    target_id = lookup[0].get("ID")
            if target_id:
                sub = _orthanc_get(f"/{level}s/{target_id}")
                if sub:
                    study_id = sub.get("ParentStudy")
        elif level == "patient":
            patient_id = orthanc_id
            if not patient_id and dicom_uid:
                lookup = _orthanc_post("/tools/lookup", dicom_uid)
                if isinstance(lookup, list) and lookup:
                    patient_id = lookup[0].get("ID")
            if patient_id:
                pat = _orthanc_get(f"/patients/{patient_id}")
                if pat:
                    info["patient_name"] = _format_patient_name(
                        pat.get("MainDicomTags", {}).get("PatientName"))

        if study_id:
            # ?requestedTags: Orthanc only returns ModalitiesInStudy when
            # ASKED. Without this parameter the field is simply absent, and
            # _collect_modalities therefore always returned None -- the share
            # manager has never shown a single modality since it existed.
            # Measured on 2026-08-29: without the parameter, RequestedTags is
            # None; with it, it is {"ModalitiesInStudy": "MR"}.
            study = _orthanc_get(
                f"/studies/{study_id}?requestedTags=ModalitiesInStudy")
            if study:
                tags = study.get("MainDicomTags", {}) or {}
                patient_tags = study.get("PatientMainDicomTags", {}) or {}
                if not info["patient_name"]:
                    info["patient_name"] = _format_patient_name(
                        patient_tags.get("PatientName"))
                info["study_date"] = _format_study_date(tags.get("StudyDate"))
                info["study_description"] = (tags.get("StudyDescription") or "").strip() or None
                info["modality"] = _collect_modalities(study)
    except Exception as err:
        logger.debug(f"Resource info resolution failed for {cache_key}: {err}")

    _resource_info_cache[cache_key] = (info, now)
    return info


# Backward-compat helper
def resolve_patient_name(resource):
    return resolve_resource_info(resource).get("patient_name")

# Asset version for cache-busting static files (auto-updates on each container start)
ASSET_VERSION = os.getenv("ASSET_VERSION", str(int(time.time())))
# Semantic version of the image, shown in the footer. Independent of the
# ASSET_VERSION cache-buster, which is a Unix timestamp.
IMAGE_VERSION = os.getenv("IMAGE_VERSION", "dev")

def _langue() -> str:
    """The installation's language, read on every call.

    It used to be frozen at startup from LANGUAGE: changing language meant
    recreating the container, and the share page ignored the setting that the
    wizard and the panel save. admin_module is authoritative; if it could not
    be loaded, LANGUAGE, then English.
    """
    try:
        return admin_module.langue_courante()
    except Exception:  # noqa: BLE001 - admin_module absent ou reglages illisibles
        code = i18n.normaliser(LANGUAGE)
        return code if code in i18n.langues_disponibles() else i18n.LANGUE_REPLI


def _msg(cle: str, **variables) -> str:
    """API message (« api » section), in the installation's language."""
    return i18n.texte("api", cle, _langue(), **variables)


class _CatalogueCourant(Mapping):
    """TRANSLATIONS["ui"][...] resolved in the language in force on EVERY access.

    The existing code indexes a dictionary loaded once and for all; this object
    keeps the same way of access, without freezing the language. Keys missing
    from a translation fall back to English (i18n.section).
    """
    SECTIONS = ("ui", "js")

    def __getitem__(self, section):
        return i18n.section(section, _langue())

    def __iter__(self):
        return iter(self.SECTIONS)

    def __len__(self):
        return len(self.SECTIONS)


TRANSLATIONS = _CatalogueCourant()


class _MessagesUI(Mapping):
    """UI_MESSAGES[...]: same keys as before, resolved on the fly."""
    CLES = {
        "INVALID_TOKEN": "invalid_token",
        "EXPIRED_TOKEN": "expired_token",
        "NO_STUDY": "no_study",
        "INVALID_STUDY": "invalid_study",
        "USAGE_LIMIT": "usage_limit",
    }

    def __getitem__(self, cle):
        return TRANSLATIONS["ui"][self.CLES[cle]]

    def __iter__(self):
        return iter(self.CLES)

    def __len__(self):
        return len(self.CLES)


UI_MESSAGES = _MessagesUI()

# Configuration CDN
FONT_AWESOME_CDN = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"

# Configuration JavaScript
JS_CONFIG = {
    "REFRESH_INTERVAL": int(os.getenv("JS_REFRESH_INTERVAL", "30000")),
    "API_BASE": os.getenv("JS_API_BASE", ""),  # Empty = use window.location.origin
    "DEBUG_MODE": os.getenv("JS_DEBUG_MODE", "false").lower() == "true"
}

VALID_USERS = {
    os.getenv("AUTH_USERNAME", "share-user"): os.getenv("AUTH_PASSWORD", "change-me")
}

USER_ROLES = {
    "admin": "admin-role",
    "doctor": "doctor-role",
    "external": "external-role"
}

# Redis connection
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

# ============================================================================
# Admin/setup panel (feat/admin-setup-panel — WIP)
# ============================================================================
# admin_module uses an async Redis client (aioredis) because its endpoints
# are async. It is initialised separately, sharing the same Redis database.
# The module exposes: router, setup_gate, csrf_gate, set_redis.
try:
    import redis.asyncio as aioredis
    import admin_module

    _admin_redis = aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True,
    )
    admin_module.set_redis(_admin_redis)
    app.include_router(admin_module.router)
    app.middleware("http")(admin_module.setup_gate)
    app.middleware("http")(admin_module.csrf_gate)
    logging.info("admin_module loaded — /auth/setup and /auth/admin routes active")
except ImportError as e:
    logging.warning(f"admin_module not loaded: {e} — admin routes will not be available")

def store_token(token: str, token_data: dict):
    """Store token in Redis with expiration"""
    expiration_time = int(token_data["expires_at"] - time.time())
    if expiration_time > 0:
        redis_client.setex(f"token:{token}", expiration_time, json.dumps(token_data))

def get_token(token: str) -> dict:
    """Get token from Redis"""
    data = redis_client.get(f"token:{token}")
    if data:
        return json.loads(data)
    return None

def delete_token(token: str):
    """Delete token from Redis"""
    redis_client.delete(f"token:{token}")

def increment_token_usage(token: str) -> bool:
    """Count one opening of the link. Returns False when the quota is reached.

    Called ONLY from share_redirect: one increment = one opening of the share
    link. Certainly not from protocol validation, which fires hundreds of times
    per viewing.
    """
    data = get_token(token)
    if not data:
        return False

    # The quota is checked BEFORE counting, and the use that reaches the
    # ceiling is granted. The code tested `current_uses >= max_uses` AFTER
    # incrementing: a share created for a single opening granted none, and
    # every quota was one short (measured: max_uses=1 -> 0 openings, max_uses=3
    # -> 2).
    utilisees = data.get("current_uses", 0)
    plafond = data.get("max_uses", 999999)
    if utilisees >= plafond:
        # Refuse, without deleting. Deleting here would wipe out the grace
        # period granted below: clicking an exhausted link again was enough to
        # cut the images of the person currently viewing. It is the shortened
        # lifetime that makes the token disappear, and that alone.
        return False

    data["current_uses"] = utilisees + 1

    restant = int(data["expires_at"] - time.time())
    if restant <= 0:
        delete_token(token)
        return False

    # The ceiling has just been reached: this was the last opening. The token
    # is NOT deleted right away -- the viewer that has just opened needs it for
    # the whole viewing, and the images would freeze in front of the colleague.
    # Its lifetime is shortened to a grace period, time enough to look at the
    # exam, then it disappears.
    if data["current_uses"] >= plafond:
        restant = min(restant, SURSIS_DERNIERE_OUVERTURE)

    redis_client.setex(f"token:{token}", restant, json.dumps(data))
    return True

def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic authentication"""
    correct_password = VALID_USERS.get(credentials.username)
    if not correct_password or not secrets.compare_digest(credentials.password, correct_password):
        raise HTTPException(status_code=401, detail=_msg("invalid_credentials"))
    return credentials.username

def verify_admin_auth(request: Request):
    """Verify admin authentication from Authelia headers"""
    remote_user = request.headers.get("Remote-User", "")
    remote_groups = request.headers.get("Remote-Groups", "")

    # NEVER log the headers wholesale. The previous line was `logger.info(f"All
    # headers: {dict(request.headers)}")`: it wrote the authelia_session cookie
    # IN CLEAR into the container logs, on every call to an administration
    # route. Anyone reading `docker logs` -- or the NAS log files, or a backup
    # of them -- found a valid session cookie there and could impersonate the
    # administrator. Checked on 2026-08-29: the cookie appeared as is.
    logger.debug("Controle admin : %s [%s]", remote_user, remote_groups)

    # EXACT comparison, on the comma-separated list Authelia produces. The test
    # was `"admin" not in remote_groups`, a substring search: a group named
    # "nonadmin", "badmin" or "admins" would have been enough to open
    # administration. No existing group falls into the trap today -- it closes
    # the day one is added.
    groupes = {g.strip() for g in remote_groups.split(",") if g.strip()}
    if ADMIN_GROUP not in groupes:
        raise HTTPException(status_code=403, detail=_msg("admin_access_required"))
    return remote_user or "unknown"

def normalize_bearer_token(token_value: str) -> str:
    """Remove Bearer prefix if present"""
    return token_value[7:] if token_value.startswith("Bearer ") else token_value

def get_base_url(request: Request) -> str:
    """Get base URL from request headers"""
    host = request.headers.get("Host", "localhost")
    scheme = "https" if request.headers.get("X-Forwarded-Proto") == "https" else "http"
    return f"{scheme}://{host}"

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

def render_template(template_name: str, **kwargs) -> str:
    """Render HTML template with provided variables.

    A single regex pass that matches `{word}` and replaces it with the value of
    the matching kwarg. When no kwarg matches, the placeholder is left as is
    (useful for the `{js_config}` blocks that contain JSON). No cascade => no
    risk that a substituted value contains a placeholder that would be
    substituted again on the next round.
    """
    template_path = f"/app/templates/{template_name}"
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        kwargs["font_awesome_cdn"] = FONT_AWESOME_CDN
        # The pages' lang attribute: it was hard-coded "fr", whatever the
        # language of the texts -- screen readers and spell checkers rely on
        # it.
        kwargs.setdefault("lang", _langue())
        kwargs.setdefault("asset_version", ASSET_VERSION)
        kwargs.setdefault("image_version", IMAGE_VERSION)

        def _sub(match):
            key = match.group(1)
            return str(kwargs[key]) if key in kwargs else match.group(0)

        return _PLACEHOLDER_RE.sub(_sub, template_content)
    except FileNotFoundError:
        logger.error(f"Template not found: {template_path}")
        return f"<html><body><h1>Template Error</h1><p>Template not found: {template_name}</p></body></html>"
    except Exception as e:
        logger.error(f"Template rendering error: {e}")
        return f"<html><body><h1>Template Error</h1><p>Error rendering template: {e}</p></body></html>"

def render_error_template(title: str, message: str, icon_class: str, status_code: int = 400) -> HTMLResponse:
    """Render error template using external template"""
    content = render_template("error.html",
                             title=title,
                             message=message,
                             icon_class=icon_class,
                             extra_content="")
    return HTMLResponse(content=content, status_code=status_code)

def render_access_denied_template(message: str = None, back_link: str = "") -> HTMLResponse:
    """Render access denied template"""
    if message is None:
        message = TRANSLATIONS["ui"]["access_denied_message"]

    back_link_html = f'<a href="{back_link}" class="oe2-centered__link">{TRANSLATIONS["ui"]["back_to_pacs"]}</a>' if back_link else ""
    content = render_template("access_denied.html",
                             access_denied_title=TRANSLATIONS["ui"]["access_denied_title"],
                             message=message,
                             back_link=back_link_html)
    return HTMLResponse(content=content, status_code=403)

def render_file_not_found_template(title: str, message: str) -> HTMLResponse:
    """Render file not found template"""
    content = render_template("error.html",
                             title=title,
                             message=message,
                             icon_class="fas fa-exclamation-triangle",
                             extra_content="")
    return HTMLResponse(content=content, status_code=404)

@app.get("/settings/roles")
def get_settings_roles(username: str = Depends(verify_basic_auth)):
    # Return roles and permissions adapted to our PACS environment
    # OHIF, VolView, Explorer 2 - no Osimis
    return {
        "roles": [
            "admin-role",
            "doctor-role", 
            "external-role"
        ],
        "permissions": [
            "view",           # Read access to studies/series/instances
            "download",       # Download DICOM files
            "upload",         # Upload new DICOM files
            "delete",         # Delete studies/series/instances
            "modify",         # Modify DICOM tags
            "anonymize",      # Anonymize DICOM data
            "share",          # Create share links (Explorer 2)
            "send",           # Send to modalities/peers
            "edit-labels",    # Edit study/series labels
            "settings"        # System settings access
        ],
        "available-viewers": [
            "ohif-viewer-publication",
            "stone-viewer-publication",
            "volview-viewer-publication",
            "viewer-instant-link"
        ],
        "default-viewer": "ohif-viewer-publication",
        "share-durations": [0, 7, 15, 30, 90, 365],
        "default-share-duration": 15
    }

@app.post("/tokens/validate")
async def validate_token(request: Request, username: str = Depends(verify_basic_auth)):
    body = await request.json()
    
    token_value = normalize_bearer_token(body.get("token-value", ""))
    level = body.get("level", "")
    method = body.get("method", "")
    orthanc_id = body.get("orthanc-id", "")
    dicom_uid = body.get("dicom-uid", "")
    uri = body.get("uri", "")
    
    # Log the validation request
    logger.debug(f"Token validation request: {body}")
    logger.debug(f"Token value: {token_value}")
    logger.debug(f"Level: {level}, Method: {method}, URI: {uri}")
    logger.debug(f"Orthanc ID: {orthanc_id}, DICOM UID: {dicom_uid}")
    
    # Check user session tokens (mapped from nginx groups)
    if token_value in USER_ROLES:
        role = USER_ROLES[token_value]
        granted = check_permission_for_role(role, level, method, uri)
        return JSONResponse(content={
            "granted": granted,
            "validity": CACHE_VALIDITY_USER_SESSION
        })
    
    # Check generated share tokens in Redis
    token_data = get_token(token_value)
    if token_data:
        # Check if token has expired (Redis auto-expires, but double-check)
        if time.time() >= token_data["expires_at"]:
            delete_token(token_value)
            return JSONResponse(content={
                "granted": False,
                "validity": 0
            })
        
        # Quota CHECKED, not consumed.
        #
        # This endpoint is called by the Orthanc plugin for every resource, and
        # it revalidates every 60 s (CACHE_VALIDITY_SHARE_TOKEN). A study means
        # hundreds of series and instances: a 50-use token died in the middle
        # of a viewing, sometimes within seconds. The colleague saw the images
        # freeze with no explanation.
        #
        # The count only makes sense relative to an OPENING of the link, and it
        # already happens there, in share_redirect.
        #
        # No ceiling test here either: the last authorised opening brings
        # current_uses TO max_uses, and refusing in that case would cut the
        # images of the viewing that has just been authorised. It is the
        # token's lifetime that enforces the limit -- share_redirect brings it
        # down to SURSIS_DERNIERE_OUVERTURE as soon as the ceiling is reached,
        # after which the token disappears by itself and get_token no longer
        # finds it.
        
        # For share tokens, check if the requested resource matches the token's resources
        granted = check_resource_access(token_data, level, method, orthanc_id, dicom_uid, uri)
        
        return JSONResponse(content={
            "granted": granted,
            "validity": CACHE_VALIDITY_SHARE_TOKEN
        })
    
    # Token not found
    return JSONResponse(content={
        "granted": False,
        "validity": 0
    })

def check_permission_for_role(role: str, level: str, method: str, uri: str) -> bool:
    """Check if a role has permission for the requested action"""
    if role == "admin-role":
        return True  # Admin can do everything
    elif role == "doctor-role":
        # Doctors can read, upload, share but not delete/modify system
        #
        # "system" was in the same list as patient/study/series/instance, POST
        # included: this function therefore said yes to POST /tools/reset,
        # /tools/shutdown and /tools/execute-script -- restart, shut down, and
        # run Lua. Those paths are indeed publicly exposed (nginx routes them,
        # line ~650). It was not exploitable in practice: the doctor's profile
        # does not carry the permission the plugin requires for those
        # endpoints, and Orthanc answers 403 -- measured on 2026-08-29. But an
        # authorisation must not depend on a refusal further down the line: the
        # day a permission is added to the profile, the hole opens without
        # anyone rereading this line.
        #
        # For reads, the system level remains necessary: the viewers query
        # /system and /plugins at startup.
        if method == "get" and level == "system":
            return True
        if method in ("get", "post") and level in (
                "patient", "study", "series", "instance"):
            return True
        if method == "put" and "tokens" in (uri or ""):  # Allow token creation for sharing
            return True
        return False
    elif role == "external-role":
        # Read-only, but REAL reading.
        #
        # The "system" level was missing from this list. Yet a LIST request
        # names no specific resource: the plugin maps it to the system level,
        # not the study level. An external account therefore signed in, opened
        # Explorer -- and got 403 on /studies: an explorer opened on an empty
        # list, which means nothing. Measured on 2026-08-29 with a real
        # external account.
        #
        # The doctor already had this level, for GET as well as POST. The
        # external account only gets it for GET: it views and downloads
        # (permissions ["view", "download"] in its profile), it writes nothing.
        return method == "get" and level in [
            "patient", "study", "series", "instance", "system",
        ]

    return False

def check_resource_access(token_data: dict, level: str, method: str, orthanc_id: str, dicom_uid: str, uri: str) -> bool:
    """Check if a share token allows access to the requested resource"""
    # Share tokens are read-only
    if method != "get":
        return False
    
    # System level access not allowed for share tokens (except specific URIs)
    if level == "system":
        # Allow some system URIs needed for viewers
        allowed_system_uris = ["/system", "/plugins", "/dicom-web/servers"]
        return any(allowed_uri in (uri or "") for allowed_uri in allowed_system_uris)
    
    # Check if the requested resource is covered by this token
    token_resources = token_data.get("resources", [])
    
    for resource in token_resources:
        token_orthanc_id = resource.get("OrthancId", resource.get("orthanc-id", ""))
        token_dicom_uid = resource.get("DicomUid", resource.get("dicom-uid", ""))
        token_level = resource.get("Level", resource.get("level", ""))

        # Exact match -- with BOTH sides non-empty.
        #
        # The test was `orthanc_id == token_orthanc_id or dicom_uid ==
        # token_dicom_uid`. When both values were missing, "" == "" was true
        # and access was granted: a request where the plugin does not identify
        # the resource was enough to get through.
        if token_orthanc_id and orthanc_id == token_orthanc_id:
            return True
        if token_dicom_uid and dicom_uid == token_dicom_uid:
            return True

        # Hierarchical access: a STUDY token covers its series and its
        # instances -- ITS OWN.
        #
        # The previous code answered `return True` to any series- or
        # instance-level request as soon as the token was study-level, with
        # this comment: "We'd need to query Orthanc to check hierarchy, for now
        # allow it". In other words, a share link valid for one study gave
        # access to EVERY series and EVERY instance on the server -- 209
        # studies, not one. Same family as the hole closed on 2026-08-27: the
        # chain trusted a match it did not check.
        #
        # So Orthanc is asked which study the resource really belongs to. The
        # result is cached: an instance's parentage never changes.
        if token_level == "study" and level in ("series", "instance"):
            if _appartient_a_etude(level, orthanc_id, token_orthanc_id, token_dicom_uid):
                return True
        elif token_level == "series" and level == "instance":
            if _appartient_a_serie(orthanc_id, token_orthanc_id):
                return True

    return False


# Parentage: 24 h cache. An instance never changes series, nor a series study
# -- only a deletion makes them disappear, and the request then fails by
# itself.
_PARENT_CACHE_TTL = 86400
_parent_cache: dict = {}


def _parent_orthanc(level: str, orthanc_id: str) -> dict | None:
    """Return the Orthanc record of a series or an instance, cached."""
    if not orthanc_id:
        return None
    cle = (level, orthanc_id)
    entree = _parent_cache.get(cle)
    now = time.time()
    if entree and now - entree[1] < _PARENT_CACHE_TTL:
        return entree[0]
    chemin = {"series": "/series/", "instance": "/instances/"}.get(level)
    if not chemin:
        return None
    fiche = _orthanc_get(f"{chemin}{orthanc_id}")
    if fiche is not None:
        _parent_cache[cle] = (fiche, now)
    return fiche


def _appartient_a_etude(level: str, orthanc_id: str,
                        etude_orthanc_id: str, etude_dicom_uid: str) -> bool:
    """Does the requested series / instance really belong to this study?

    Refuse when Orthanc does not answer: better a share that fails than a share
    that opens the whole server.
    """
    fiche = _parent_orthanc(level, orthanc_id)
    if not fiche:
        return False

    if level == "instance":
        serie = fiche.get("ParentSeries")
        fiche = _parent_orthanc("series", serie) if serie else None
        if not fiche:
            return False

    if etude_orthanc_id and fiche.get("ParentStudy") == etude_orthanc_id:
        return True
    if etude_dicom_uid:
        etude = _orthanc_get(f"/studies/{fiche.get('ParentStudy')}")
        if etude and etude.get("MainDicomTags", {}).get(
                "StudyInstanceUID") == etude_dicom_uid:
            return True
    return False


def _appartient_a_serie(orthanc_id: str, serie_orthanc_id: str) -> bool:
    """Does the requested instance really belong to this series?"""
    if not serie_orthanc_id:
        return False
    fiche = _parent_orthanc("instance", orthanc_id)
    return bool(fiche) and fiche.get("ParentSeries") == serie_orthanc_id

@app.post("/user/get-profile")
async def get_user_profile(request: Request, username: str = Depends(verify_basic_auth)):
    body = await request.json()

    # NB: the body also carries "server-id" (the calling Orthanc instance, for
    # multi-site setups). Single instance here -> we don't need it; body.get()
    # simply ignores the extra field.

    # token-value = the Authelia group injected by nginx (Remote-Groups), OR
    # empty/absent for an ANONYMOUS request. Since Authorization plugin v0.10.0,
    # the plugin calls /user/get-profile even without a token, so we MUST always
    # return a profile (never 401), including the anonymous case.
    group = normalize_bearer_token(body.get("token-value", "") or "")

    # --- Anonymous (no token) : upload-only -------------------------------
    # No user identity -> grant ONLY 'upload'. This is what authorizes the
    # programmatic DICOM import endpoint (/api-upload/), which is gated upstream
    # by Cloudflare Access + nginx Basic auth and reaches Orthanc WITHOUT a user
    # token. The plugin then asks for the anonymous profile and we allow the
    # upload while denying every read / list / delete / share.
    if not group:
        # authorized-labels: ["*"] (NOT [] as in the reference). Empirically the
        # Authorization plugin in orthancteam/orthanc:26.4.x denies POST
        # /instances for an anonymous profile with an empty labels array, even
        # though the permission pattern (`post ^/instances$ - all|upload`) is
        # satisfied by the "upload" permission. With ["*"] (full label scope)
        # the upload is granted. Safe in practice because (a) the only path
        # that reaches Orthanc anonymously is /api-upload/ which is gated by
        # CF Access + nginx Basic auth, and (b) the only permission granted is
        # "upload" — no read/list/delete/share is possible.
        return JSONResponse(content={
            "name": "Anonymous",
            "user-id": None,
            # [] and NOT ["*"]. The label scope governs ENUMERATION,
            # independently of permissions: with ["*"], an anonymous client
            # with no read right at all still got the complete list of studies
            # through /dicom-web/studies -- patient names, dates, descriptions.
            # Checked exploitable from the Internet on 2026-08-27, 209 studies
            # exposed.
            #
            # The original comment justified ["*"] with: "the only path that
            # reaches Orthanc anonymously is /api-upload/, protected by
            # Cloudflare Access". The assumption was wrong: Authelia's bypass
            # rules (^/dicom-web.*token=.*$ and its four siblings) fire on the
            # mere presence of "token=" in the URL and open a second anonymous
            # path, that one without any guard.
            "authorized-labels": [],
            "permissions": ["upload"],
            "groups": [],
            "validity": CACHE_VALIDITY_USER_SESSION
        })

    # --- Authenticated user : map Authelia group -> permissions ------------
    if "admin" in group:
        user_name = TRANSLATIONS["ui"]["administrator"]
        permissions = ["view", "download", "upload", "delete", "modify", "anonymize", "share", "send", "settings", "edit-labels"]
    elif "doctor" in group:
        user_name = TRANSLATIONS["ui"]["doctor"]
        permissions = ["view", "download", "upload", "share", "send", "edit-labels"]
    elif "external" in group:
        # The external group MUST be handled here, explicitly.
        #
        # It was not, and it was collateral damage from the 2026-08-27 fix:
        # that day the `else` below was hardened to close the "token=anything"
        # hole, by making any unrecognised value fall back to the anonymous
        # profile. Yet "external" is a perfectly legitimate Authelia group, and
        # it fell into that same `else`: get_token("external") finds nothing,
        # and the user inherited the anonymous profile -- permissions
        # ["upload"], authorized-labels [], hence no reading.
        #
        # Consequence measured on 2026-08-29 with a real external account: it
        # signed in, Explorer opened, and /studies as well as /patients
        # answered 403. An explorer on an empty list.
        #
        # Nobody had noticed because the role was not in use. That is exactly
        # what a security fix can break silently: the regression only shows on
        # the path nobody ever takes.
        user_name = TRANSLATIONS["ui"]["external_user"]
        permissions = ["view", "download"]
    else:
        # This branch received EVERY unrecognised value and granted it view +
        # download on "authorized-labels": ["*"]. It was a hole anyone could
        # exploit, without an account:
        #
        #   GET /dicom-web/studies?token=anything   -> 200, 209 studies
        #
        # The full path: Authelia's `bypass` rules fire on the mere presence of
        # "token=" in the URL (^/dicom-web.*token=.*$ and its four siblings);
        # Orthanc then calls /user/get-profile with the parameter's value; and
        # this `else` took it for a legitimate external user. Checked
        # exploitable from the Internet on 2026-08-27.
        #
        # The only legitimate values here are share tokens, issued by this
        # service and kept in Redis. Everything else must fall back to the
        # anonymous profile -- upload allowed, no reading.
        jeton = get_token(group)
        if not jeton or time.time() >= jeton.get("expires_at", 0):
            logger.warning(
                "Profile refused: unknown or expired token (%s...)", group[:8]
            )
            return JSONResponse(content={
                "name": "Anonymous",
                "user-id": None,
                "authorized-labels": ["*"],
                "permissions": ["upload"],
                "groups": [],
                "validity": CACHE_VALIDITY_SHARE_TOKEN
            })
        user_name = TRANSLATIONS["ui"]["external_user"]
        permissions = ["view", "download"]

    return JSONResponse(content={
        "name": user_name,
        "user-id": group,                 # wire key is 'user-id' (NOT 'id')
        "authorized-labels": ["*"],       # access to all labels
        "permissions": permissions,
        "groups": [group],
        "validity": CACHE_VALIDITY_USER_SESSION
    })

@app.post("/tokens/decode")
async def decode_token(request: Request):
    body = await request.json()
    
    # token-key: the Orthanc plugin sends the NAME of the parameter where it
    # found the token (e.g. "token"). Unused here, so it is not read.
    token_value = normalize_bearer_token(body.get("token-value", ""))
    
    # Check if token exists and is valid in Redis
    token_data = get_token(token_value)
    if not token_data:
        return JSONResponse(content={
            "error-code": "unknown"
        })
    
    # Check if token has expired
    if time.time() >= token_data["expires_at"]:
        # Remove expired token
        delete_token(token_value)
        return JSONResponse(content={
            "error-code": "expired"
        })
    
    # Get the first resource (usually there's only one for shares)
    resources = token_data.get("resources", [])
    if not resources:
        return JSONResponse(content={
            "error-code": "invalid"
        })
    
    # Only the DICOM UID is used here to build the viewer URL, and it has
    # already been extracted above. The token's OrthancId and Level are not
    # used at this level: the scope is enforced further on, by
    # check_resource_access.
    token_type = token_data.get("token_type", "")
    
    # Generate redirect URL - always use /share/ route for token handling
    base_url = get_base_url(request)
    redirect_url = f"{base_url}/share/?token={token_value}"

    # Expose the token's resources (plugin >=v0.9.3 uses this to filter
    # DICOMweb prior-studies in OHIF). Normalized to the wire key names.
    decoded_resources = [
        {
            "dicom-uid": r.get("DicomUid", r.get("dicom-uid", "")),
            "orthanc-id": r.get("OrthancId", r.get("orthanc-id", "")),
            "level": r.get("Level", r.get("level", "study"))
        }
        for r in resources
    ]

    return JSONResponse(content={
        "token-type": token_type,
        "redirect-url": redirect_url,
        "resources": decoded_resources
    })

@app.post("/tokens/{token_type}")
@app.put("/tokens/{token_type}")
async def create_token(token_type: str, request: Request):
    # Check Authelia authentication headers
    remote_user = request.headers.get("Remote-User")
    remote_groups = request.headers.get("Remote-Groups")
    
    if not remote_user or not remote_groups:
        raise HTTPException(status_code=401, detail=_msg("auth_required"))

    # Who may share: only admin and doctor share.
    #
    # The route only required a session, whatever it was. Explorer 2 does hide
    # the button from external accounts -- their profile does not carry the
    # "share" permission -- but hiding a button is not an authorisation: the
    # direct call was still accepted, and a "view only" account could therefore
    # issue a public share link to a study.
    #
    # Exact comparison on the comma-separated list, never by substring:
    # "nondoctor" must not pass for a doctor.
    groupes = {g.strip() for g in remote_groups.split(",") if g.strip()}
    if not ({"admin", "doctor"} & groupes):
        logger.warning(
            "Share refused: %s (groups: %s) is not allowed to share",
            remote_user, remote_groups,
        )
        raise HTTPException(
            status_code=403,
            detail=_msg("share_role_forbidden"),
        )
    
    body = await request.json()
    
    # Extract parameters from Authorization plugin request (PascalCase)
    request_id = body.get("Id", body.get("id", ""))
    resources = body.get("Resources", body.get("resources", []))
    validity_duration = body.get("ValidityDuration", body.get("validity-duration", DEFAULT_TOKEN_VALIDITY_SECONDS))

    # Handle case where ValidityDuration is 0 (unlimited in Authorization Plugin)
    if validity_duration == 0:
        validity_duration = UNLIMITED_TOKEN_DURATION

    # ExpirationDate: the Orthanc authorization plugin may request an explicit
    # expiry date instead of a duration. It was READ THEN IGNORED -- a caller
    # asking for a precise date silently got the default duration (7 days),
    # with no error and no trace. Found by static analysis on 2026-08-27
    # (variable assigned, never used). No caller in the repository sends it
    # today, but the plugin can: better to honour it than to lie about the
    # duration of a link that gives access to patient images.
    date_expiration = body.get("ExpirationDate", body.get("expiration-date"))
    if date_expiration:
        try:
            texte = str(date_expiration).replace("Z", "+00:00")
            instant = datetime.datetime.fromisoformat(texte)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=datetime.timezone.utc)
            restant = instant.timestamp() - time.time()
            if restant <= 0:
                raise HTTPException(400, _msg("expiration_past"))
            validity_duration = restant
        except HTTPException:
            raise
        except (ValueError, TypeError, OverflowError) as err:
            # Refuse rather than fall back to the default duration: a malformed
            # date must be visible, not produce a token whose real lifetime
            # nobody knows.
            raise HTTPException(
                400, _msg("expiration_unreadable", value=repr(date_expiration), error=err)
            ) from err
    
    # Generate unique token
    token = str(uuid.uuid4())
    
    # Store token in Redis with expiration and resources
    token_data = {
        "token_type": token_type,
        "request_id": request_id,
        "resources": resources,
        "role": "external-role",  # Share tokens are read-only
        "expires_at": time.time() + validity_duration,
        "created_at": time.time(),
        "max_uses": DEFAULT_TOKEN_MAX_USES,
        "current_uses": 0
    }
    store_token(token, token_data)
    
    # Generate URL based on token type
    base_url = get_base_url(request)
    
    # "instant-link" tokens sign an action Explorer 2 triggers itself: it
    # builds the URL and only expects the token from us. Orthanc asks for three
    # of them -- viewer-instant-link, download-instant-link and
    # meddream-instant-link (seen in its logs).
    #
    # Only viewer-instant-link was recognised. The other two fell into the
    # "publication" branch and got a /share/?token=... URL; Explorer 2
    # navigates to it, and /share/ does not know this kind of token: it falls
    # back to its default viewer. Result: clicking "download study" opened the
    # study in OHIF instead of delivering the file.
    #
    # The test therefore looks at the suffix, not at a precise name: a future
    # <something>-instant-link will behave correctly out of the box.
    if token_type.endswith("-instant-link"):
        response_data = {
            "Token": token,  # PascalCase for Authorization Plugin
            "Url": None      # Explorer 2 will build the URL directly
        }
    else:
        # For publications (shares), generate share URL that goes through /share/ route
        share_url = f"{base_url}/share/?token={token}"
        response_data = {
            "Token": token,  # PascalCase for Authorization Plugin
            "Url": share_url  # PascalCase for Authorization Plugin
        }
    
    return JSONResponse(content=response_data)

@app.get("/tokens")
async def list_tokens(request: Request):
    """List all active tokens with their metadata"""
    verify_admin_auth(request)
    
    # Get all tokens from Redis
    tokens = []
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, match="token:*", count=100)
        for key in keys:
            token_id = key.replace("token:", "")
            token_data = get_token(token_id)
            if token_data:
                # Add token ID to the data
                token_data["id"] = token_id
                # Calculate remaining time
                remaining_time = max(0, int(token_data.get("expires_at", time.time()) - time.time()))
                token_data["remaining_seconds"] = remaining_time
                # Format creation time
                try:
                    created_at = token_data.get("created_at", time.time())
                    token_data["created_at_formatted"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S", 
                        time.localtime(created_at)
                    )
                except (ValueError, OSError, KeyError):
                    token_data["created_at_formatted"] = "Unknown"
                # Enrich resources with info from Orthanc
                for res in token_data.get("resources", []) or []:
                    try:
                        info = resolve_resource_info(res)
                        for k, v in info.items():
                            if v:
                                res[k] = v
                    except Exception:
                        pass
                tokens.append(token_data)
        
        if cursor == 0:
            break
    
    # Sort by creation date (newest first)
    tokens.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    
    return JSONResponse(content={
        "tokens": tokens,
        "count": len(tokens)
    })

@app.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, request: Request):
    """Revoke a specific token"""
    remote_user = verify_admin_auth(request)
    
    # Check if token exists
    token_data = get_token(token_id)
    if not token_data:
        raise HTTPException(status_code=404, detail=_msg("token_not_found"))
    
    # Audit log for token revocation
    audit_data = {
        "action": "token_revoked",
        "token_id": token_id,
        "token_type": token_data.get("token_type"),
        "revoked_by": remote_user,
        "revoked_at": time.time(),
        "token_created_at": token_data.get("created_at"),
        "token_uses": token_data.get("current_uses", 0),
        "token_max_uses": token_data.get("max_uses", DEFAULT_TOKEN_MAX_USES)
    }
    
    # Log to application logs
    logger.info(f"Token revoked: {token_id} by {remote_user} (type: {token_data.get('token_type')})")
    
    # Store audit log in Redis with configurable retention
    audit_key = f"audit:revoke:{token_id}:{int(time.time())}"
    redis_client.setex(audit_key, AUDIT_RETENTION_DAYS * 24 * 3600, json.dumps(audit_data))
    
    # Delete the token
    delete_token(token_id)
    
    return JSONResponse(content={
        "message": "Token revoked successfully",
        "token_id": token_id,
        "revoked_by": remote_user,
        "revoked_at": time.time()
    })

@app.get("/tokens/expired")
async def list_expired_tokens(request: Request):
    """List expired tokens from audit logs"""
    verify_admin_auth(request)
    
    # Get expired tokens from audit logs
    expired_tokens = []
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, match="audit:revoke:*", count=100)
        for key in keys:
            audit_data_raw = redis_client.get(key)
            if audit_data_raw:
                try:
                    audit_data = json.loads(audit_data_raw)
                    # Transform audit data to token format
                    expired_token = {
                        "id": audit_data.get("token_id", ""),
                        "token_type": audit_data.get("token_type", "unknown"),
                        "created_at": audit_data.get("token_created_at", time.time()),
                        "expired_at": audit_data.get("revoked_at", time.time()),
                        "current_uses": audit_data.get("token_uses", 0),
                        "max_uses": audit_data.get("token_max_uses", DEFAULT_TOKEN_MAX_USES),
                        "resources": []  # Not stored in audit logs
                    }
                    expired_tokens.append(expired_token)
                except json.JSONDecodeError:
                    continue
        
        if cursor == 0:
            break
    
    # Sort by expiration date (newest first)
    expired_tokens.sort(key=lambda x: x.get("expired_at", 0), reverse=True)
    
    return JSONResponse(content={
        "tokens": expired_tokens,
        "count": len(expired_tokens)
    })

@app.get("/tokens/stats")
async def token_stats(request: Request):
    """Get statistics about tokens"""
    verify_admin_auth(request)
    
    # Collect statistics
    total_tokens = 0
    tokens_by_type = {}
    tokens_by_usage = {"low": 0, "medium": 0, "high": 0}
    
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, match="token:*", count=100)
        for key in keys:
            token_id = key.replace("token:", "")
            token_data = get_token(token_id)
            if token_data:
                total_tokens += 1
                
                # Count by type
                token_type = token_data.get("token_type", "unknown")
                tokens_by_type[token_type] = tokens_by_type.get(token_type, 0) + 1
                
                # Count by usage
                usage_percent = (token_data.get("current_uses", 0) / token_data.get("max_uses", DEFAULT_TOKEN_MAX_USES)) * 100
                if usage_percent < 33:
                    tokens_by_usage["low"] += 1
                elif usage_percent < 66:
                    tokens_by_usage["medium"] += 1
                else:
                    tokens_by_usage["high"] += 1
        
        if cursor == 0:
            break
    
    return JSONResponse(content={
        "total_active_tokens": total_tokens,
        "tokens_by_type": tokens_by_type,
        "tokens_by_usage": tokens_by_usage
    })

@app.get("/tokens/test")
async def token_test_interface(request: Request):
    """Test page for debugging token API"""
    try:
        verify_admin_auth(request)
    except HTTPException:
        return render_access_denied_template()
    
    # Serve the test page
    try:
        with open("/app/static/test-page.html", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except FileNotFoundError:
        return render_file_not_found_template(TRANSLATIONS["ui"]["test_page_not_found"], TRANSLATIONS["ui"]["test_page_not_found_message"])

@app.get("/tokens/manage")
async def token_management_interface(request: Request):
    """Serve the token management web interface"""
    try:
        verify_admin_auth(request)
    except HTTPException:
        return render_access_denied_template(TRANSLATIONS["ui"]["admin_access_required"], "/ui/")
    
    # Serve the token management interface
    try:
        # Prepare JavaScript configuration with translations
        js_config = dict(JS_CONFIG)
        
        # Add JavaScript translations based on current language
        js_translations = {}
        for key, value in TRANSLATIONS["js"].items():
            js_translations[key.upper()] = value
        
        if js_translations:
            js_config["MESSAGES"] = js_translations
        
        # Prepare template variables from translations. Cleaned up: the
        # TOTAL_TOKENS/SUBTITLE/OHIF_VIEWER/INSTANT_LINKS keys no longer have a
        # matching {PLACEHOLDER} in the template (KPI cards removed, subtitle
        # moved to static "Orthanc" HTML). ASSET_VERSION is also injected
        # automatically by render_template().
        ui_translations = TRANSLATIONS["ui"]
        template_vars = {
            "TITLE": ui_translations["title"],
            "REFRESH_BUTTON": ui_translations["refresh_button"],
            "ACTIVE_TOKENS": ui_translations["active_tokens"],
            "EXPIRED_TOKENS": ui_translations["expired_tokens"],
            "LOADING_TOKENS": ui_translations["loading_tokens"],
            "LOADING_EXPIRED_TOKENS": ui_translations["loading_expired_tokens"],
            "ADMIN_LABEL": ui_translations["admin_label"],
            "CONFIRM_REVOKE_TITLE": ui_translations["confirm_revoke_title"],
            "CONFIRM_REVOKE_MESSAGE": ui_translations["confirm_revoke_message"],
            "CONFIRM_REVOKE_WARNING": ui_translations["confirm_revoke_warning"],
            "CANCEL_BUTTON": ui_translations["cancel_button"],
            "REVOKE_BUTTON": ui_translations["revoke_button"],
            "SUCCESS_TOAST": ui_translations["success_toast"],
            "TOKEN_REVOKED_SUCCESS": ui_translations["token_revoked_success"],
            "ERROR_TOAST": ui_translations["error_toast"],
            "ERROR_OCCURRED": ui_translations["error_occurred"],
            "BACK_TO_PACS": ui_translations["back_to_pacs"],
            "PAGE_TITLE": ui_translations["page_title_shares"],
            "NAV_SHARES": ui_translations["nav_shares"],
            "NAV_LABEL": ui_translations["nav_label"],
        }
        
        # Render template with variables
        content = render_template("token-manager.html", 
                                js_config=json.dumps(js_config, indent=4),
                                **template_vars)
        
        return HTMLResponse(content=content)
    except FileNotFoundError:
        return render_file_not_found_template(TRANSLATIONS["ui"]["interface_not_found"], TRANSLATIONS["ui"]["token_management_interface_not_found"])

@app.get("/share/")
async def share_redirect(request: Request):
    """Validate token and redirect to OHIF or show error"""
    token = request.query_params.get("token")
    
    if not token:
        return render_error_template(TRANSLATIONS["ui"]["invalid_link"], UI_MESSAGES["INVALID_TOKEN"], "fas fa-shield-alt", 400)
    
    # Check if token exists and is valid
    token_data = get_token(token)
    if not token_data:
        return render_error_template(TRANSLATIONS["ui"]["expired_token"], UI_MESSAGES["EXPIRED_TOKEN"], "fas fa-clock", 410)
    
    # Check if token has expired
    if time.time() >= token_data["expires_at"]:
        delete_token(token)
        return render_error_template(TRANSLATIONS["ui"]["expired_token"], UI_MESSAGES["EXPIRED_TOKEN"], "fas fa-clock", 410)
    
    # Get study from token resources
    resources = token_data.get("resources", [])
    if not resources:
        return render_error_template(TRANSLATIONS["ui"]["no_study"], UI_MESSAGES["NO_STUDY"], "fas fa-folder-open", 400)
    
    study_uid = resources[0].get("DicomUid", "").strip()  # Remove any whitespace
    if not study_uid:
        return render_error_template(TRANSLATIONS["ui"]["invalid_study"], UI_MESSAGES["INVALID_STUDY"], "fas fa-exclamation-triangle", 400)
    
    # Increment token usage counter for share access
    if not increment_token_usage(token):
        return render_error_template(TRANSLATIONS["ui"]["link_expired"], UI_MESSAGES["USAGE_LIMIT"], "fas fa-clock", 410)
    
    # Redirect to appropriate viewer based on token type
    base_url = get_base_url(request)
    # Add cache-busting parameter to force config reload
    cache_bust = int(time.time())
    # URL encode the study UID to handle any special characters
    study_uid_encoded = urllib.parse.quote(study_uid, safe='')
    
    # Determine viewer URL based on token type
    token_type = token_data.get("token_type", "")
    if token_type == "stone-viewer-publication":
        # Stone Web Viewer
        viewer_url = f"{base_url}/stone-webviewer/index.html?study={study_uid_encoded}&token={token}&_cb={cache_bust}"
    elif token_type == "volview-viewer-publication":
        # VolView 3D Viewer entry HTML is served at /volview/index.html
        # (same pattern as Stone Web Viewer's /stone-webviewer/index.html).
        # Probe results with a valid token in the URL:
        #   /volview              -> 403  (auth plugin denies bare path)
        #   /volview/             -> 404  ("Unknown resource")
        #   /volview/index.html   -> 200  <-- this one
        #   /volview/main(.html)  -> 404
        #   /volview/app/(...)    -> 404  ("Unknown VolView resource: app")
        # The other fix that was strictly needed to make this work was the
        # Authelia bypass rule ^/volview.*token=.*$ in authelia
        # configuration.yml -- without it the request 302s to the SSO login.
        viewer_url = f"{base_url}/volview/index.html?StudyInstanceUIDs={study_uid_encoded}&token={token}&_cb={cache_bust}"
    else:
        # Default to OHIF for ohif-viewer-publication and unknown types
        viewer_url = f"{base_url}/ohif/viewer?StudyInstanceUIDs={study_uid_encoded}&token={token}&_cb={cache_bust}"
    
    # Use redirect template with translations
    content = render_template("redirect.html",
                             redirect_title=TRANSLATIONS["ui"]["redirect_title"],
                             redirecting=TRANSLATIONS["ui"]["redirecting"],
                             redirect_message=TRANSLATIONS["ui"]["redirect_message"],
                             redirect_click_here=TRANSLATIONS["ui"]["redirect_click_here"],
                             ohif_url=viewer_url)
    
    return HTMLResponse(content=content)

@app.get("/api/internal/verify-share", include_in_schema=False)
def verify_share(request: Request, token: str = ""):
    """Validate a share token for nginx. 204 when valid, 403 otherwise.

    The token is read from the X-Original-URI header, which nginx fills with the
    client request's URI. The `token` parameter is still accepted for a direct
    call, but nginx cannot use it: in the context of an auth_request
    subrequest, $arg_token comes out EMPTY. Going through $request_uri is the
    only reliable way to carry the value across.

    Replaces Authelia's blind bypass. The `bypass` rules
    (^/dicom-web.*token=.*$ and ^/wado.*token=.*$) fired on the MERE PRESENCE
    of the string "token=" in the URL, without checking anything:

        GET /dicom-web/studies?token=anything
          -> 200, 264 KB, 209 studies, from the Internet, without an account.

    Validation was supposed to fall to the Orthanc authorization plugin. It
    never happened: Orthanc does not extract the ?token= parameter on
    /dicom-web/studies, so auth-service never saw it. Measured and closed on
    2026-08-27; this endpoint is what makes it possible to reopen sharing
    without reopening the hole.

    Model: /api/internal/verify-cf, queried by nginx through
    `auth_request /_verify-cf`. Same principle, same discipline -- fail
    closed, anything unexpected answers 403.

    DOES NOT COUNT USAGE. nginx calls this endpoint on EVERY viewer request: a
    study means hundreds of dicom-web calls. Hooking increment_token_usage in
    here would exhaust a 50-use token in a single viewing. The count stays
    where it was, on /share/ (once per link opening) and in /tokens/validate.
    """
    if not token:
        uri = request.headers.get("x-original-uri", "")
        parsed = urllib.parse.urlparse(uri)
        token = (urllib.parse.parse_qs(parsed.query).get("token") or [""])[0]

    token = normalize_bearer_token(token or "")
    if not token:
        return Response(status_code=403)

    donnees = get_token(token)
    if not donnees:
        logger.warning("Share refused: unknown token (%s...)", token[:8])
        return Response(status_code=403)

    if time.time() >= donnees.get("expires_at", 0):
        delete_token(token)
        logger.warning("Share refused: expired token (%s...)", token[:8])
        return Response(status_code=403)

    # No ceiling test here. There was one, and it cut off the last authorised
    # viewing: the opening that reaches the quota brings current_uses TO
    # max_uses, and this test then refused every request from the viewer that
    # had just opened. The limit is enforced by the token's lifetime --
    # share_redirect brings it down to SURSIS_DERNIERE_OUVERTURE as soon as the
    # ceiling is reached. A token exhausted long enough ago no longer exists,
    # and the existence test above is enough to refuse it.

    # --- SCOPE: the token must cover WHAT IS REQUESTED -------------------
    #
    # This endpoint only checked the token's validity, never its scope.
    # Measured on 2026-08-29, from a browser WITHOUT any cookie, armed only
    # with the share link of one study:
    #
    #   GET /dicom-web/studies?limit=101&...&token=<token>  ->  200
    #   -> the list of all 209 studies, with patient names.
    #
    # It is the 2026-08-27 hole, back through the door we opened when
    # rebuilding sharing: Orthanc does restrict NAMED resources, but a QIDO
    # ENUMERATION request names nothing -- there is no resource to compare, and
    # it gets through.
    #
    # The scope is therefore checked here, on the URI, before Orthanc sees the
    # request. Rule: a share link gives access to ONE study, never to an
    # inventory. Anything that does not explicitly designate the covered study
    # is refused.
    uri = request.headers.get("x-original-uri", "")
    if not _partage_couvre_uri(donnees, uri):
        logger.warning(
            "Share refused: out of scope (%s...) %s",
            token[:8], urllib.parse.urlparse(uri).path,
        )
        return Response(status_code=403)

    return Response(status_code=204)


def _etudes_du_jeton(donnees: dict) -> set:
    """DICOM study UID this token covers."""
    uids = set()
    for r in donnees.get("resources", []):
        uid = r.get("DicomUid") or r.get("dicom-uid") or ""
        if uid:
            uids.add(uid)
    return uids


def _partage_couvre_uri(donnees: dict, uri: str) -> bool:
    """Does the token allow this precise URI?

    Fail-closed: anything not explicitly recognised is refused. An unexpected
    URI must cost a share that does not open, never an inventory that leaks.
    """
    autorisees = _etudes_du_jeton(donnees)
    if not autorisees:
        return False

    parsed = urllib.parse.urlparse(uri or "")
    chemin = parsed.path
    params = urllib.parse.parse_qs(parsed.query)

    # DICOMweb: /dicom-web/studies/<StudyInstanceUID>/... The segment after
    # "studies" must be the shared study. An enumeration -- /dicom-web/studies
    # on its own, with or without filters -- has no such segment: it is
    # refused, and that is the whole point of the fix.
    prefixe = "/dicom-web/studies"
    if chemin.startswith(prefixe):
        reste = chemin[len(prefixe):].lstrip("/")
        if reste:
            return reste.split("/")[0] in autorisees

        # No study segment: this is a QIDO request. It is NOT refused outright
        # -- OHIF uses it to resolve the study to open, with a StudyInstanceUID
        # filter. Refusing it wholesale cut sharing off: the viewer fell back
        # to "notfoundstudy".
        #
        # The rule is therefore: a QIDO must designate the shared study through
        # its filter. Without a filter, or with a filter aimed at something
        # else, it is an inventory, and it is refused. The filter name may be
        # written in clear or as a tag number (0020000D), both are accepted.
        for cle in ("StudyInstanceUID", "0020000D", "0020000d"):
            valeurs = params.get(cle) or []
            if valeurs:
                return all(v in autorisees for v in valeurs)
        return False

    # WADO-URI : /wado?requestType=WADO&studyUID=...&objectUID=...
    if chemin.rstrip("/") == "/wado":
        demandee = (params.get("studyUID") or params.get("studyInstanceUID") or [""])[0]
        return bool(demandee) and demandee in autorisees

    return False


@app.get("/health")
def health_check():
    return JSONResponse(content={
        "status": "healthy",
        "service": "auth-service",
        "version": "1.0.0"
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)