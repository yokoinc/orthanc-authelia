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

# Self-signed certificate: generated if missing, REGENERATED when nearing its
# end.
#
# The condition only tested whether the file was missing. Yet the certificate
# lives in a named volume: generated once for 365 days, it was never redone and
# expired silently. Found on 2026-08-29: the one in service was due to expire
# on 10 October, with nothing planned to replace it.
#
# The Cloudflare tunnel does not verify it (it could not, it is self-signed),
# so expiry probably does not cut public access -- but "probably" is not a
# basis, and direct access from the local network does display an expired
# certificate.
#
# 3650 days: this certificate is only used between the tunnel and nginx, on the
# Docker network. Its lifetime is not a security guarantee here, and a short
# expiry only buys a future outage.
mkdir -p /etc/nginx/ssl
apk add --no-cache openssl 2>/dev/null || true

BESOIN_CERT=0
if [ ! -f /etc/nginx/ssl/cert.pem ] || [ ! -f /etc/nginx/ssl/key.pem ]; then
    echo "Certificat absent."
    BESOIN_CERT=1
elif ! openssl x509 -in /etc/nginx/ssl/cert.pem -noout -checkend 2592000 >/dev/null 2>&1; then
    # -checkend 2592000: expires in less than 30 days (or already expired).
    echo "Certificat expire ou arrivant a echeance sous 30 jours."
    BESOIN_CERT=1
fi

if [ "$BESOIN_CERT" = "1" ]; then
    echo "Generation d'un certificat auto-signe pour ${DOMAIN}..."
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /etc/nginx/ssl/key.pem \
        -out /etc/nginx/ssl/cert.pem \
        -days 3650 \
        -subj "/CN=${DOMAIN}/O=Auto-Generated/C=FR" 2>/dev/null
    echo "Certificat genere, valable jusqu'au $(openssl x509 -in /etc/nginx/ssl/cert.pem -noout -enddate | cut -d= -f2)."
fi

# Generate htpasswd for the programmatic upload endpoint (/api-upload/)
# If UPLOAD_USER and UPLOAD_PASSWORD are unset, the file is not created and
# nginx will return 500 on /api-upload/* (fail-closed).
if [ -n "$UPLOAD_USER" ] && [ -n "$UPLOAD_PASSWORD" ]; then
    echo "Generating /etc/nginx/htpasswd for UPLOAD_USER='$UPLOAD_USER'..."
    # Use apr1 (Apache MD5-based) format, NOT SHA-256 ($5$): nginx on Alpine
    # (musl crypt) cannot verify $5$ hashes -> all Basic auth requests would
    # 401. apr1 is implemented natively by nginx and works on every libc.
    # (SHA-256 was tried first, for its better resistance to offline brute
    # force.)
    HASH=$(printf "%s" "$UPLOAD_PASSWORD" | openssl passwd -apr1 -stdin)
    printf "%s:%s\n" "$UPLOAD_USER" "$HASH" > /etc/nginx/htpasswd
    # 644 (NOT 600 root:root): the nginx WORKER processes run as user 'nginx'
    # and need read access; a 600 root file gives "[crit] open() htpasswd
    # failed (13: Permission denied)" -> 401. The file contains an apr1 hash,
    # not a plaintext password, so world-readable inside the container is fine.
    chmod 644 /etc/nginx/htpasswd
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