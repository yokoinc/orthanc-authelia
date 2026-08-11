from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import secrets
import uuid
import time
import json
import redis
import os
import logging
import re
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

app = FastAPI(title="PACS Auth Service", description="Authentication and token management for PACS")
security = HTTPBasic()

# Mount static files
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# Mount the Vue SPA when present (skipped if the image was not built
# with the frontend stage, e.g. local dev without an npm build).
import os as _os
if _os.path.isdir("/app/frontend"):
    app.mount("/ui/assets", StaticFiles(directory="/app/frontend/assets"), name="frontend-assets")

    # SPA catch-all: any /ui/xxx returns index.html so that vue-router
    # (history mode) can take over client-side. Needed because StaticFiles
    # with html=True does not fall back on unknown paths.
    from fastapi.responses import HTMLResponse as _HTMLResponse

    _SPA_INDEX = "/app/frontend/index.html"

    def _spa_html() -> str:
        """The SPA's index.html, with translations injected.

        The language is changed from the panel, so freezing labels into the
        bundle at build time is out of the question. Injecting them into the
        page avoids exposing an extra route -- the wizard is unauthenticated,
        and would have needed a dedicated opening in the nginx configuration.

        translations() and _language() are defined further down this file;
        resolution happens at call time, not at import, so the order hardly
        matters.
        """
        html = Path(_SPA_INDEX).read_text(encoding="utf-8")
        charge = json.dumps(
            {"lang": _language(), "ui": translations().get("ui", {})},
            ensure_ascii=False,
        )
        # A </script> inside a translated value would close the tag by
        # accident and break the page.
        charge = charge.replace("</", "<\\/")
        return html.replace(
            "</head>",
            f"<script>window.__I18N__={charge};</script></head>",
            1,
        )

    @app.get("/ui/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return _HTMLResponse(_spa_html())

    @app.get("/ui", include_in_schema=False)
    async def spa_root():
        return _HTMLResponse(_spa_html())

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# Token configuration
DEFAULT_TOKEN_MAX_USES = int(os.getenv("DEFAULT_TOKEN_MAX_USES", "50"))
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
def _language() -> str:
    """Interface language, changeable from the panel.

    Read from the settings rather than from the environment: a display
    preference should not require recreating the container to change. The
    LANGUAGE variable is still consulted second, for installations that still
    carry it in their .env.
    """
    try:
        from admin_module import _read_setting

        # "langue" was the original key: installations that already wrote
        # it keep working without intervention.
        choisie = (_read_setting("language", "LANGUAGE")
                   or _read_setting("langue"))
    except Exception:  # noqa: BLE001 - reglages illisibles ne cassent rien
        choisie = ""
    if choisie in AVAILABLE_LANGUAGES:
        return choisie
    return os.getenv("LANGUAGE", "en") if os.getenv("LANGUAGE") in AVAILABLE_LANGUAGES else "en"


# Languages for which a translation file is shipped.
AVAILABLE_LANGUAGES = ("en", "fr")

# Orthanc API configuration (for patient name resolution)
ORTHANC_API_URL = os.getenv("ORTHANC_API_URL", "http://orthanc:8042").rstrip("/")
ORTHANC_API_TIMEOUT = float(os.getenv("ORTHANC_API_TIMEOUT", "3"))
PATIENT_NAME_CACHE_TTL = int(os.getenv("PATIENT_NAME_CACHE_TTL", "300"))  # 5 minutes
_resource_info_cache = {}  # {key: (info_dict, timestamp)}


def _orthanc_get(path):
    """GET helper for Orthanc REST API."""
    url = f"{ORTHANC_API_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=ORTHANC_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as err:
        logger.debug(f"Orthanc GET {path} failed: {err}")
        return None


def _orthanc_post(path, body):
    """POST helper for Orthanc REST API."""
    url = f"{ORTHANC_API_URL}{path}"
    try:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
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
            study = _orthanc_get(f"/studies/{study_id}")
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
# Semantic version of the image (shown in the footer). Independent of the
# ASSET_VERSION cache-buster, which is a Unix timestamp.
IMAGE_VERSION = os.getenv("IMAGE_VERSION", "dev")

# Load translations from JSON files
def load_translations(language="en"):
    """Load translations from JSON files"""
    translations_dir = Path("/app/translations")
    translation_file = translations_dir / f"{language}.json"
    
    # Fallback to English if requested language not found
    if not translation_file.exists():
        translation_file = translations_dir / "en.json"
    
    try:
        with open(translation_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading translations: {e}")
        # Return minimal English fallback
        return {
            "ui": {
                "invalid_token": "No token provided.",
                "expired_token": "This sharing link is no longer valid.",
                "no_study": "No study associated with this token.",
                "invalid_study": "Missing study identifier.",
                "usage_limit": "This sharing link has reached its usage limit."
            },
            "js": {}
        }

# Translations loaded on demand, rather than once and for all at startup.
#
# The cache avoids re-reading the file for every label displayed; it only
# covers the current language, so a change made from the panel takes effect on
# the next request.
_translations_cache: dict = {"langue": None, "data": None}


def translations() -> dict:
    """Table de traduction correspondant a la langue en vigueur."""
    langue = _language()
    if _translations_cache["langue"] != langue:
        _translations_cache["data"] = load_translations(langue)
        _translations_cache["langue"] = langue
    return _translations_cache["data"]


def ui_messages() -> dict:
    """Messages d'erreur des pages publiques, en majuscules par habitude."""
    ui = translations()["ui"]
    return {
        "INVALID_TOKEN": ui["invalid_token"],
        "EXPIRED_TOKEN": ui["expired_token"],
        "NO_STUDY": ui["no_study"],
        "INVALID_STUDY": ui["invalid_study"],
        "USAGE_LIMIT": ui["usage_limit"],
    }

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
# are async. We initialise it separately, sharing the same Redis database.
# Le module expose : router, setup_gate, csrf_gate, set_redis.
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
    logging.info("admin_module chargé — panel sur /console/, assistant sur /console/setup")
except ImportError as e:
    logging.warning(f"admin_module non charge : {e} — les routes admin ne seront pas dispo")

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
    """Increment token usage counter, return False if max reached"""
    data = get_token(token)
    if not data:
        return False
    
    data["current_uses"] = data.get("current_uses", 0) + 1
    
    # Check if max uses exceeded
    if data["current_uses"] >= data.get("max_uses", 999999):
        delete_token(token)
        return False
    
    # Update in Redis
    expiration_time = int(data["expires_at"] - time.time())
    if expiration_time > 0:
        redis_client.setex(f"token:{token}", expiration_time, json.dumps(data))
    
    return True

def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic authentication"""
    correct_password = VALID_USERS.get(credentials.username)
    if not correct_password or not secrets.compare_digest(credentials.password, correct_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username

def verify_admin_auth(request: Request):
    """Verify admin authentication from Authelia headers"""
    remote_user = request.headers.get("Remote-User", "")
    remote_groups = request.headers.get("Remote-Groups", "")
    
    # Debug logging
    logger.info(f"Auth check - Remote-User: '{remote_user}', Remote-Groups: '{remote_groups}'")
    logger.info(f"All headers: {dict(request.headers)}")
    
    if "admin" not in remote_groups:
        raise HTTPException(status_code=403, detail="Admin access required")
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
    """Render an HTML template with the provided variables.

    A single regex pass matching `{word}` and replacing it with the matching
    keyword argument. When no argument matches, the placeholder is left as-is
    (useful for `{js_config}` blocks, which contain JSON). No cascading, so a
    replaced value containing a placeholder cannot be substituted again on a
    later pass.
    """
    template_path = f"/app/templates/{template_name}"
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        kwargs["font_awesome_cdn"] = FONT_AWESOME_CDN
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
        message = translations()["ui"]["access_denied_message"]

    back_link_html = f'<a href="{back_link}" class="oe2-centered__link">{translations()["ui"]["back_to_pacs"]}</a>' if back_link else ""
    content = render_template("access_denied.html",
                             access_denied_title=translations()["ui"]["access_denied_title"],
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

# Viewers that can be offered by default on a share link.
# "viewer-instant-link" is not one of them: it is not a publication but a link
# Explorer builds directly, with no share page.
SHARE_VIEWERS = (
    "ohif-viewer-publication",
    "stone-viewer-publication",
    "volview-viewer-publication",
)
DEFAULT_SHARE_VIEWER = "ohif-viewer-publication"


def _default_share_viewer() -> str:
    """Viewer preselected when sharing a study from Explorer.

    Careful: Explorer does NOT consult this value. Its bundle contains no
    occurrence of "default-viewer"; it reads
    OrthancExplorer2.Tokens.ShareType from its own configuration. We
    therefore return that same field here, so a client querying this API does
    not get an answer contradicting what actually applies on screen.
    """
    try:
        from admin_module import _read_share_type

        return _read_share_type()
    except Exception:  # noqa: BLE001 - orthanc.json illisible ne casse rien
        return DEFAULT_SHARE_VIEWER


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
        "default-viewer": _default_share_viewer(),
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
        
        # Increment usage counter and check limits
        if not increment_token_usage(token_value):
            return JSONResponse(content={
                "granted": False,
                "validity": 0
            })
        
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
        if method in ["get", "post"] and level in ["patient", "study", "series", "instance", "system"]:
            return True
        if method == "put" and "tokens" in (uri or ""):  # Allow token creation for sharing
            return True
        return False
    elif role == "external-role":
        # External users can only read
        return method == "get" and level in ["patient", "study", "series", "instance"]

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
        
        # Exact match
        if orthanc_id == token_orthanc_id or dicom_uid == token_dicom_uid:
            return True
            
        # Hierarchical access: if token is for a study, allow access to its series/instances
        if token_level == "study" and level in ["series", "instance"]:
            # We'd need to query Orthanc to check hierarchy, for now allow it
            return True
        elif token_level == "series" and level == "instance":
            return True
    
    return False

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
            "authorized-labels": ["*"],
            "permissions": ["upload"],
            "groups": [],
            "validity": CACHE_VALIDITY_USER_SESSION
        })

    # --- Authenticated user : map Authelia group -> permissions ------------
    # Authelia passes groups as a single comma-separated string
    # ("admin,doctor"). Test membership of the list, not the presence of a
    # substring: "admin" in "readonly-admin" is true, and a group created in
    # good faith would inherit full rights over the PACS with nothing to
    # signal it.
    group_list = [g.strip() for g in group.replace(";", ",").split(",") if g.strip()]

    if "admin" in group_list:
        user_name = translations()["ui"]["administrator"]
        # Full list of permissions the Authorization plugin recognises,
        # taken from the patterns it registers at startup.
        #
        # "all" is not enough: several routes name a permission it does not
        # cover. Creating and deleting
        # modalities, for instance, require "admin-permissions" --
        #   put    ^/modalities/(.*)$ - admin-permissions
        #   delete ^/modalities/(.*)$ - admin-permissions
        # and "all" is not among them. An administrator holding every other
        # right therefore could not declare a DICOM device, with no
        # explanation beyond a 403.
        #
        # Manquaient egalement : audit-logs (journaux d'Orthanc), worklists
        # (worklists) and job management, also filed under
        # admin-permissions.
        permissions = [
            "all", "admin-permissions", "audit-logs", "worklists",
            "view", "download", "upload", "delete", "modify", "anonymize",
            "share", "send", "settings", "edit-labels", "q-r-remote-modalities",
        ]
    elif "doctor" in group_list:
        user_name = translations()["ui"]["doctor"]
        permissions = ["view", "download", "upload", "share", "send", "edit-labels"]
    else:
        user_name = translations()["ui"]["external_user"]
        permissions = ["view", "download"]

    return JSONResponse(content={
        "name": user_name,
        "user-id": group,                 # wire key is 'user-id' (NOT 'id')
        "authorized-labels": ["*"],       # access to all labels
        "permissions": permissions,
        "groups": group_list,
        "validity": CACHE_VALIDITY_USER_SESSION
    })

@app.post("/tokens/decode")
async def decode_token(request: Request):
    body = await request.json()
    
    token_key = body.get("token-key", "")
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
    
    resource = resources[0]
    orthanc_id = resource.get("OrthancId", resource.get("orthanc-id", ""))
    level = resource.get("Level", resource.get("level", "study"))
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
        raise HTTPException(status_code=401, detail="Missing authentication headers")
    
    body = await request.json()
    
    # Extract parameters from Authorization plugin request (PascalCase)
    request_id = body.get("Id", body.get("id", ""))
    resources = body.get("Resources", body.get("resources", []))
    expiration_date = body.get("ExpirationDate", body.get("expiration-date"))
    validity_duration = body.get("ValidityDuration", body.get("validity-duration", DEFAULT_TOKEN_VALIDITY_SECONDS))
    
    # Handle case where ValidityDuration is 0 (unlimited in Authorization Plugin)
    if validity_duration == 0:
        validity_duration = UNLIMITED_TOKEN_DURATION
    
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
    
    if token_type == "viewer-instant-link":
        # For instant links, no URL returned - Explorer 2 builds it directly
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
        raise HTTPException(status_code=404, detail="Token not found")
    
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
        return render_file_not_found_template(translations()["ui"]["test_page_not_found"], translations()["ui"]["test_page_not_found_message"])

async def _server_name() -> str:
    """Server name, as Orthanc actually applies it.

    We query Orthanc rather than read orthanc.json: the file may have been
    modified from the panel without the container having restarted, in which
    case it announces a name that is not in force yet.

    Orthanc being unavailable must not stop the page from rendering, so we
    fall back to "Orthanc".
    """
    try:
        from admin_module import _orthanc

        reponse = await _orthanc("GET", "/system")
        if reponse.status_code == 200:
            return reponse.json().get("Name") or "Orthanc"
    except Exception:  # noqa: BLE001 - une page d'UI ne casse pas pour si peu
        logger.debug("Nom du serveur indisponible, repli sur 'Orthanc'")
    return "Orthanc"


@app.get("/tokens/manage")
async def token_management_interface(request: Request):
    """Serve the token management web interface"""
    try:
        verify_admin_auth(request)
    except HTTPException:
        return render_access_denied_template(translations()["ui"]["admin_access_required"], "/ui/")
    
    # Serve the token management interface
    try:
        # Prepare JavaScript configuration with translations
        js_config = dict(JS_CONFIG)
        
        # Add JavaScript translations based on current language
        js_translations = {}
        for key, value in translations()["js"].items():
            js_translations[key.upper()] = value
        
        if js_translations:
            js_config["MESSAGES"] = js_translations
        
        # Prepare template variables from translations.
        # Cleaned up: the TOTAL_TOKENS/SUBTITLE/OHIF_VIEWER/INSTANT_LINKS
        # keys no longer have a matching {PLACEHOLDER} in the template (KPI
        # cards
        # retirees, subtitle deplacee en HTML statique "Orthanc"). ASSET_VERSION
        # is also injected automatically by render_template().
        ui_translations = translations()["ui"]
        template_vars = {
            "SERVER_NAME": await _server_name(),
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
            "BACK_TO_PACS": ui_translations.get("back_to_pacs", "Retour au PACS"),
        }
        
        # Render template with variables
        content = render_template("token-manager.html", 
                                js_config=json.dumps(js_config, indent=4),
                                **template_vars)
        
        return HTMLResponse(content=content)
    except FileNotFoundError:
        return render_file_not_found_template(translations()["ui"]["interface_not_found"], translations()["ui"]["token_management_interface_not_found"])

@app.get("/share/")
async def share_redirect(request: Request):
    """Validate token and redirect to OHIF or show error"""
    token = request.query_params.get("token")
    
    if not token:
        return render_error_template(translations()["ui"]["invalid_link"], ui_messages()["INVALID_TOKEN"], "fas fa-shield-alt", 400)
    
    # Check if token exists and is valid
    token_data = get_token(token)
    if not token_data:
        return render_error_template(translations()["ui"]["expired_token"], ui_messages()["EXPIRED_TOKEN"], "fas fa-clock", 410)
    
    # Check if token has expired
    if time.time() >= token_data["expires_at"]:
        delete_token(token)
        return render_error_template(translations()["ui"]["expired_token"], ui_messages()["EXPIRED_TOKEN"], "fas fa-clock", 410)
    
    # Get study from token resources
    resources = token_data.get("resources", [])
    if not resources:
        return render_error_template(translations()["ui"]["no_study"], ui_messages()["NO_STUDY"], "fas fa-folder-open", 400)
    
    study_uid = resources[0].get("DicomUid", "").strip()  # Remove any whitespace
    if not study_uid:
        return render_error_template(translations()["ui"]["invalid_study"], ui_messages()["INVALID_STUDY"], "fas fa-exclamation-triangle", 400)
    
    # Increment token usage counter for share access
    if not increment_token_usage(token):
        return render_error_template(translations()["ui"]["link_expired"], ui_messages()["USAGE_LIMIT"], "fas fa-clock", 410)
    
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
                             redirect_title=translations()["ui"]["redirect_title"],
                             redirecting=translations()["ui"]["redirecting"],
                             redirect_message=translations()["ui"]["redirect_message"],
                             redirect_click_here=translations()["ui"]["redirect_click_here"],
                             ohif_url=viewer_url)
    
    return HTMLResponse(content=content)

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