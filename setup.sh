#!/bin/bash
# =============================================================================
# ORTHANC-AUTHELIA SETUP SCRIPT
# =============================================================================
# Interactive setup script for ORTHANC-AUTHELIA deployment
# Generates .env file and user database configuration
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

# Database configuration
echo
echo "Database Configuration:"
DB_MODE=$(prompt_input "Database mode (external/internal)" "internal")

if [[ "$DB_MODE" == "internal" ]]; then
    echo "Using internal PostgreSQL 15 Alpine container with local data directory"
    
    # Check for existing data directory
    if [[ -d "./data/postgres" ]] && [[ -n "$(ls -A ./data/postgres 2>/dev/null)" ]]; then
        echo
        echo "WARNING: Existing PostgreSQL data directory detected!"
        echo "Directory ./data/postgres contains existing database files."
        echo
        echo "Options:"
        echo "1) Keep existing database (you must know the existing credentials)"
        echo "2) Create new database (existing data will be moved to backup)"
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
            echo "The existing data will be moved to ./data/postgres.backup.$(date +%Y%m%d_%H%M%S)"
            echo
            echo "Enter NEW database credentials:"
            POSTGRES_DB=$(prompt_input "New database name" "orthanc")
            POSTGRES_USER=$(prompt_input "New database username" "orthanc")
            POSTGRES_PASSWORD=$(prompt_password "New database password")
            
            # Backup existing data
            if [[ -d "./data/postgres" ]]; then
                BACKUP_DIR="./data/postgres.backup.$(date +%Y%m%d_%H%M%S)"
                mv "./data/postgres" "$BACKUP_DIR"
                echo "Existing data backed up to: $BACKUP_DIR"
            fi
        fi
    else
        echo "Creating new PostgreSQL database"
        POSTGRES_DB=$(prompt_input "Database name" "orthanc")
        POSTGRES_USER=$(prompt_input "Database username" "orthanc")
        POSTGRES_PASSWORD=$(prompt_password "Database password")
    fi
    
    # Ensure data directory exists
    mkdir -p ./data/postgres
    
else
    echo "Using external PostgreSQL database"
    POSTGRES_HOST=$(prompt_input "PostgreSQL hostname" "database")
    POSTGRES_DB=$(prompt_input "Database name" "orthanc")
    POSTGRES_USER=$(prompt_input "Database username" "orthanc")
    POSTGRES_PASSWORD=$(prompt_password "Database password")
fi

# SSL Configuration
echo
echo "SSL Certificate Configuration:"
SSL_MODE=$(prompt_input "SSL mode (letsencrypt/manual/selfsigned/none)" "selfsigned")

case "$SSL_MODE" in
    "letsencrypt")
        echo "Let's Encrypt automatic certificate generation"
        EMAIL=$(prompt_input "Email for Let's Encrypt notifications" "admin@${DOMAIN}")
        echo "IMPORTANT: Domain $DOMAIN must point to this server for Let's Encrypt validation"
        ;;
    "manual")
        echo "Manual certificate configuration"
        CERT_PATH=$(prompt_input "Path to certificate file" "./ssl/cert.pem")
        KEY_PATH=$(prompt_input "Path to private key file" "./ssl/key.pem")
        
        # Verify certificate files exist
        if [[ ! -f "$CERT_PATH" ]]; then
            echo "WARNING: Certificate file not found: $CERT_PATH"
            echo "Please ensure the certificate file exists before starting services"
        fi
        if [[ ! -f "$KEY_PATH" ]]; then
            echo "WARNING: Private key file not found: $KEY_PATH"
            echo "Please ensure the private key file exists before starting services"
        fi
        ;;
    "selfsigned")
        echo "Self-signed certificate (development only)"
        echo "WARNING: Self-signed certificates are not trusted by browsers"
        ;;
    "none")
        echo "No SSL - HTTP only (not recommended for production)"
        echo "WARNING: Traffic will not be encrypted"
        ;;
    *)
        echo "Invalid SSL mode, defaulting to self-signed"
        SSL_MODE="selfsigned"
        ;;
esac

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
# Generated by setup script on $(date)

# =============================================================================
# DOMAIN CONFIGURATION
# =============================================================================
DOMAIN=${DOMAIN}
SSL_ENABLED=${SSL_ENABLED:-false}

# =============================================================================
# AUTHENTICATION CONFIGURATION
# =============================================================================
# Auth-service API credentials for Orthanc Authorization plugin
AUTH_USERNAME=${AUTH_USERNAME}
AUTH_PASSWORD=${AUTH_PASSWORD}

# Interface language (en/fr)
LANGUAGE=${LANGUAGE}

