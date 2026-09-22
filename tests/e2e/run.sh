 E2E_URL picks the public address, port included; the default is what a user#!/bin/bash
 E2E_URL picks the public address, port included; the default is what a user# =============================================================================
 E2E_URL picks the public address, port included; the default is what a user# End-to-end test of a FRESH installation
 E2E_URL picks the public address, port included; the default is what a user# =============================================================================
 E2E_URL picks the public address, port included; the default is what a user# What a new user does, start to finish: bootstrap.sh, docker compose up, the
 E2E_URL picks the public address, port included; the default is what a user# setup wizard, sign-in, the admin panel, a DICOM upload shown in OHIF, the
 E2E_URL picks the public address, port included; the default is what a user# nightly backup script and apply.sh. Every problem found by hand on a fresh
 E2E_URL picks the public address, port included; the default is what a user# install so far (clock check, a false Cloudflare alarm, scripts hard-coding
 E2E_URL picks the public address, port included; the default is what a user# one installation, docker-compose v1) would have failed here first.
 E2E_URL picks the public address, port included; the default is what a user#
 E2E_URL picks the public address, port included; the default is what a user# Run it on a throwaway clone, never on a live installation: it generates .env
 E2E_URL picks the public address, port included; the default is what a user# and the configuration in place and starts the stack on ports 30080/30443.
 E2E_URL picks the public address, port included; the default is what a user#
 E2E_URL picks the public address, port included; the default is what a user#   git clone https://github.com/yokoinc/orthanc-authelia.git e2e && cd e2e
 E2E_URL picks the public address, port included; the default is what a user#   bash tests/e2e/run.sh
 E2E_URL picks the public address, port included; the default is what a user#
 E2E_URL picks the public address, port included; the default is what a user# CI builds the auth-service and nginx images of the commit under test first,
 E2E_URL picks the public address, port included; the default is what a user# under the tags the compose file pins, so the stack runs this commit's code.
 E2E_URL picks the public address, port included; the default is what a user# =============================================================================
 E2E_URL picks the public address, port included; the default is what a userset -euo pipefail
 E2E_URL picks the public address, port included; the default is what a usercd "$(dirname "$0")/../.."
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userPLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:v1.49.1-noble
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userLOG=$(mktemp)
 E2E_URL picks the public address, port included; the default is what a userexec > >(tee -a "$LOG") 2>&1
 E2E_URL picks the public address, port included; the default is what a userCURRENT_STEP="start"
 E2E_URL picks the public address, port included; the default is what a userstep() { CURRENT_STEP=$1; printf '\n==== %s ====\n' "$1"; }
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a user# On GitHub, a failure is written as an annotation: annotations are public,
 E2E_URL picks the public address, port included; the default is what a user# the raw job log is not. Last lines of output, plus the log of every container
 E2E_URL picks the public address, port included; the default is what a user# that is not healthy -- enough to diagnose without re-running anything.
 E2E_URL picks the public address, port included; the default is what a userannotate() {
 E2E_URL picks the public address, port included; the default is what a user    [ -n "${GITHUB_ACTIONS:-}" ] || return 0
 E2E_URL picks the public address, port included; the default is what a user    local body
 E2E_URL picks the public address, port included; the default is what a user    body=$(tail -n 40 "$LOG")
 E2E_URL picks the public address, port included; the default is what a user    for c in $(docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | awk '/^orthanc-/ && (/unhealthy/ || !/Up/) {print $1}'); do
 E2E_URL picks the public address, port included; the default is what a user        body+=$'\n'"--- $c (last lines) ---"$'\n'"$(docker logs --tail 15 "$c" 2>&1)"
 E2E_URL picks the public address, port included; the default is what a user    done
 E2E_URL picks the public address, port included; the default is what a user    body=${body//'%'/'%25'}
 E2E_URL picks the public address, port included; the default is what a user    body=${body//$'\r'/'%0D'}
 E2E_URL picks the public address, port included; the default is what a user    body=${body//$'\n'/'%0A'}
 E2E_URL picks the public address, port included; the default is what a user    echo "::error title=e2e failed during: $CURRENT_STEP::$body"
 E2E_URL picks the public address, port included; the default is what a user}
 E2E_URL picks the public address, port included; the default is what a usertrap 'status=$?; [ $status -ne 0 ] && annotate; exit $status' EXIT
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userif [ -e .env ]; then
 E2E_URL picks the public address, port included; the default is what a user    echo ".env already exists: run this on a fresh clone, not on an installation." >&2
 E2E_URL picks the public address, port included; the default is what a user    exit 1
 E2E_URL picks the public address, port included; the default is what a userfi
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a user# E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a user# gets by pressing Enter.
 E2E_URL picks the public address, port included; the default is what a userexport E2E_URL=${E2E_URL:-https://pacs.localhost:30443}
 E2E_URL picks the public address, port included; the default is what a userexport E2E_NEW_URL=${E2E_NEW_URL:-https://moved.localhost:30005}
 E2E_URL picks the public address, port included; the default is what a userexport E2E_EMAIL=admin-e2e@example.org
 E2E_URL picks the public address, port included; the default is what a userE2E_PASSWORD=$(openssl rand -hex 12)
 E2E_URL picks the public address, port included; the default is what a userexport E2E_PASSWORD
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a user# Playwright, pinned, with the host's network: *.localhost reaches the stack.
 E2E_URL picks the public address, port included; the default is what a userin_browser() {
 E2E_URL picks the public address, port included; the default is what a user    docker run --rm --network host -e GITHUB_ACTIONS -e E2E_URL -e E2E_NEW_URL -e E2E_EMAIL \
 E2E_URL picks the public address, port included; the default is what a user        -e E2E_PASSWORD -v "$PWD/tests/e2e:/e2e:ro" "$PLAYWRIGHT_IMAGE" \
 E2E_URL picks the public address, port included; the default is what a user        sh -c "pip install -q --disable-pip-version-check --root-user-action=ignore playwright==1.49.1 pydicom==3.0.1 && python $*"
 E2E_URL picks the public address, port included; the default is what a user}
 E2E_URL picks the public address, port included; the default is what a userstep "bootstrap.sh, public address $E2E_URL"
 E2E_URL picks the public address, port included; the default is what a userBOOTSTRAP_PUBLIC_URL=$E2E_URL ./bootstrap.sh < /dev/null
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userexport E2E_SSL=${E2E_SSL:-selfsigned}
 E2E_URL picks the public address, port included; the default is what a userHOST=$(printf '%s' "$E2E_URL" | sed -E 's#^https://([^:/]+).*#\1#')
 E2E_URL picks the public address, port included; the default is what a userif [ "$E2E_SSL" = custom ]; then
 E2E_URL picks the public address, port included; the default is what a user    step "own certificate (SSL_MODE=custom), issued by a throwaway CA for $HOST"
 E2E_URL picks the public address, port included; the default is what a user    openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/e2e-ca.key -out /tmp/e2e-ca.pem \
 E2E_URL picks the public address, port included; the default is what a user        -days 2 -subj "/CN=E2E Test CA" 2>/dev/null
 E2E_URL picks the public address, port included; the default is what a user    openssl req -newkey rsa:2048 -nodes -keyout certs/privkey.pem -out /tmp/e2e.csr \
 E2E_URL picks the public address, port included; the default is what a user        -subj "/CN=$HOST" 2>/dev/null
 E2E_URL picks the public address, port included; the default is what a user    printf 'subjectAltName=DNS:%s\n' "$HOST" > /tmp/e2e.ext
 E2E_URL picks the public address, port included; the default is what a user    openssl x509 -req -in /tmp/e2e.csr -CA /tmp/e2e-ca.pem -CAkey /tmp/e2e-ca.key -CAcreateserial \
 E2E_URL picks the public address, port included; the default is what a user        -days 2 -extfile /tmp/e2e.ext -out /tmp/e2e.crt 2>/dev/null
 E2E_URL picks the public address, port included; the default is what a user    cat /tmp/e2e.crt /tmp/e2e-ca.pem > certs/fullchain.pem
 E2E_URL picks the public address, port included; the default is what a user    sed -i 's/^SSL_MODE=.*/SSL_MODE=custom/' .env
 E2E_URL picks the public address, port included; the default is what a userfi
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "docker compose up"
 E2E_URL picks the public address, port included; the default is what a userdocker compose up -d
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "waiting for every container to be healthy"
 E2E_URL picks the public address, port included; the default is what a userfor _ in $(seq 90); do
 E2E_URL picks the public address, port included; the default is what a user    pending=$(docker compose ps --format '{{.Name}} {{.Health}}' | awk 'NF == 2 && $2 != "healthy"' | wc -l)
 E2E_URL picks the public address, port included; the default is what a user    [ "$pending" -eq 0 ] && break
 E2E_URL picks the public address, port included; the default is what a user    sleep 5
 E2E_URL picks the public address, port included; the default is what a userdone
 E2E_URL picks the public address, port included; the default is what a userdocker compose ps --format '  {{.Name}}  {{.Status}}'
 E2E_URL picks the public address, port included; the default is what a userif [ "$pending" -ne 0 ]; then
 E2E_URL picks the public address, port included; the default is what a user    echo "Containers still not healthy after 7.5 minutes." >&2
 E2E_URL picks the public address, port included; the default is what a user    exit 1
 E2E_URL picks the public address, port included; the default is what a userfi
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "certificate served ($E2E_SSL)"
 E2E_URL picks the public address, port included; the default is what a userPORT=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2)
 E2E_URL picks the public address, port included; the default is what a userissuer=$(echo | openssl s_client -connect "127.0.0.1:$PORT" -servername "$HOST" 2>/dev/null | openssl x509 -noout -issuer)
 E2E_URL picks the public address, port included; the default is what a userecho "  $issuer"
 E2E_URL picks the public address, port included; the default is what a userif [ "$E2E_SSL" = custom ]; then
 E2E_URL picks the public address, port included; the default is what a user    echo "$issuer" | grep -q "E2E Test CA"
 E2E_URL picks the public address, port included; the default is what a userelse
 E2E_URL picks the public address, port included; the default is what a user    echo "$issuer" | grep -q "Auto-Generated"
 E2E_URL picks the public address, port included; the default is what a userfi
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "browser run (wizard, sign-in, panel, DICOM, OHIF, OE2)"
 E2E_URL picks the public address, port included; the default is what a userin_browser /e2e/browser.py
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "nightly backup script"
 E2E_URL picks the public address, port included; the default is what a usersh scripts/backup-postgres.sh --check
 E2E_URL picks the public address, port included; the default is what a userdumps=$(mktemp -d)
 E2E_URL picks the public address, port included; the default is what a userBACKUP_DIR="$dumps" sh scripts/backup-postgres.sh
 E2E_URL picks the public address, port included; the default is what a userls -la "$dumps"
 E2E_URL picks the public address, port included; the default is what a userls "$dumps"/orthanc-*.dump >/dev/null
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "apply.sh"
 E2E_URL picks the public address, port included; the default is what a usersh scripts/apply.sh | tee /tmp/e2e-apply.log
 E2E_URL picks the public address, port included; the default is what a usergrep -q "Deployment verified" /tmp/e2e-apply.log
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "change the public address from the panel: $E2E_URL -> $E2E_NEW_URL"
 E2E_URL picks the public address, port included; the default is what a userin_browser /e2e/move.py change
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "apply.sh after the change (recreates nginx on the new port, restarts Authelia)"
 E2E_URL picks the public address, port included; the default is what a usersh scripts/apply.sh | tee /tmp/e2e-apply-2.log
 E2E_URL picks the public address, port included; the default is what a usergrep -q "Deployment verified" /tmp/e2e-apply-2.log
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "the PACS at its new address"
 E2E_URL picks the public address, port included; the default is what a userin_browser /e2e/move.py verify
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userif [ "$E2E_SSL" = custom ]; then
 E2E_URL picks the public address, port included; the default is what a user    step "a private key that does not match the certificate stops nginx, with the reason"
 E2E_URL picks the public address, port included; the default is what a user    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out certs/privkey.pem 2>/dev/null
 E2E_URL picks the public address, port included; the default is what a user    docker restart orthanc-nginx >/dev/null
 E2E_URL picks the public address, port included; the default is what a user    sleep 8
 E2E_URL picks the public address, port included; the default is what a user    docker logs --tail 20 orthanc-nginx 2>&1 | grep "is not the private key"
 E2E_URL picks the public address, port included; the default is what a userfi
 E2E_URL picks the public address, port included; the default is what a user
 E2E_URL picks the public address, port included; the default is what a userstep "fresh install, certificate and address change: every step passed"
