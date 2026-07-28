#!/bin/sh

# NGINX Custom Entrypoint with Environment Variable Substitution
# This script processes configuration templates and substitutes environment variables

set -e

# PUBLIC_URL est la seule variable a renseigner dans .env : l'URL complete
# par laquelle les navigateurs joignent la stack, port inclus s'il n'est pas
# standard. DOMAIN (le nom d'hote seul) en est derive.
#
# Les deux servent a des choses differentes dans les templates :
#   DOMAIN     -> en-tetes Host, X-Forwarded-Host, CN du certificat
#                 (un nom d'hote ne porte jamais de port)
#   PUBLIC_URL -> URLs absolues : redirections, origine CORS, X-Original-URL
#                 (elles doivent inclure le port, sinon le navigateur part
#                  sur 443 alors que la stack ecoute ailleurs)
PUBLIC_URL=${PUBLIC_URL:-https://localhost}
DOMAIN=$(echo "$PUBLIC_URL" | sed -E 's#^https?://##; s#:[0-9]+$##; s#/.*$##')
SSL_MODE=${SSL_MODE:-selfsigned}

# HSTS et certificat auto-signe ne vont pas ensemble.
#
# Une fois la directive enregistree, les navigateurs refusent d'afficher
# l'exception de certificat : plus de bouton "Continuer quand meme", le site
# devient inaccessible et le rechargement force n'y change rien. includeSubDomains
# etend le blocage a tout *.localhost. L'utilisateur voit une page vide sans
# comprendre pourquoi, et purger l'enregistrement demande de passer par
# chrome://net-internals/#hsts.
#
# max-age=0 ordonne au navigateur d'oublier la directive : les postes deja
# pieges se debloquent d'eux-memes au premier chargement.
if [ "$SSL_MODE" = "selfsigned" ]; then
    HSTS="max-age=0"
else
    HSTS="max-age=63072000; includeSubDomains; preload"
fi

export PUBLIC_URL DOMAIN HSTS

echo "Starting nginx configuration with environment variables..."
echo "PUBLIC_URL: $PUBLIC_URL"
echo "DOMAIN (derive): $DOMAIN"
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
envsubst '$DOMAIN $PUBLIC_URL $HSTS' < /etc/nginx/templates/nginx.conf.template > /etc/nginx/nginx.conf

# Process configuration files in conf.d
echo "Processing conf.d templates..."
mkdir -p /etc/nginx/conf.d

for template in /etc/nginx/conf.d.templates/*.conf; do
    if [ -f "$template" ]; then
        filename=$(basename "$template")
        echo "Processing $filename..."
        envsubst '$DOMAIN $PUBLIC_URL $HSTS' < "$template" > "/etc/nginx/conf.d/$filename"
    fi
done

# Test nginx configuration
echo "Testing nginx configuration..."
nginx -t

echo "Configuration processed successfully. Starting nginx..."

# Execute the original command
exec "$@"