# =============================================================================
# AUTHELIA SECURITY SECRETS
# =============================================================================
# Generated secure values - keep these secret!
AUTHELIA_SESSION_SECRET=${SESSION_SECRET}
AUTHELIA_STORAGE_ENCRYPTION_KEY=${STORAGE_KEY}
AUTHELIA_JWT_SECRET=${JWT_SECRET}

# =============================================================================
# SSL CONFIGURATION
# =============================================================================
SSL_MODE=${SSL_MODE}
EOF

# Add SSL-specific variables if needed
if [[ "$SSL_MODE" == "letsencrypt" ]]; then
    cat >> .env << EOF
LETSENCRYPT_EMAIL=${EMAIL}
SSL_ENABLED=true
EOF
elif [[ "$SSL_MODE" == "manual" ]] || [[ "$SSL_MODE" == "selfsigned" ]]; then
    cat >> .env << EOF
SSL_ENABLED=true
EOF
elif [[ "$SSL_MODE" == "none" ]]; then
    cat >> .env << EOF
SSL_ENABLED=false
EOF
fi

cat >> .env << EOF

# =============================================================================
# SYSTEM CONFIGURATION
# =============================================================================
# Timezone
TZ=Europe/Paris
EOF

# Update Authelia configuration with domain
echo "Updating Authelia configuration..."
mkdir -p services/authelia/config
if [[ -f "services/authelia/config/configuration.yml" ]]; then
    # Update domain in existing configuration
    sed -i "s/pacs\.example\.com/${DOMAIN}/g" services/authelia/config/configuration.yml
    echo "Updated domain in Authelia configuration"
else
    echo "WARNING: services/authelia/config/configuration.yml not found"
    echo "Please manually update the domain in the configuration file"
fi

# Handle SSL certificate generation/setup
echo "Setting up SSL certificates..."
mkdir -p ./ssl

case "$SSL_MODE" in
    "selfsigned")
        echo "Generating self-signed certificate..."
        openssl req -x509 -newkey rsa:4096 -nodes \
            -keyout ./ssl/pacs.key \
            -out ./ssl/pacs.crt \
            -days 365 \
            -subj "/CN=${DOMAIN}/O=ORTHANC-AUTHELIA/C=FR" 2>/dev/null
        echo "Self-signed certificate generated in ./ssl/"
        echo "  - Certificate: ./ssl/pacs.crt"
        echo "  - Private key: ./ssl/pacs.key"
        ;;
    "manual")
        echo "Manual certificate mode selected"
        if [[ -f "$CERT_PATH" ]] && [[ -f "$KEY_PATH" ]]; then
            # Copy certificates to ssl directory with correct names
            cp "$CERT_PATH" ./ssl/pacs.crt
            cp "$KEY_PATH" ./ssl/pacs.key
            echo "Certificates copied to ./ssl/"
            echo "  - Certificate: ./ssl/pacs.crt"
            echo "  - Private key: ./ssl/pacs.key"
        else
            echo "WARNING: Certificate files not found, you'll need to provide them manually"
            echo "Expected files: ./ssl/pacs.crt and ./ssl/pacs.key"
        fi
        ;;
    "letsencrypt")
        echo "Let's Encrypt mode selected"
        echo "Certificates will be generated when you start the services"
        echo "Make sure domain $DOMAIN points to this server"
        ;;
    "none")
        echo "SSL disabled - HTTP only mode"
        ;;
esac

# Function to create Let's Encrypt enabled docker-compose
create_letsencrypt_compose() {
    echo "Creating Let's Encrypt docker-compose configuration..."
    
    # Copy base example and add certbot service
    cp docker-compose.example.yml docker-compose.ssl.yml
    
    # Add certbot service and SSL volumes
    cat >> docker-compose.ssl.yml << 'EOF'

  # =============================================================================
  # SSL CERTIFICATE MANAGEMENT
  # =============================================================================
  
  certbot:
    image: certbot/certbot:latest
    container_name: orthanc-certbot
    volumes:
      - ./ssl:/etc/letsencrypt
      - ./data/certbot:/var/www/certbot
    command: |
      sh -c "
        if [ ! -f /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ]; then
          certbot certonly --webroot --webroot-path=/var/www/certbot \
            --email ${LETSENCRYPT_EMAIL} --agree-tos --no-eff-email \
            --keep-until-expiring -d ${DOMAIN}
        fi
        # Copy certificates to expected location
        if [ -f /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ]; then
          cp /etc/letsencrypt/live/${DOMAIN}/fullchain.pem /etc/letsencrypt/cert.pem
          cp /etc/letsencrypt/live/${DOMAIN}/privkey.pem /etc/letsencrypt/key.pem
        fi
      "
    networks:
      - orthanc-network
    depends_on:
      - nginx

# =============================================================================
# ADDITIONAL VOLUMES FOR SSL
# =============================================================================

volumes:
  postgres_data:
    name: orthanc_postgres_data
  certbot_data:
    name: orthanc_certbot_data
EOF

    # Update nginx service to add certbot volumes
    sed -i '/nginx:/,/networks:/ {
        /volumes:/a\
      - ./data/certbot:/var/www/certbot:ro
    }' docker-compose.ssl.yml
}

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

