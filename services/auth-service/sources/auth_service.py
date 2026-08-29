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

app = FastAPI(title="PACS Auth Service", description="Authentication and token management for PACS")
security = HTTPBasic()

# Mount static files
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# Token configuration
DEFAULT_TOKEN_MAX_USES = int(os.getenv("DEFAULT_TOKEN_MAX_USES", "50"))

# Sursis accorde a un jeton dont le quota d'ouvertures vient d'etre epuise.
# Sans lui, la derniere ouverture supprimerait le jeton et le visualiseur qui
# vient de s'afficher perdrait ses images en cours de route. Deux heures :
# assez pour relire un examen sans se presser, assez court pour qu'un lien
# epuise ne serve pas la journee.
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


# Orthanc n'est pas gouverne par ses identifiants HTTP mais par son greffon
# d'autorisation : meme avec ORTHANC_ADMIN_USER/PASS, un GET /studies repond
# 403 (mesure le 2026-08-29). Le greffon accepte en revanche un jeton dans
# l'en-tete `auth-token` (TokenHttpHeaders d'orthanc.json), qu'il nous renvoie
# ensuite valider : la valeur "admin" y ouvre le profil administrateur.
#
# Sans cet en-tete, TOUS les appels de ce module partaient en 403 en silence --
# _orthanc_get renvoyait None et l'appelant se contentait d'une valeur vide.
# C'est pour cela que le gestionnaire de partages n'affichait aucun nom de
# patient : la recherche echouait, sans un mot dans les journaux.
#
# Sans danger depuis Internet : nginx execute auth_request AVANT de relayer, et
# un client qui injecte lui-meme auth-token / X-Auth-User / Remote-User est
# redirige vers l'authentification (verifie sur les quatre en-tetes). Cette
# valeur ne sert qu'entre conteneurs, sur le reseau Docker ferme.
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
# Semantic version of the image, shown in the footer. Independent of the
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

# Load translations based on configured language
TRANSLATIONS = load_translations(LANGUAGE)

