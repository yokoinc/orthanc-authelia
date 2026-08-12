#!/bin/sh

# NGINX Custom Entrypoint with Environment Variable Substitution
# This script processes configuration templates and substitutes environment variables

set -e

# PUBLIC_URL is the only variable to fill in inside .env: the full URL
# through which browsers reach the stack, port included when it is not
# standard. DOMAIN (the bare host name) is derived from it.
#
# The two serve different purposes in the templates:
#   DOMAIN     -> Host and X-Forwarded-Host headers, certificate CN
#                 (a host name never carries a port)
#   PUBLIC_URL -> URLs absolues : redirections, origine CORS, X-Original-URL
#                 (they must include the port, otherwise the browser
#                  assumes 443 while the stack listens elsewhere)
PUBLIC_URL=${PUBLIC_URL:-https://localhost}
DOMAIN=$(echo "$PUBLIC_URL" | sed -E 's#^https?://##; s#:[0-9]+$##; s#/.*$##')
SSL_MODE=${SSL_MODE:-selfsigned}

# HSTS and a self-signed certificate do not go together.
#
# Once the directive is recorded, browsers refuse to offer the certificate
# exception: no more "Proceed anyway" button, the site becomes unreachable
# and a forced reload changes nothing. includeSubDomains extends the block to
# every *.localhost. The user sees a blank page with no
# understand why, and clearing the record means going through
# chrome://net-internals/#hsts.
#
# max-age=0 tells the browser to forget the directive: machines already
# trapped unblock themselves on the first load.
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

# Self-signed certificate: generate it when missing, and reissue it when the
# domain
# a change.
#
# The second case is not theoretical. The certificate lives in a named
# volume, so it survives any container recreation; changing PUBLIC_URL from
# the panel used to leave a certificate bearing the previous domain, valid
# for a year. The browser then no longer complains about a self-signature --
# which one can accept -- but about a certificate issued for another site,
# which looks like an interception and is far harder to diagnose.
apk add --no-cache openssl 2>/dev/null || true

BESOIN_CERT=0
if [ ! -f /etc/nginx/ssl/cert.pem ] || [ ! -f /etc/nginx/ssl/key.pem ]; then
    echo "SSL certificates not found."
    BESOIN_CERT=1
elif [ "$SSL_MODE" = "selfsigned" ]; then
    # Compare ONLY in self-signed mode: a certificate supplied by
    # the operator (Let's Encrypt, internal CA) can legitimately carry
    # a different name -- wildcard, multiple SANs -- and must never be
    # overwritten.
    CURRENT_CN=$(openssl x509 -in /etc/nginx/ssl/cert.pem -noout -subject 2>/dev/null \
                | sed -n 's/.*CN *= *\([^,/]*\).*/\1/p' | tr -d ' ')
    if [ -n "$CURRENT_CN" ] && [ "$CURRENT_CN" != "$DOMAIN" ]; then
        echo "Certificate is for '${CURRENT_CN}' but domain is now '${DOMAIN}'."
        BESOIN_CERT=1
    fi
fi

if [ "$BESOIN_CERT" = "1" ]; then
    echo "Generating self-signed certificate for ${DOMAIN}..."
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /etc/nginx/ssl/key.pem \
        -out /etc/nginx/ssl/cert.pem \
        -days 365 \
        -subj "/CN=${DOMAIN}/O=Auto-Generated/C=FR" 2>/dev/null
    echo "Self-signed certificate generated for ${DOMAIN}."
fi

# Generate htpasswd for the programmatic upload endpoint (/api-upload/)
# If UPLOAD_USER and UPLOAD_PASSWORD are unset, the file is not created and
# nginx will return 500 on /api-upload/* (fail-closed).
if [ -n "$UPLOAD_USER" ] && [ -n "$UPLOAD_PASSWORD" ]; then
    echo "Generating /etc/nginx/htpasswd for UPLOAD_USER='$UPLOAD_USER'..."
    # SHA-256 ($5$) would resist offline brute force better than MD5-apr1
    # ($apr1$), and nginx auth_basic supports $5$/$6$/$2y$ through crypt(3)
    # on modern Linux -- but not on Alpine, see below.
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