# Choose deployment mode and create appropriate docker-compose file
if [[ "$DB_MODE" == "external" ]]; then
    COMPOSE_FILE="docker-compose.yml"
    echo "Using external PostgreSQL database"
    echo "Selected compose file: $COMPOSE_FILE"
    echo
    echo "IMPORTANT: Make sure your external PostgreSQL database is running"
    echo "and accessible with the provided credentials."
else
    COMPOSE_FILE="docker-compose.example.yml"
    echo "Using internal PostgreSQL 15 Alpine database"
    
    # Create SSL-aware docker-compose file
    if [[ "$SSL_MODE" == "letsencrypt" ]]; then
        COMPOSE_FILE="docker-compose.ssl.yml"
        echo "Creating Let's Encrypt enabled docker-compose file: $COMPOSE_FILE"
        create_letsencrypt_compose
    else
        echo "Selected compose file: $COMPOSE_FILE"
    fi
    
    echo
    echo "IMPORTANT: Database data will be stored in ./data/postgres/"
    if [[ "$SSL_MODE" != "none" ]]; then
        echo "IMPORTANT: SSL certificates will be in ./ssl/"
    fi
fi

echo
echo "Setup completed successfully!"
echo "============================"
echo
echo "Configuration Summary:"
echo "- Domain: $DOMAIN"
echo "- SSL Mode: $SSL_MODE"
echo "- Database: $DB_MODE"
if [[ "$DB_MODE" == "internal" ]]; then
    echo "- Database data: ./data/postgres/"
else
    echo "- Database host: $POSTGRES_HOST"
fi
echo "- Language: $LANGUAGE"
echo
echo "User Accounts:"
echo "- Admin: $ADMIN_EMAIL"
echo "- Doctor: $DOCTOR_EMAIL"
echo "- External: $EXTERNAL_EMAIL"
echo
echo "Generated Files:"
echo "- .env (environment variables)"
echo "- services/authelia/config/users_database.yml"
if [[ "$DB_MODE" == "internal" ]]; then
    echo "- data/postgres/ (database directory created)"
fi
case "$SSL_MODE" in
    "selfsigned")
        echo "- ssl/pacs.crt, ssl/pacs.key (self-signed certificates)"
        ;;
    "manual")
        echo "- ssl/pacs.crt, ssl/pacs.key (manual certificates)"
        ;;
    "letsencrypt")
        echo "- docker-compose.ssl.yml (Let's Encrypt configuration)"
        echo "- data/certbot/ (certificate challenges)"
        ;;
esac
echo
echo "Next Steps:"
if [[ "$SSL_MODE" == "letsencrypt" ]]; then
    echo "1. Ensure domain $DOMAIN points to this server (A record)"
    echo "2. Open port 80 for Let's Encrypt validation"
    echo "3. Start services: docker-compose -f $COMPOSE_FILE up -d"
    echo "4. Wait for certificate generation (check logs: docker-compose logs certbot)"
    echo "5. Access your PACS at: https://$DOMAIN"
elif [[ "$SSL_MODE" == "none" ]]; then
    echo "1. Start services: docker-compose -f $COMPOSE_FILE up -d"
    echo "2. Access your PACS at: http://$DOMAIN"
else
    echo "1. Start services: docker-compose -f $COMPOSE_FILE up -d"
    echo "2. Access your PACS at: https://$DOMAIN"
    if [[ "$SSL_MODE" == "selfsigned" ]]; then
        echo "   (Accept security warning for self-signed certificate)"
    fi
fi
echo "3. Login with any of the created user accounts"
echo "4. Admin token management: https://$DOMAIN/auth/tokens/manage"
echo
echo "IMPORTANT NOTES:"
echo "- Keep your .env file secure (contains passwords and secrets)"
echo "- Database credentials are hardcoded in Orthanc configuration files"
echo "- Update services/orthanc/config/orthanc.json with your database password"
if [[ "$DB_MODE" == "internal" ]]; then
    echo "- PostgreSQL credentials: ${POSTGRES_USER}/${POSTGRES_PASSWORD}"
fi
echo "- Configure SSL certificates for production use"
echo "- Admin token management: https://$DOMAIN/auth/tokens/manage"
echo