# Extract UI messages for backward compatibility
UI_MESSAGES = {
    "INVALID_TOKEN": TRANSLATIONS["ui"]["invalid_token"],
    "EXPIRED_TOKEN": TRANSLATIONS["ui"]["expired_token"],
    "NO_STUDY": TRANSLATIONS["ui"]["no_study"],
    "INVALID_STUDY": TRANSLATIONS["ui"]["invalid_study"],
    "USAGE_LIMIT": TRANSLATIONS["ui"]["usage_limit"]
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
# are async. It is initialised separately, sharing the same Redis database.
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
    logging.info("admin_module chargé — routes /auth/setup et /auth/admin actives")
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
    """Compte une ouverture du lien. Renvoie False si le quota est atteint.

    Appele UNIQUEMENT depuis share_redirect : une incrementation = une
    ouverture du lien de partage. Surtout pas depuis la validation de
    protocole, qui se declenche des centaines de fois par consultation.
    """
    data = get_token(token)
    if not data:
        return False

    # Le quota se verifie AVANT de compter, et l'utilisation qui atteint le
    # plafond est accordee. Le code testait `current_uses >= max_uses` APRES
    # avoir incremente : un partage cree pour une seule ouverture n'en
    # accordait aucune, et chaque quota etait court d'une unite (mesure :
    # max_uses=1 -> 0 ouverture, max_uses=3 -> 2).
    utilisees = data.get("current_uses", 0)
    plafond = data.get("max_uses", 999999)
    if utilisees >= plafond:
        # Refuser, sans supprimer. Supprimer ici aneantirait le sursis accorde
        # plus bas : il suffisait qu'on reclique sur un lien epuise pour couper
        # les images de la personne en train de consulter. C'est la duree de vie
        # raccourcie qui fait disparaitre le jeton, elle seule.
        return False

    data["current_uses"] = utilisees + 1

    restant = int(data["expires_at"] - time.time())
    if restant <= 0:
        delete_token(token)
        return False

    # Le plafond vient d'etre atteint : c'etait la derniere ouverture. On ne
    # supprime PAS le jeton tout de suite -- le visualiseur qui vient de
    # s'ouvrir a besoin de lui pendant toute la consultation, et les images se
    # figeraient sous les yeux du confrere. On raccourcit sa duree de vie a un
    # sursis, le temps de regarder l'examen, puis il disparait.
    if data["current_uses"] >= plafond:
        restant = min(restant, SURSIS_DERNIERE_OUVERTURE)

    redis_client.setex(f"token:{token}", restant, json.dumps(data))
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

    # Ne JAMAIS journaliser les en-tetes en bloc. La ligne precedente etait
    # `logger.info(f"All headers: {dict(request.headers)}")` : elle ecrivait le
    # cookie authelia_session EN CLAIR dans les journaux du conteneur, a chaque
    # appel d'une route d'administration. Quiconque lit `docker logs` -- ou les
    # fichiers de journaux du NAS, ou une sauvegarde de ceux-ci -- y trouvait un
    # cookie de session valide et pouvait se faire passer pour l'administrateur.
    # Verifie le 2026-08-29 : le cookie apparaissait tel quel.
    logger.debug("Controle admin : %s [%s]", remote_user, remote_groups)

    # Comparaison EXACTE, sur la liste separee par des virgules que produit
    # Authelia. Le test etait `"admin" not in remote_groups`, une recherche de
    # sous-chaine : un groupe nomme « nonadmin », « badmin » ou « admins »
    # aurait suffi a ouvrir l'administration. Aucun groupe existant ne tombe
    # dans le piege aujourd'hui -- c'est le jour ou l'on en ajoute un qu'il se
    # referme.
    groupes = {g.strip() for g in remote_groups.split(",") if g.strip()}
    if ADMIN_GROUP not in groupes:
        raise HTTPException(status_code=403, detail="Acces administrateur requis")
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

    Un seul pass regex qui matche `{word}` et le remplace par la valeur du
    kwarg correspondant. Si aucun kwarg ne matche, le placeholder est laisse
    tel quel (utile pour les blocs `{js_config}` qui contiennent du JSON).
    Zero cascade => pas de risque qu'une valeur remplacee contienne un
    placeholder qui serait re-remplace au tour suivant.
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
        
        # Quota VERIFIE, pas consomme.
        #
        # Ce point d'entree est appele par le greffon d'Orthanc a chaque
        # ressource, et il revalide toutes les 60 s (CACHE_VALIDITY_SHARE_TOKEN).
        # Une etude, ce sont des centaines de series et d'instances : un jeton
        # de 50 usages mourait en pleine consultation, parfois en quelques
        # secondes. Le confrere voyait les images se figer sans explication.
        #
        # Le decompte n'a de sens que rapporte a une OUVERTURE de lien, et il se
        # fait deja la, dans share_redirect.
        #
        # Aucun test de plafond ici non plus : la derniere ouverture autorisee
        # porte current_uses A max_uses, et refuser dans ce cas couperait les
        # images de la consultation qu'on vient tout juste d'autoriser. C'est
        # la duree de vie du jeton qui applique la limite -- share_redirect la
        # ramene a SURSIS_DERNIERE_OUVERTURE des le plafond atteint, apres quoi
        # le jeton disparait de lui-meme et get_token ne le trouve plus.
        
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
        # « system » etait dans la meme liste que patient/study/series/instance,
        # POST compris : cette fonction repondait donc oui a POST /tools/reset,
        # /tools/shutdown et /tools/execute-script -- redemarrer, eteindre, et
        # executer du Lua. Ces chemins sont bien exposes publiquement (nginx les
        # route, ligne ~650). Ce n'etait pas exploitable en pratique : le profil
        # du medecin ne porte pas la permission que le greffon exige pour ces
        # points d'entree, et Orthanc repond 403 -- mesure le 2026-08-29. Mais
        # une autorisation ne doit pas dependre d'un refus place plus loin :
        # le jour ou l'on ajoute une permission au profil, le trou s'ouvre sans
        # que personne ne relise cette ligne.
        #
        # En lecture, le niveau systeme reste necessaire : les visualiseurs
        # interrogent /system et /plugins au demarrage.
        if method == "get" and level == "system":
            return True
        if method in ("get", "post") and level in (
                "patient", "study", "series", "instance"):
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

        # Correspondance exacte -- des DEUX cotes non vides.
        #
        # Le test etait `orthanc_id == token_orthanc_id or dicom_uid ==
        # token_dicom_uid`. Quand les deux valeurs manquaient, "" == "" etait
        # vrai et l'acces etait accorde : il suffisait d'une requete ou le
        # greffon n'identifie pas la ressource pour passer.
        if token_orthanc_id and orthanc_id == token_orthanc_id:
            return True
        if token_dicom_uid and dicom_uid == token_dicom_uid:
            return True

        # Acces hierarchique : un jeton d'ETUDE couvre ses series et ses
        # instances -- LES SIENNES.
        #
        # Le code precedent repondait `return True` a toute requete de niveau
        # serie ou instance des lors que le jeton etait de niveau etude, avec
        # ce commentaire : « We'd need to query Orthanc to check hierarchy, for
        # now allow it ». Autrement dit, un lien de partage valide pour une
        # etude donnait acces a TOUTE serie et TOUTE instance du serveur --
        # 209 etudes, pas une. Meme famille que la faille fermee le
        # 2026-08-27 : la chaine faisait confiance a une correspondance qu'elle
        # ne verifiait pas.
        #
        # On demande donc a Orthanc a quelle etude appartient reellement la
        # ressource. Le resultat est mis en cache : la filiation d'une instance
        # ne change jamais.
        if token_level == "study" and level in ("series", "instance"):
            if _appartient_a_etude(level, orthanc_id, token_orthanc_id, token_dicom_uid):
                return True
        elif token_level == "series" and level == "instance":
            if _appartient_a_serie(orthanc_id, token_orthanc_id):
                return True

    return False


# Filiation : 24 h de cache. Une instance ne change jamais de serie, ni une
# serie d'etude -- seule une suppression les fait disparaitre, et la requete
# echoue alors d'elle-meme.
_PARENT_CACHE_TTL = 86400
_parent_cache: dict = {}


def _parent_orthanc(level: str, orthanc_id: str) -> dict | None:
    """Renvoie la fiche Orthanc d'une serie ou d'une instance, en cache."""
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
    """La serie / l'instance demandee appartient-elle bien a cette etude ?

    Refuse quand Orthanc ne repond pas : mieux vaut un partage qui echoue
    qu'un partage qui ouvre le serveur entier.
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
    """L'instance demandee appartient-elle bien a cette serie ?"""
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
            # [] et NON ["*"]. La portee d'etiquettes gouverne l'ENUMERATION,
            # independamment des permissions : avec ["*"], un anonyme sans
            # aucun droit de lecture obtenait quand meme la liste complete des
            # etudes par /dicom-web/studies -- noms de patients, dates,
            # descriptions. Verifie exploitable depuis Internet le 2026-08-27,
            # 209 etudes exposees.
            #
            # Le commentaire d'origine justifiait ["*"] par : "le seul chemin
            # qui atteint Orthanc anonymement est /api-upload/, protege par
            # Cloudflare Access". L'hypothese etait fausse : les regles bypass
            # d'Authelia (^/dicom-web.*token=.*$ et ses quatre soeurs) se
            # declenchent sur la simple presence de "token=" dans l'URL et
            # ouvrent un second chemin anonyme, celui-la sans aucun garde.
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
    else:
        # Cette branche recevait TOUTE valeur non reconnue et lui accordait
        # view + download sur "authorized-labels": ["*"]. C'etait une faille
        # exploitable par n'importe qui, sans compte :
        #
        #   GET /dicom-web/studies?token=nimportequoi   -> 200, 209 etudes
        #
        # Le chemin complet : les regles `bypass` d'Authelia se declenchent sur
        # la simple presence de "token=" dans l'URL (^/dicom-web.*token=.*$ et
        # ses quatre soeurs) ; Orthanc appelle alors /user/get-profile avec la
        # valeur du parametre ; et ce `else` la prenait pour un utilisateur
        # externe legitime. Verifie exploitable depuis Internet le 2026-08-27.
        #
        # Les seules valeurs licites ici sont les jetons de partage, emis par ce
        # service et conserves dans Redis. Tout le reste doit retomber sur le
        # profil anonyme -- depot autorise, aucune lecture.
        jeton = get_token(group)
        if not jeton or time.time() >= jeton.get("expires_at", 0):
            logger.warning(
                "Profil refuse : jeton inconnu ou expire (%s...)", group[:8]
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
    
    # token-key : le greffon d'Orthanc envoie le NOM du parametre ou il a
    # trouve le jeton (par ex. "token"). Sans usage ici, on ne le lit pas.
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
    
    # Seul l'UID DICOM sert ici a construire l'URL du visualiseur, et il est
    # deja extrait plus haut. OrthancId et Level du jeton ne servent pas a ce
    # niveau : le perimetre est applique plus loin, par check_resource_access.
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
    validity_duration = body.get("ValidityDuration", body.get("validity-duration", DEFAULT_TOKEN_VALIDITY_SECONDS))

    # Handle case where ValidityDuration is 0 (unlimited in Authorization Plugin)
    if validity_duration == 0:
        validity_duration = UNLIMITED_TOKEN_DURATION

    # ExpirationDate : le greffon d'autorisation d'Orthanc peut demander une
    # date d'expiration explicite au lieu d'une duree. Elle etait LUE PUIS
    # IGNOREE -- un appelant qui demandait une date precise recevait
    # silencieusement la duree par defaut (7 jours), sans erreur ni trace.
    # Trouve a l'analyse statique le 2026-08-27 (variable assignee, jamais
    # utilisee). Aucun appelant du depot ne l'envoie aujourd'hui, mais le
    # greffon le peut : mieux vaut l'honorer que de mentir sur la duree d'un
    # lien qui donne acces a des images de patients.
    date_expiration = body.get("ExpirationDate", body.get("expiration-date"))
    if date_expiration:
        try:
            texte = str(date_expiration).replace("Z", "+00:00")
            instant = datetime.datetime.fromisoformat(texte)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=datetime.timezone.utc)
            restant = instant.timestamp() - time.time()
            if restant <= 0:
                raise HTTPException(400, "ExpirationDate est deja passee")
            validity_duration = restant
        except HTTPException:
            raise
        except (ValueError, TypeError, OverflowError) as err:
            # On refuse plutot que de retomber sur la duree par defaut : une
            # date mal formee doit se voir, pas produire un jeton dont personne
            # ne connait la duree reelle.
            raise HTTPException(
                400, f"ExpirationDate illisible ({date_expiration!r}) : {err}"
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
    
    # Les jetons « instant-link » servent a signer une action qu'Explorer 2
    # declenche lui-meme : il construit l'URL et n'attend de nous que le jeton.
    # Orthanc en demande trois -- viewer-instant-link, download-instant-link et
    # meddream-instant-link (vus dans ses journaux).
    #
    # Seul viewer-instant-link etait reconnu. Les deux autres tombaient dans la
    # branche « publication » et recevaient une URL /share/?token=... ;
    # Explorer 2 y navigue, et /share/ ne connait pas ce type de jeton : il
    # retombe sur son visualiseur par defaut. Resultat : cliquer « telecharger
    # l'etude » ouvrait l'etude dans OHIF au lieu de livrer le fichier.
    #
    # Le test porte donc sur le suffixe, pas sur un nom precis : un futur
    # <quelquechose>-instant-link se comportera correctement d'office.
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
        
        # Prepare template variables from translations.
        # Nettoye : les cles TOTAL_TOKENS/SUBTITLE/OHIF_VIEWER/INSTANT_LINKS
        # n'ont plus de {PLACEHOLDER} correspondant dans le template (KPI cards
        # retirees, subtitle deplacee en HTML statique "Orthanc"). ASSET_VERSION
        # est aussi injecte automatiquement par render_template().
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
            "BACK_TO_PACS": ui_translations.get("back_to_pacs", "Retour au PACS"),
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
    """Valide un jeton de partage pour nginx. 204 si valide, 403 sinon.

    Le jeton est lu dans l'en-tete X-Original-URI, que nginx renseigne avec
    l'URI de la requete cliente. Le parametre `token` reste accepte pour un
    appel direct, mais nginx ne peut pas s'en servir : dans le contexte d'une
    sous-requete auth_request, $arg_token ressort VIDE. Passer par
    $request_uri est la seule facon fiable de faire traverser la valeur.

    Remplace le contournement aveugle d'Authelia. Les regles `bypass`
    (^/dicom-web.*token=.*$ et ^/wado.*token=.*$) se declenchaient sur la SEULE
    PRESENCE de la chaine "token=" dans l'URL, sans rien verifier :

        GET /dicom-web/studies?token=nimportequoi
          -> 200, 264 Ko, 209 etudes, depuis Internet, sans compte.

    La validation etait censee revenir au greffon d'autorisation d'Orthanc.
    Elle n'avait jamais lieu : Orthanc n'extrait pas le parametre ?token= sur
    /dicom-web/studies, auth-service ne le voyait donc jamais. Mesure et
    fermeture le 2026-08-27 ; ce point d'entree est ce qui permet de rouvrir le
    partage sans rouvrir la faille.

    Modele : /api/internal/verify-cf, interroge par nginx via
    `auth_request /_verify-cf`. Meme principe, meme discipline -- on echoue
    ferme, tout imprevu repond 403.

    NE COMPTE PAS L'USAGE. nginx appelle ce point d'entree a CHAQUE requete du
    visualiseur : une etude, ce sont des centaines d'appels dicom-web. Y
    brancher increment_token_usage epuiserait un jeton de 50 usages en une
    seule consultation. Le decompte reste ou il etait, sur /share/ (une fois
    par ouverture de lien) et dans /tokens/validate.
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
        logger.warning("Partage refuse : jeton inconnu (%s...)", token[:8])
        return Response(status_code=403)

    if time.time() >= donnees.get("expires_at", 0):
        delete_token(token)
        logger.warning("Partage refuse : jeton expire (%s...)", token[:8])
        return Response(status_code=403)

    # Pas de test de plafond ici. Il y en avait un, et il coupait la derniere
    # consultation autorisee : l'ouverture qui atteint le quota porte
    # current_uses A max_uses, et ce test refusait alors toutes les requetes du
    # visualiseur qui venait de s'ouvrir. La limite est appliquee par la duree
    # de vie du jeton -- share_redirect la ramene a SURSIS_DERNIERE_OUVERTURE
    # des le plafond atteint. Un jeton epuise depuis assez longtemps n'existe
    # plus, et le test d'existence ci-dessus suffit a le refuser.

    # --- PORTEE : le jeton doit couvrir CE QUI EST DEMANDE ----------------
    #
    # Ce point d'entree ne verifiait que la validite du jeton, jamais son
    # perimetre. Mesure le 2026-08-29, depuis un navigateur SANS aucun cookie,
    # muni du seul lien de partage d'une etude :
    #
    #   GET /dicom-web/studies?limit=101&...&token=<jeton>  ->  200
    #   -> la liste des 209 etudes, avec les noms de patients.
    #
    # C'est la faille du 2026-08-27, revenue par la porte que nous avons
    # ouverte en refaisant le partage : Orthanc restreint bien les ressources
    # NOMMEES, mais une requete QIDO d'ENUMERATION ne nomme rien -- il n'y a
    # aucune ressource a comparer, et elle passe.
    #
    # La portee se verifie donc ici, sur l'URI, avant qu'Orthanc ne voie la
    # requete. Regle : un lien de partage donne acces a UNE etude, jamais a un
    # inventaire. Tout ce qui ne designe pas explicitement l'etude couverte est
    # refuse.
    uri = request.headers.get("x-original-uri", "")
    if not _partage_couvre_uri(donnees, uri):
        logger.warning(
            "Partage refuse : hors perimetre (%s...) %s",
            token[:8], urllib.parse.urlparse(uri).path,
        )
        return Response(status_code=403)

    return Response(status_code=204)


def _etudes_du_jeton(donnees: dict) -> set:
    """UID d'etude DICOM que ce jeton couvre."""
    uids = set()
    for r in donnees.get("resources", []):
        uid = r.get("DicomUid") or r.get("dicom-uid") or ""
        if uid:
            uids.add(uid)
    return uids


def _partage_couvre_uri(donnees: dict, uri: str) -> bool:
    """Le jeton autorise-t-il cette URI precise ?

    Fail-closed : tout ce qui n'est pas explicitement reconnu est refuse. Une
    URI inattendue doit couter un partage qui ne s'ouvre pas, jamais un
    inventaire qui s'echappe.
    """
    autorisees = _etudes_du_jeton(donnees)
    if not autorisees:
        return False

    parsed = urllib.parse.urlparse(uri or "")
    chemin = parsed.path
    params = urllib.parse.parse_qs(parsed.query)

    # DICOMweb : /dicom-web/studies/<StudyInstanceUID>/...
    # Le segment qui suit « studies » doit etre l'etude partagee. Une
    # enumeration -- /dicom-web/studies tout court, avec ou sans filtres --
    # n'a pas ce segment : elle est refusee, et c'est tout l'objet du
    # correctif.
    prefixe = "/dicom-web/studies"
    if chemin.startswith(prefixe):
        reste = chemin[len(prefixe):].lstrip("/")
        if not reste:
            return False           # enumeration
        return reste.split("/")[0] in autorisees

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