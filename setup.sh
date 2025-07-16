#!/bin/bash
# =============================================================================
# ORTHANC-AUTHELIA SETUP SCRIPT
# =============================================================================
# Interactive setup script for ORTHANC-AUTHELIA deployment
# Generates configuration files from templates using envsubst
# =============================================================================

set -e

echo "ORTHANC-AUTHELIA Setup Script"
echo "============================="
echo

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Function to prompt for input with default value
prompt_input() {
    local prompt="$1"
    local default="$2"
    local value
    
    if [[ -n "$default" ]]; then
        read -p "$prompt (default: $default): " value
        echo "${value:-$default}"
    else
        read -p "$prompt: " value
        echo "$value"
    fi
}

# Function to prompt for password
prompt_password() {
    local prompt="$1"
    local password
    
    while true; do
        # Use stderr for the prompt to ensure it's displayed
        >&2 echo "$prompt (input hidden):"
        read -s password
        >&2 echo  # Move to next line on stderr
        
        if [[ -n "$password" ]]; then
            break
        fi
        >&2 echo "ERROR: Password cannot be empty. Please try again."
    done
    
    echo "$password"
}

# Function to generate secure random string
generate_secret() {
    openssl rand -base64 48
}

# =============================================================================
# MAIN SETUP PROCESS
# =============================================================================

# Check if .env already exists
REUSE_SECRETS=false
if [[ -f ".env" ]]; then
    echo "WARNING: .env file already exists!"
    
    # Try to extract existing Authelia secrets
    if grep -q "AUTHELIA_SESSION_SECRET" .env && grep -q "AUTHELIA_STORAGE_ENCRYPTION_KEY" .env; then
        echo "Found existing Authelia secrets in .env"
        
        # Check if Authelia database exists
        if [[ -f "services/authelia/config/db.sqlite3" ]]; then
            echo "Found existing Authelia database"
            read -p "Reuse existing Authelia encryption keys to preserve database? (Y/n): " reuse
            if [[ ! "$reuse" =~ ^[Nn]$ ]]; then
                REUSE_SECRETS=true
                EXISTING_SESSION_SECRET=$(grep "^AUTHELIA_SESSION_SECRET=" .env | cut -d= -f2)
                EXISTING_STORAGE_KEY=$(grep "^AUTHELIA_STORAGE_ENCRYPTION_KEY=" .env | cut -d= -f2)
                EXISTING_JWT_SECRET=$(grep "^AUTHELIA_JWT_SECRET=" .env | cut -d= -f2)
            else
                echo
                echo "WARNING: New encryption keys will be generated."
                echo "The existing Authelia database will need to be removed:"
                echo "rm services/authelia/config/db.sqlite3"
                echo
                read -p "Press Enter to continue..."
            fi
        fi
    fi
    
    read -p "Overwrite .env file? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Setup cancelled."
        exit 0
    fi
fi

echo "Step 1: Collecting configuration..."
echo "================================="

# Collect basic configuration
DOMAIN=$(prompt_input "Enter your PACS domain" "pacs.example.com")
PORT=$(prompt_input "Enter external HTTP port" "30080")

# Database configuration
echo
echo "Database Configuration:"
DB_MODE=$(prompt_input "Database mode (external/internal)" "internal")

if [[ "$DB_MODE" == "internal" ]]; then
    POSTGRES_HOST="postgres"
    
    # Check for existing PostgreSQL volume
    if docker volume ls --format '{{.Name}}' | grep -q "postgres_data\|orthanc-authelia_postgres_data\|pacs-orthanc-authelia_postgres_data"; then
        echo
        echo "WARNING: Existing PostgreSQL volume detected!"
        echo "This volume may contain an existing database with different credentials."
        echo
        echo "Options:"
        echo "1) Keep existing database (you must know the existing credentials)"
        echo "2) Create new database (existing data will be lost)"
        echo
        read -p "Your choice (1/2): " db_choice
        
        if [[ "$db_choice" == "1" ]]; then
            echo
            echo "Enter the EXISTING database credentials:"
            POSTGRES_DB=$(prompt_input "Existing database name" "orthanc")
            POSTGRES_USER=$(prompt_input "Existing database username" "orthanc")
            POSTGRES_PASSWORD=$(prompt_password "Existing database password")
        else
            echo
            echo "The existing PostgreSQL volume will be removed when you run:"
            echo "docker-compose down && docker volume rm <volume_name>"
            echo
            echo "Enter NEW database credentials:"
            POSTGRES_DB=$(prompt_input "New database name" "orthanc")
            POSTGRES_USER=$(prompt_input "New database username" "orthanc")
            POSTGRES_PASSWORD=$(prompt_password "New database password")
            echo
            echo "IMPORTANT: Remember to remove the old volume before starting services!"
        fi
    else
        POSTGRES_DB=$(prompt_input "Database name" "orthanc")
        POSTGRES_USER=$(prompt_input "Database username" "orthanc")
        POSTGRES_PASSWORD=$(prompt_password "Database password")
    fi
