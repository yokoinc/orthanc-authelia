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

# =============================================================================
# TLS certificate
# =============================================================================
# SSL_MODE=selfsigned (default): a certificate generated here and renewed before
#   it expires. Right for a local network, and behind a Cloudflare tunnel or any
#   proxy that does not verify it.
# SSL_MODE=custom: your own certificate, from the installation's certs/
#   directory (mounted read-only on /etc/nginx/custom-certs): fullchain.pem and
#   privkey.pem, or cert.pem and key.pem. Checked at every start and used as it
#   is -- never replaced. Renewing it: replace the files, then
#   sh scripts/apply.sh.
#
# SSL_MODE used to be read nowhere. Every installation got the self-signed
# certificate, and a certificate placed by hand in the volume was overwritten
# by a self-signed one once it came within 30 days of its expiry, silently.
SSL_DIR=/etc/nginx/ssl
CUSTOM_DIR=/etc/nginx/custom-certs
mkdir -p "$SSL_DIR"

case "$SSL_MODE" in
custom)
    if [ -f "$CUSTOM_DIR/fullchain.pem" ] && [ -f "$CUSTOM_DIR/privkey.pem" ]; then
        CERT=$CUSTOM_DIR/fullchain.pem
        KEY=$CUSTOM_DIR/privkey.pem
    elif [ -f "$CUSTOM_DIR/cert.pem" ] && [ -f "$CUSTOM_DIR/key.pem" ]; then
        CERT=$CUSTOM_DIR/cert.pem
        KEY=$CUSTOM_DIR/key.pem
    else
        echo "ERROR: SSL_MODE=custom, but the installation's certs/ directory holds no certificate." >&2
        echo "       Expected fullchain.pem + privkey.pem (or cert.pem + key.pem)." >&2
        exit 1
    fi
    if ! openssl x509 -in "$CERT" -noout 2>/dev/null; then
        echo "ERROR: certs/$(basename "$CERT") is not a readable PEM certificate." >&2
        exit 1
    fi
    CERT_PUB=$(openssl x509 -in "$CERT" -noout -pubkey 2>/dev/null | openssl sha256)
    KEY_PUB=$(openssl pkey -in "$KEY" -pubout 2>/dev/null | openssl sha256)
    if ! openssl pkey -in "$KEY" -noout 2>/dev/null || [ "$CERT_PUB" != "$KEY_PUB" ]; then
        echo "ERROR: certs/$(basename "$KEY") is not the private key of certs/$(basename "$CERT")." >&2
        exit 1
    fi
    if ! openssl x509 -in "$CERT" -noout -checkend 0 >/dev/null 2>&1; then
        echo "ERROR: the certificate expired on $(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)." >&2
        exit 1
    fi
    if ! openssl x509 -in "$CERT" -noout -checkend 2592000 >/dev/null 2>&1; then
        echo "WARNING: the certificate expires in less than 30 days: replace the files in certs/, then run sh scripts/apply.sh."
    fi
    if ! openssl x509 -in "$CERT" -noout -checkhost "$DOMAIN" 2>/dev/null | grep -q "does match"; then
        echo "WARNING: the certificate does not name ${DOMAIN}: browsers will show a warning."
    fi
    cp "$CERT" "$SSL_DIR/cert.pem"
    cp "$KEY" "$SSL_DIR/key.pem"
    chmod 600 "$SSL_DIR/key.pem"
    echo "Custom certificate: $(openssl x509 -in "$CERT" -noout -subject | sed 's/^subject=//'), valid until $(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)."
    ;;
*)
    # Any value other than custom serves the self-signed certificate, as every
    # value did before SSL_MODE was read: an installation upgraded with
    # SSL_MODE=none or disabled (both once suggested) must not lose its nginx.
    if [ "$SSL_MODE" != selfsigned ]; then
        echo "WARNING: SSL_MODE=${SSL_MODE} is not a mode (selfsigned or custom): serving the self-signed certificate."
    fi
    # Regenerated when missing, when within 30 days of expiry, or when the file
    # is not one generated here (coming back from SSL_MODE=custom).
    #
    # The volume keeps it across restarts: generated once for 365 days, it was
    # never redone and expired silently (found on 2026-08-29). 3650 days: it only
    # protects the hop to nginx, its lifetime is not a security guarantee, and
    # a short one only buys a future outage.
    NEEDED=""
    if [ ! -f "$SSL_DIR/cert.pem" ] || [ ! -f "$SSL_DIR/key.pem" ]; then
        NEEDED="no certificate yet"
    elif ! openssl x509 -in "$SSL_DIR/cert.pem" -noout -checkend 2592000 >/dev/null 2>&1; then
        NEEDED="expires within 30 days"
    elif ! openssl x509 -in "$SSL_DIR/cert.pem" -noout -subject 2>/dev/null | grep -q "Auto-Generated"; then
        NEEDED="the current one was not generated here"
    fi
    if [ -n "$NEEDED" ]; then
        echo "Generating a self-signed certificate for ${DOMAIN} (${NEEDED})..."
        openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" -days 3650 \
            -subj "/CN=${DOMAIN}/O=Auto-Generated" \
            -addext "subjectAltName=DNS:${DOMAIN}" 2>/dev/null
    fi
    echo "Self-signed certificate, valid until $(openssl x509 -in "$SSL_DIR/cert.pem" -noout -enddate | cut -d= -f2)."
    ;;
esac

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