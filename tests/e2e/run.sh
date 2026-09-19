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
step "bootstrap.sh, public address $E2E_URL"
BOOTSTRAP_PUBLIC_URL=$E2E_URL ./bootstrap.sh < /dev/null

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

step "browser run (wizard, sign-in, panel, DICOM, OHIF, OE2)"
docker run --rm --network host -e GITHUB_ACTIONS -e E2E_URL -v "$PWD/tests/e2e:/e2e:ro" "$PLAYWRIGHT_IMAGE" \
    sh -c 'pip install -q --disable-pip-version-check --root-user-action=ignore playwright==1.49.1 pydicom==3.0.1 && python /e2e/browser.py'

step "nightly backup script"
sh scripts/backup-postgres.sh --check
dumps=$(mktemp -d)
BACKUP_DIR="$dumps" sh scripts/backup-postgres.sh
ls -la "$dumps"
ls "$dumps"/orthanc-*.dump >/dev/null

step "apply.sh"
sh scripts/apply.sh | tee /tmp/e2e-apply.log
grep -q "Deployment verified" /tmp/e2e-apply.log

step "fresh install: every step passed"
