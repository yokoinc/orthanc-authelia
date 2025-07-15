#!/bin/bash
# =============================================================================
# ORTHANC-AUTHELIA QUICK SETUP
# =============================================================================
# Generates secure secrets and creates working .env file
# =============================================================================

set -e

echo "🚀 ORTHANC-AUTHELIA Quick Setup"
echo "=========================="
echo

# Check if .env already exists
if [[ -f ".env" ]]; then
    echo "⚠️  .env file already exists!"
    read -p "Overwrite it? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Setup cancelled."
        exit 0
    fi
fi

# Copy example file
echo "📋 Creating .env from template..."
cp .env.example .env

# Generate secrets
echo "🔐 Generating secure secrets..."
SESSION_SECRET=$(openssl rand -base64 48)
STORAGE_KEY=$(openssl rand -base64 48)  
JWT_SECRET=$(openssl rand -base64 48)

# Function to escape special characters for sed
escape_sed() {
    echo "$1" | sed 's/[[\.*^$()+?{|]/\\&/g'
}

# Replace secrets in .env (using | as delimiter to avoid issues with / in base64)
sed -i "s|AUTHELIA_SESSION_SECRET=.*|AUTHELIA_SESSION_SECRET=$SESSION_SECRET|" .env
sed -i "s|AUTHELIA_STORAGE_ENCRYPTION_KEY=.*|AUTHELIA_STORAGE_ENCRYPTION_KEY=$STORAGE_KEY|" .env
sed -i "s|AUTHELIA_JWT_SECRET=.*|AUTHELIA_JWT_SECRET=$JWT_SECRET|" .env

# Ask for domain
echo
read -p "🌐 Enter your domain (default: pacs.example.com): " domain
domain=${domain:-pacs.example.com}

sed -i "s|pacs.example.com|$domain|g" .env

# Ask for port  
echo
read -p "🔌 Enter external port (default: 30080): " port
port=${port:-30080}

sed -i "s|NGINX_EXTERNAL_PORT=.*|NGINX_EXTERNAL_PORT=$port|" .env

# Read user credentials from .env
echo
echo "👥 Setting up Authelia users from .env configuration..."

# Function to safely read value from .env
get_env_value() {
    local key=$1
    grep "^${key}=" .env | cut -d'=' -f2- | sed 's/[[:space:]]*#.*//' | xargs
}

# Get user credentials from .env file
admin_email=$(get_env_value "AUTHELIA_ADMIN_USERNAME")
admin_password=$(get_env_value "AUTHELIA_ADMIN_PASSWORD")
doctor_email=$(get_env_value "AUTHELIA_DOCTOR_USERNAME")
doctor_password=$(get_env_value "AUTHELIA_DOCTOR_PASSWORD")
external_email=$(get_env_value "AUTHELIA_EXTERNAL_USERNAME")
external_password=$(get_env_value "AUTHELIA_EXTERNAL_PASSWORD")

# Generate user database
echo
echo "🔐 Generating Authelia user database..."

# Function to hash password using Docker
hash_password() {
    local password=$1
    docker run --rm authelia/authelia:latest authelia crypto hash generate argon2 --password "$password" 2>/dev/null | grep "Digest: " | cut -d' ' -f2
}

# Create Authelia config directory
mkdir -p services/authelia/config

# Copy configuration.yml from example and replace variables
echo "📋 Creating Authelia configuration..."
cp services/authelia/config/configuration.yml.example services/authelia/config/configuration.yml

# Replace environment variables in configuration.yml
echo "🔧 Configuring Authelia with domain: $domain"
export AUTHELIA_DOMAIN="$domain"
export AUTHELIA_URL="https://$domain/auth"
export AUTHELIA_DEFAULT_REDIRECT_URL="https://$domain/"
# Use envsubst to replace only specific variables, preserving escaped ones
envsubst '$AUTHELIA_DOMAIN $AUTHELIA_URL $AUTHELIA_DEFAULT_REDIRECT_URL' < services/authelia/config/configuration.yml.example > services/authelia/config/configuration.yml

# Configure Orthanc with environment variables
echo "🔧 Configuring Orthanc with environment variables..."
# Source the .env file to get all variables
set -a  # automatically export all variables
source .env
set +a  # stop auto-exporting
envsubst < services/orthanc/config/orthanc.json.example > services/orthanc/config/orthanc.json

# Create users_database.yml
cat > services/authelia/config/users_database.yml << EOF
---
users:
  $admin_email:
    displayname: "Administrateur PACS"
    password: "$(hash_password "$admin_password")"
    email: $admin_email
    groups:
      - admin
      
  $doctor_email:
    displayname: "Médecin"
    password: "$(hash_password "$doctor_password")"
    email: $doctor_email
    groups:
      - doctor
      
  $external_email:
    displayname: "Utilisateur Externe"
    password: "$(hash_password "$external_password")"
    email: $external_email
    groups:
      - external
EOF

echo
echo "✅ Setup complete!"
echo
echo "📝 Next steps:"
echo "1. IMPORTANT: Edit .env file to set user credentials (AUTHELIA_*_USERNAME and AUTHELIA_*_PASSWORD)"
echo "2. For SSL: uncomment SSL variables in .env and configure certificates"
echo "3. Run setup.sh again after modifying user credentials to regenerate user database"
echo "4. Start services: docker-compose up -d"
echo "5. Access your PACS at: http://$domain:$port"
echo
echo "⚠️  Default users in users_database.yml:"
echo "   - Admin: $admin_email"
echo "   - Doctor: $doctor_email"
echo "   - External: $external_email"
echo