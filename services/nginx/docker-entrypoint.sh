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

# Certificat auto-signe : genere s'il manque, REGENERE s'il approche de sa fin.
#
# La condition ne testait que l'absence du fichier. Or le certificat vit dans un
# volume nomme : genere une fois pour 365 jours, il n'etait jamais refait et
# expirait en silence. Constate le 2026-08-29 : celui en service arrivait a
# echeance le 10 octobre, sans que rien ne soit prevu pour le remplacer.
#
# Le tunnel Cloudflare ne le verifie pas (il ne pourrait pas, il est auto-signe),
# donc l'expiration ne coupe probablement pas l'acces public -- mais « probablement »
# n'est pas une base, et un acces direct depuis le reseau local, lui, affiche
# bien un certificat perime.
#
# 3650 jours : ce certificat ne sert qu'entre le tunnel et nginx, sur le reseau
# Docker. Sa duree de vie n'est pas une garantie de securite ici, et une echeance
# courte n'achete qu'une panne future.
mkdir -p /etc/nginx/ssl
apk add --no-cache openssl 2>/dev/null || true

BESOIN_CERT=0
if [ ! -f /etc/nginx/ssl/cert.pem ] || [ ! -f /etc/nginx/ssl/key.pem ]; then
    echo "Certificat absent."
    BESOIN_CERT=1
elif ! openssl x509 -in /etc/nginx/ssl/cert.pem -noout -checkend 2592000 >/dev/null 2>&1; then
    # -checkend 2592000 : expire dans moins de 30 jours (ou deja expire).
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
    # SHA-256 ($5$) au lieu de MD5-apr1 ($apr1$) : meilleure resistance au brute-force offline.
    # nginx auth_basic supporte $5$/$6$/$2y$ via crypt(3) sur Linux moderne.
    # Use apr1 (Apache MD5-based) format, NOT SHA-256 ($5$): nginx on Alpine
    # (musl crypt) cannot verify $5$ hashes -> all Basic auth requests would 401.
    # apr1 is implemented natively by nginx and works on every libc.
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