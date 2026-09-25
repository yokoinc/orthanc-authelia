#!/bin/bash
# =============================================================================
# End-to-end test of a FRESH installation
# =============================================================================
# What a new user does, start to finish: bootstrap.sh, docker compose up, the
# setup wizard, sign-in, the admin panel, a DICOM upload shown in OHIF, the
# nightly backup script and apply.sh. Every problem found by hand on a fresh
# install so far (clock check, a false Cloudflare alarm, scripts hard-coding
# one installation, docker-compose v1) would have failed here first.
#
# Run it on a throwaway clone, never on a live installation: it generates .env
# and the configuration in place and starts the stack on ports 30080/30443.
#
#   git clone https://github.com/yokoinc/orthanc-authelia.git e2e && cd e2e
#   bash tests/e2e/run.sh
#
# CI builds the auth-service and nginx images of the commit under test first,
# under the tags the compose file pins, so the stack runs this commit's code.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:v1.49.1-noble

LOG=$(mktemp)
exec > >(tee -a "$LOG") 2>&1
CURRENT_STEP="start"
step() { CURRENT_STEP=$1; printf '\n==== %s ====\n' "$1"; }

# On GitHub, a failure is written as an annotation: annotations are public,
# the raw job log is not. Last lines of output, plus the log of every container
# that is not healthy -- enough to diagnose without re-running anything.
annotate() {
    [ -n "${GITHUB_ACTIONS:-}" ] || return 0
    local body
    body=$(tail -n 40 "$LOG")
    for c in $(docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | awk '/^orthanc-/ && (/unhealthy/ || !/Up/) {print $1}'); do
        body+=$'\n'"--- $c (last lines) ---"$'\n'"$(docker logs --tail 15 "$c" 2>&1)"
    done
    body=${body//'%'/'%25'}
    body=${body//$'\r'/'%0D'}
    body=${body//$'\n'/'%0A'}
    echo "::error title=e2e failed during: $CURRENT_STEP::$body"
}
trap 'status=$?; [ $status -ne 0 ] && annotate; exit $status' EXIT

if [ -e .env ]; then
    echo ".env already exists: run this on a fresh clone, not on an installation." >&2
    exit 1
fi

# E2E_URL picks the public address, port included; the default is what a user
# gets by pressing Enter.
export E2E_URL=${E2E_URL:-https://pacs.localhost:30443}
export E2E_NEW_URL=${E2E_NEW_URL:-https://moved.localhost:30005}
export E2E_EMAIL=admin-e2e@example.org
E2E_PASSWORD=$(openssl rand -hex 12)
export E2E_PASSWORD

# Playwright, pinned, with the host's network: *.localhost reaches the stack.
in_browser() {
    docker run --rm --network host -e GITHUB_ACTIONS -e E2E_URL -e E2E_NEW_URL -e E2E_EMAIL \
        -e E2E_PASSWORD -v "$PWD/tests/e2e:/e2e:ro" "$PLAYWRIGHT_IMAGE" \
        sh -c "pip install -q --disable-pip-version-check --root-user-action=ignore playwright==1.49.1 pydicom==3.0.1 && python $*"
}
step "bootstrap.sh, public address $E2E_URL"
BOOTSTRAP_PUBLIC_URL=$E2E_URL BOOTSTRAP_NO_START=1 ./bootstrap.sh < /dev/null

export E2E_SSL=${E2E_SSL:-selfsigned}
HOST=$(printf '%s' "$E2E_URL" | sed -E 's#^https://([^:/]+).*#\1#')
if [ "$E2E_SSL" = custom ]; then
    step "own certificate (SSL_MODE=custom), issued by a throwaway CA for $HOST"
    openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/e2e-ca.key -out /tmp/e2e-ca.pem \
        -days 2 -subj "/CN=E2E Test CA" 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -keyout certs/privkey.pem -out /tmp/e2e.csr \
        -subj "/CN=$HOST" 2>/dev/null
    printf 'subjectAltName=DNS:%s\n' "$HOST" > /tmp/e2e.ext
    openssl x509 -req -in /tmp/e2e.csr -CA /tmp/e2e-ca.pem -CAkey /tmp/e2e-ca.key -CAcreateserial \
        -days 2 -extfile /tmp/e2e.ext -out /tmp/e2e.crt 2>/dev/null
    cat /tmp/e2e.crt /tmp/e2e-ca.pem > certs/fullchain.pem
    sed -i 's/^SSL_MODE=.*/SSL_MODE=custom/' .env
fi

step "docker compose up"
docker compose up -d

step "waiting for every container to be healthy"
for _ in $(seq 90); do
    pending=$(docker compose ps --format '{{.Name}} {{.Health}}' | awk 'NF == 2 && $2 != "healthy"' | wc -l)
    [ "$pending" -eq 0 ] && break
    sleep 5
done
docker compose ps --format '  {{.Name}}  {{.Status}}'
if [ "$pending" -ne 0 ]; then
    echo "Containers still not healthy after 7.5 minutes." >&2
    exit 1
fi

step "certificate served ($E2E_SSL)"
PORT=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2)
issuer=$(echo | openssl s_client -connect "127.0.0.1:$PORT" -servername "$HOST" 2>/dev/null | openssl x509 -noout -issuer)
echo "  $issuer"
if [ "$E2E_SSL" = custom ]; then
    echo "$issuer" | grep -q "E2E Test CA"
else
    echo "$issuer" | grep -q "Auto-Generated"
fi

step "browser run (wizard, sign-in, panel, DICOM, OHIF, OE2)"
in_browser /e2e/browser.py

step "nightly backup script"
sh scripts/backup-postgres.sh --check
dumps=$(mktemp -d)
BACKUP_DIR="$dumps" sh scripts/backup-postgres.sh
ls -la "$dumps"
ls "$dumps"/orthanc-*.dump >/dev/null

step "apply.sh"
sh scripts/apply.sh | tee /tmp/e2e-apply.log
grep -q "Deployment verified" /tmp/e2e-apply.log

step "change the public address from the panel: $E2E_URL -> $E2E_NEW_URL"
in_browser /e2e/move.py change

step "apply.sh after the change (recreates nginx on the new port, restarts Authelia)"
sh scripts/apply.sh | tee /tmp/e2e-apply-2.log
grep -q "Deployment verified" /tmp/e2e-apply-2.log

step "the PACS at its new address"
in_browser /e2e/move.py verify

if [ "$E2E_SSL" = custom ]; then
    step "a private key that does not match the certificate stops nginx, with the reason"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out certs/privkey.pem 2>/dev/null
    docker restart orthanc-nginx >/dev/null
    sleep 8
    docker logs --tail 20 orthanc-nginx 2>&1 | grep "is not the private key"
fi

step "fresh install, certificate and address change: every step passed"