else
    POSTGRES_HOST=$(prompt_input "PostgreSQL hostname" "database")
    POSTGRES_DB=$(prompt_input "Database name" "orthanc")
    POSTGRES_USER=$(prompt_input "Database username" "orthanc")
    POSTGRES_PASSWORD=$(prompt_password "Database password")
fi

# Authentication configuration
echo
echo "Authentication Configuration:"
AUTH_USERNAME=$(prompt_input "Auth service username" "share-user")
AUTH_PASSWORD=$(prompt_password "Auth service password")
LANGUAGE=$(prompt_input "Interface language (en/fr)" "en")

# User accounts
echo
echo "User Accounts:"
ADMIN_EMAIL=$(prompt_input "Admin email" "admin@example.com")
ADMIN_PASSWORD=$(prompt_password "Admin password")

DOCTOR_EMAIL=$(prompt_input "Doctor email" "doctor@example.com")
DOCTOR_PASSWORD=$(prompt_password "Doctor password")

EXTERNAL_EMAIL=$(prompt_input "External user email" "external@example.com")
EXTERNAL_PASSWORD=$(prompt_password "External user password")

echo
echo "Step 2: Generating secure secrets..."
echo "===================================="

# Generate or reuse Authelia secrets
if [[ "$REUSE_SECRETS" == "true" ]]; then
    SESSION_SECRET="$EXISTING_SESSION_SECRET"
    STORAGE_KEY="$EXISTING_STORAGE_KEY"
    JWT_SECRET="$EXISTING_JWT_SECRET"
    echo "Reusing existing Authelia secrets to preserve database"
else
    SESSION_SECRET=$(generate_secret)
    STORAGE_KEY=$(generate_secret)
    JWT_SECRET=$(generate_secret)
    echo "Generated new secure secrets for Authelia"
fi

echo
echo "Step 3: Creating configuration files..."
echo "======================================"

# Create .env file
echo "Creating .env file..."
cat > .env << EOF
# =============================================================================
# ORTHANC-AUTHELIA ENVIRONMENT CONFIGURATION
# =============================================================================
# Generated by setup script

# =============================================================================
# DOMAIN & NETWORK CONFIGURATION
# =============================================================================
AUTHELIA_DOMAIN=${DOMAIN}
NGINX_EXTERNAL_PORT=${PORT}

# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# =============================================================================
# REDIS CONFIGURATION
# =============================================================================
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# =============================================================================
# AUTHENTICATION CONFIGURATION
# =============================================================================
AUTH_USERNAME=${AUTH_USERNAME}
AUTH_PASSWORD=${AUTH_PASSWORD}
LANGUAGE=${LANGUAGE}

# =============================================================================
# AUTHELIA SECURITY SECRETS
# =============================================================================
AUTHELIA_SESSION_SECRET=${SESSION_SECRET}
AUTHELIA_STORAGE_ENCRYPTION_KEY=${STORAGE_KEY}
AUTHELIA_JWT_SECRET=${JWT_SECRET}
AUTHELIA_LOG_LEVEL=info

# =============================================================================
# USER ACCOUNTS
# =============================================================================
AUTHELIA_ADMIN_USERNAME=${ADMIN_EMAIL}
AUTHELIA_ADMIN_PASSWORD=${ADMIN_PASSWORD}
AUTHELIA_ADMIN_ROLE=admin

AUTHELIA_DOCTOR_USERNAME=${DOCTOR_EMAIL}
AUTHELIA_DOCTOR_PASSWORD=${DOCTOR_PASSWORD}
AUTHELIA_DOCTOR_ROLE=doctor

