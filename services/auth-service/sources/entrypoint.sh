#!/bin/bash

# =============================================================================
# ORTHANC-AUTHELIA AUTH-SERVICE ENTRYPOINT
# =============================================================================
# This script configures and starts the auth-service with comprehensive
# documentation of all available environment variables and their usage.

set -e


# =============================================================================
# CONFIGURATION DOCUMENTATION
# =============================================================================

show_help() {
    cat << EOF
===============================================================================
                    ORTHANC-AUTHELIA AUTH-SERVICE v1.0.2                  
                        Environment Variables Guide                        
===============================================================================

BASIC CONFIGURATION
┌─────────────────────────────────────────────────────────────────────────┐
│ AUTH_USERNAME          │ API username for internal auth                 │
│ AUTH_PASSWORD          │ API password for internal auth                 │
│ REDIS_HOST             │ Redis hostname (default: redis)               │
│ REDIS_PORT             │ Redis port (default: 6379)                    │
│ REDIS_DB               │ Redis database number (default: 0)            │
│ LOG_LEVEL              │ Logging level (DEBUG/INFO/WARNING/ERROR)      │
└─────────────────────────────────────────────────────────────────────────┘

TOKEN CONFIGURATION
┌─────────────────────────────────────────────────────────────────────────┐
│ DEFAULT_TOKEN_MAX_USES         │ Max uses per token (default: 50)       │
│ DEFAULT_TOKEN_VALIDITY_SECONDS │ Token validity in seconds (def: 604800)│
│ UNLIMITED_TOKEN_DURATION       │ Duration for unlimited tokens (def: 1y)│
│ CACHE_VALIDITY_USER_SESSION    │ User session cache TTL (def: 300s)     │
│ CACHE_VALIDITY_SHARE_TOKEN     │ Share token cache TTL (def: 60s)       │
│ AUDIT_RETENTION_DAYS          │ Days to retain audit logs (def: 90)    │
└─────────────────────────────────────────────────────────────────────────┘

USER INTERFACE
┌─────────────────────────────────────────────────────────────────────────┐
│ LANGUAGE               │ Interface language (en/fr) (default: en)      │
│ FONT_AWESOME_CDN       │ CDN URL for Font Awesome icons                │
│ JS_REFRESH_INTERVAL    │ Auto-refresh interval in ms (def: 30000)      │
│ JS_DEBUG_MODE          │ Enable JS debug mode (true/false)             │
└─────────────────────────────────────────────────────────────────────────┘

USAGE EXAMPLES
┌─────────────────────────────────────────────────────────────────────────┐
│ # Basic setup                                                           │
│ AUTH_USERNAME=share-user                                                │
│ AUTH_PASSWORD=secure-password-123                                       │
│ REDIS_HOST=redis                                                        │
│                                                                         │
│ # Token customization                                                   │
│ DEFAULT_TOKEN_MAX_USES=100                                              │
│ DEFAULT_TOKEN_VALIDITY_SECONDS=1209600  # 14 days                      │
│                                                                         │
│ # UI Language                                                           │
│ LANGUAGE=en  # English interface (default)                             │
│                                                                         │
│ # Performance tuning                                                    │
│ CACHE_VALIDITY_USER_SESSION=600  # 10 minutes                          │
│ CACHE_VALIDITY_SHARE_TOKEN=120   # 2 minutes                           │
│                                                                         │
│ # Debug mode                                                            │
│ LOG_LEVEL=DEBUG                                                         │
│ JS_DEBUG_MODE=true                                                      │
└─────────────────────────────────────────────────────────────────────────┘

INTEGRATION POINTS
┌─────────────────────────────────────────────────────────────────────────┐
│ • Nginx calls /tokens/validate for auth verification                   │
│ • Orthanc Explorer 2 calls /tokens/ for token creation                 │
│ • Admin interface available at /auth/tokens/manage                     │
│ • Share interface available at /share/?token=xxx                       │
│ • Health check endpoint at /health                                     │
└─────────────────────────────────────────────────────────────────────────┘

SECURITY NOTES
┌─────────────────────────────────────────────────────────────────────────┐
│ • Always change AUTH_PASSWORD in production                            │
│ • Use strong Redis passwords if exposed                                │
│ • Monitor audit logs for suspicious activity                           │
│ • Consider lower token validity for sensitive environments             │
└─────────────────────────────────────────────────────────────────────────┘

EOF
}

# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================

