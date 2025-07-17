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
import urllib.parse
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

def render_template(template_name: str, **kwargs) -> str:
    """Render HTML template with provided variables"""
    template_path = f"/app/templates/{template_name}"
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
        
        # Add font_awesome_cdn to kwargs
        kwargs["font_awesome_cdn"] = FONT_AWESOME_CDN
        
        # Replace placeholders safely by avoiding CSS braces
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in template_content:
                template_content = template_content.replace(placeholder, str(value))
        
        # Replace double braces with single braces for CSS
        template_content = template_content.replace("{{", "{").replace("}}", "}")
        
        return template_content
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
    
    back_link_html = f'<a href="{back_link}">{TRANSLATIONS["ui"]["back_to_pacs"]}</a>' if back_link else ""
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
    
    # Get user info from request body (sent by Authorization plugin)
    token_value = normalize_bearer_token(body.get("token-value", ""))
    
    # The token-value contains the group from nginx headers
    group = token_value
    
    # Map Authelia group to user permissions
    if "admin" in group:
        user_name = TRANSLATIONS["ui"]["administrator"]
        permissions = ["view", "download", "upload", "delete", "modify", "anonymize", "share", "send", "settings", "edit-labels"]
    elif "doctor" in group:
        user_name = TRANSLATIONS["ui"]["doctor"]
        permissions = ["view", "download", "upload", "share", "send", "edit-labels"]
    else:
        user_name = TRANSLATIONS["ui"]["external_user"]
        permissions = ["view", "download"]
    
    return JSONResponse(content={
        "name": user_name,
        "authorized-labels": ["*"],  # Access to all labels
        "permissions": permissions,
        "validity": CACHE_VALIDITY_SHARE_TOKEN
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
    
    return JSONResponse(content={
        "token-type": token_type,
        "redirect-url": redirect_url
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
        
        # Prepare template variables from translations
        ui_translations = TRANSLATIONS["ui"]
        template_vars = {
            "TITLE": ui_translations["title"],
            "SUBTITLE": ui_translations["subtitle"],
            "REFRESH_BUTTON": ui_translations["refresh_button"],
            "TOTAL_TOKENS": ui_translations["total_tokens"],
            "OHIF_VIEWER": ui_translations["ohif_viewer"],
            "INSTANT_LINKS": ui_translations["instant_links"],
            "HIGH_USAGE": ui_translations["high_usage"],
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
            "ERROR_OCCURRED": ui_translations["error_occurred"]
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
        # VolView 3D Viewer
        viewer_url = f"{base_url}/volview/?StudyInstanceUIDs={study_uid_encoded}&token={token}&_cb={cache_bust}"
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