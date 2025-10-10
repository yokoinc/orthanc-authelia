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