validate_config() {
    local errors=0
    
    # Required variables
    if [[ -z "${AUTH_USERNAME:-}" ]]; then
        echo "ERROR: AUTH_USERNAME is required"
        errors=$((errors + 1))
    fi
    
    if [[ -z "${AUTH_PASSWORD:-}" ]] || [[ "${AUTH_PASSWORD}" == "change_this_password_in_production" ]]; then
        echo "ERROR: AUTH_PASSWORD must be set and changed from default"
        errors=$((errors + 1))
    fi
    
    # Redis connectivity
    if ! nc -z "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}" 2>/dev/null; then
        echo "WARNING: Cannot connect to Redis at ${REDIS_HOST:-redis}:${REDIS_PORT:-6379}"
        echo "Service will retry on startup"
    fi
    
    # Numeric validations
    if [[ -n "${DEFAULT_TOKEN_MAX_USES:-}" ]] && ! [[ "${DEFAULT_TOKEN_MAX_USES}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: DEFAULT_TOKEN_MAX_USES must be a number"
        errors=$((errors + 1))
    fi
    
    if [[ -n "${DEFAULT_TOKEN_VALIDITY_SECONDS:-}" ]] && ! [[ "${DEFAULT_TOKEN_VALIDITY_SECONDS}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: DEFAULT_TOKEN_VALIDITY_SECONDS must be a number"
        errors=$((errors + 1))
    fi
    
    # Language validation
    if [[ -n "${LANGUAGE:-}" ]] && [[ ! "${LANGUAGE}" =~ ^(en|fr)$ ]]; then
        echo "WARNING: LANGUAGE '${LANGUAGE}' not supported, falling back to 'en'"
        echo "Supported languages: en, fr"
    fi
    
    # Check if translation file exists
    if [[ -n "${LANGUAGE:-}" ]] && [[ ! -f "/app/translations/${LANGUAGE}.json" ]]; then
        echo "WARNING: Translation file '/app/translations/${LANGUAGE}.json' not found"
        echo "Available translations: $(ls -1 /app/translations/*.json 2>/dev/null | sed 's|.*/||;s|\.json||' | tr '\n' ' ')"
    fi
    
    return $errors
}

# =============================================================================
# CONFIGURATION SUMMARY
# =============================================================================

show_config() {
    echo "==============================================================================="
    echo "                          CURRENT CONFIGURATION                           "
    echo "==============================================================================="
    echo
    echo "Authentication:"
    echo "  Username: ${AUTH_USERNAME:-share-user}"
    echo "  Password: ${AUTH_PASSWORD:+[SET]} ${AUTH_PASSWORD:-[NOT SET]}"
    echo
    echo "Redis:"
    echo "  Host: ${REDIS_HOST:-redis}"
    echo "  Port: ${REDIS_PORT:-6379}"
    echo "  Database: ${REDIS_DB:-0}"
    echo
    echo "Token Defaults:"
    echo "  Max Uses: ${DEFAULT_TOKEN_MAX_USES:-50}"
    echo "  Validity: ${DEFAULT_TOKEN_VALIDITY_SECONDS:-604800}s ($(( ${DEFAULT_TOKEN_VALIDITY_SECONDS:-604800} / 86400 )) days)"
    echo "  Cache TTL: ${CACHE_VALIDITY_SHARE_TOKEN:-60}s"
    echo
    echo "System:"
    echo "  Log Level: ${LOG_LEVEL:-INFO}"
    echo "  Debug Mode: ${JS_DEBUG_MODE:-false}"
    echo "  Audit Retention: ${AUDIT_RETENTION_DAYS:-90} days"
    echo
    echo "Interface:"
    echo "  Language: ${LANGUAGE:-en}"
    echo "  Translations: $(ls -1 /app/translations/*.json 2>/dev/null | wc -l) available"
    echo
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================

main() {
    # Handle help request
    if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
        show_help
        exit 0
    fi
    
    # Show banner
    echo "==============================================================================="
    echo "                    ORTHANC-AUTHELIA AUTH-SERVICE v1.0.2                  "
    echo "                              Starting Up...                              "
    echo "==============================================================================="
    echo
    
    # Validate configuration
    echo "Validating configuration..."
    if ! validate_config; then
        echo "Configuration validation failed"
        echo "Run with --help for configuration guide"
        exit 1
    fi
    echo "Configuration validated"
    echo
    
    # Show current configuration
    show_config
    
    # Start the service
    echo "Starting auth-service..."
    echo "Health check available at: http://localhost:8000/health"
    echo "Token management at: http://localhost:8000/tokens/manage"
    echo "API documentation at: http://localhost:8000/docs"
    echo
    
    # Execute the main command
    exec "$@"
}

# Run main function with all arguments
main "$@"