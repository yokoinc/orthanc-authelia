#!/bin/sh

# NGINX Custom Entrypoint with Environment Variable Substitution
# This script processes configuration templates and substitutes environment variables

set -e

# Default values for environment variables
DOMAIN=${DOMAIN:-localhost}
SSL_MODE=${SSL_MODE:-selfsigned}

echo "Starting nginx configuration with environment variables..."
echo "DOMAIN: $DOMAIN"
echo "SSL_MODE: $SSL_MODE"

# Create SSL directory if it doesn't exist
mkdir -p /etc/nginx/ssl

# Generate self-signed certificates if they don't exist
if [ ! -f /etc/nginx/ssl/cert.pem ] || [ ! -f /etc/nginx/ssl/key.pem ]; then
    echo "SSL certificates not found. Generating self-signed certificates..."
    apk add --no-cache openssl 2>/dev/null || true
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /etc/nginx/ssl/key.pem \
        -out /etc/nginx/ssl/cert.pem \
        -days 365 \
        -subj "/CN=${DOMAIN}/O=Auto-Generated/C=FR" 2>/dev/null
    echo "Self-signed certificates generated."
fi

# Generate htpasswd for the programmatic upload endpoint (/api-upload/)
# If UPLOAD_USER and UPLOAD_PASSWORD are unset, the file is not created and
# nginx will return 500 on /api-upload/* (fail-closed).
if [ -n "$UPLOAD_USER" ] && [ -n "$UPLOAD_PASSWORD" ]; then
    echo "Generating /etc/nginx/htpasswd for UPLOAD_USER='$UPLOAD_USER'..."
    # SHA-256 ($5$) au lieu de MD5-apr1 ($apr1$) : meilleure resistance au brute-force offline.
    # nginx auth_basic supporte $5$/$6$/$2y$ via crypt(3) sur Linux moderne.
    HASH=$(printf "%s" "$UPLOAD_PASSWORD" | openssl passwd -5 -stdin)
    printf "%s:%s\n" "$UPLOAD_USER" "$HASH" > /etc/nginx/htpasswd
    chmod 600 /etc/nginx/htpasswd
else
    echo "UPLOAD_USER/UPLOAD_PASSWORD not set: /api-upload/ endpoint disabled (htpasswd absent)."
    rm -f /etc/nginx/htpasswd
fi

# Process main nginx configuration
echo "Processing nginx.conf template..."
envsubst '$DOMAIN' < /etc/nginx/templates/nginx.conf.template > /etc/nginx/nginx.conf

# Process configuration files in conf.d
echo "Processing conf.d templates..."
mkdir -p /etc/nginx/conf.d

for template in /etc/nginx/conf.d.templates/*.conf; do
    if [ -f "$template" ]; then
        filename=$(basename "$template")
        echo "Processing $filename..."
        envsubst '$DOMAIN' < "$template" > "/etc/nginx/conf.d/$filename"
    fi
done

# Test nginx configuration
echo "Testing nginx configuration..."
nginx -t

echo "Configuration processed successfully. Starting nginx..."

# Execute the original command
exec "$@"