AUTHELIA_EXTERNAL_USERNAME=${EXTERNAL_EMAIL}
AUTHELIA_EXTERNAL_PASSWORD=${EXTERNAL_PASSWORD}
AUTHELIA_EXTERNAL_ROLE=external-viewer

# =============================================================================
# OPTIONAL OVERRIDES
# =============================================================================
TZ=Europe/Paris
OHIF_PUBLIC_URL=/ohif/
EOF

# Export variables for envsubst
export AUTHELIA_DOMAIN="${DOMAIN}"
export AUTHELIA_LOG_LEVEL="info"
export AUTHELIA_SESSION_SECRET="${SESSION_SECRET}"
export AUTHELIA_STORAGE_ENCRYPTION_KEY="${STORAGE_KEY}"
export AUTHELIA_JWT_SECRET="${JWT_SECRET}"
export REDIS_HOST="redis"
export REDIS_PORT="6379"
export REDIS_DB="0"
export POSTGRES_HOST="${POSTGRES_HOST}"
export POSTGRES_DB="${POSTGRES_DB}"
export POSTGRES_USER="${POSTGRES_USER}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD}"
export AUTH_USERNAME="${AUTH_USERNAME}"
export AUTH_PASSWORD="${AUTH_PASSWORD}"

# Create configuration files from templates
echo "Generating Authelia configuration..."
mkdir -p services/authelia/config
envsubst < services/authelia/config/configuration.yml.example > services/authelia/config/configuration.yml

echo "Generating Orthanc configuration..."
envsubst < services/orthanc/config/orthanc.json.example > services/orthanc/config/orthanc.json

# Function to hash password using Docker
hash_password() {
    local password=$1
    docker run --rm authelia/authelia:latest authelia crypto hash generate argon2 --password "$password" 2>/dev/null | grep "Digest: " | cut -d' ' -f2
}

# Create users_database.yml
echo "Creating Authelia user database..."
cat > services/authelia/config/users_database.yml << EOF
---
users:
  ${ADMIN_EMAIL}:
    displayname: "PACS Administrator"
    password: "$(hash_password "${ADMIN_PASSWORD}")"
    email: ${ADMIN_EMAIL}
    groups:
      - admin
      
  ${DOCTOR_EMAIL}:
    displayname: "Medical Doctor"
    password: "$(hash_password "${DOCTOR_PASSWORD}")"
    email: ${DOCTOR_EMAIL}
    groups:
      - doctor
      
  ${EXTERNAL_EMAIL}:
    displayname: "External User"
    password: "$(hash_password "${EXTERNAL_PASSWORD}")"
    email: ${EXTERNAL_EMAIL}
    groups:
      - external
EOF

echo
echo "Step 4: Deployment configuration..."
echo "=================================="

# Choose deployment mode
if [[ "$DB_MODE" == "external" ]]; then
    COMPOSE_FILE="docker-compose.yml"
    echo "Using external PostgreSQL database"
    echo "Selected compose file: $COMPOSE_FILE"
else
    COMPOSE_FILE="docker-compose.standalone.yml"
    echo "Using internal PostgreSQL 15 database"
    echo "Selected compose file: $COMPOSE_FILE"
fi

echo
echo "Setup completed successfully!"
echo "============================"
echo
echo "Configuration Summary:"
echo "- Domain: $DOMAIN"
echo "- Port: $PORT"
echo "- Database: $DB_MODE ($POSTGRES_HOST)"
echo "- Language: $LANGUAGE"
echo
echo "User Accounts:"
echo "- Admin: $ADMIN_EMAIL"
echo "- Doctor: $DOCTOR_EMAIL"
echo "- External: $EXTERNAL_EMAIL"
echo
echo "Generated Files:"
echo "- .env (environment variables)"
echo "- services/authelia/config/configuration.yml"
echo "- services/authelia/config/users_database.yml"
echo "- services/orthanc/config/orthanc.json"
echo
echo "Next Steps:"
echo "1. Start services: docker-compose -f $COMPOSE_FILE up -d"
echo "2. Access your PACS at: https://$DOMAIN"
echo "3. Login with any of the created user accounts"
echo "4. Admin token management: https://$DOMAIN/auth/tokens/manage"
echo
echo "IMPORTANT:"
echo "- Keep your .env file secure (contains passwords and secrets)"
echo "- Change default passwords in production"
echo "- Configure SSL certificates for production use"
echo