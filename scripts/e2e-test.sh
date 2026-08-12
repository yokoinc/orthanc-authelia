#!/usr/bin/env bash
# =============================================================================
# ORTHANC-AUTHELIA — End-to-end test
# =============================================================================
# Replays a full installation from scratch and checks the stack answers:
# bootstrap, start-up, wizard, administrator creation, login, then access to
# Orthanc, to DICOMweb and to the panel.
#
# The test stack is ISOLATED: separate Docker project, separate ports,
# separate volumes and network, and a copy of the repository in a temporary
# directory. The development installation is never touched, so the test can
# be rerun as often as wanted.
#
# Usage:
#   ./scripts/e2e-test.sh              # full test, then cleanup
#   ./scripts/e2e-test.sh --keep       # leaves the stack up for inspection
#
# Exit code 0 if everything passes, 1 otherwise.
# =============================================================================

set -uo pipefail

PORT_HTTP=31080
PORT_HTTPS=31443
PROJECT=orthanc-e2e
URL="https://pacs.localhost:${PORT_HTTPS}"
ADMIN_USER=e2e.admin
ADMIN_PASS=mot-de-passe-e2e-123456
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORKDIR=$(mktemp -d /tmp/orthanc-e2e.XXXXXX)

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
FAILURES=0
step()   { printf "\n${CYAN}▶ %s${RESET}\n" "$*"; }
ok()     { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
fail()   { printf "  ${RED}✗${RESET} %s\n" "$*"; FAILURES=$((FAILURES + 1)); }
info()   { printf "  ${YELLOW}·${RESET} %s\n" "$*"; }

compose() { docker compose -p "$PROJECT" "$@"; }

cleanup() {
    local code=$?
    if [[ $KEEP -eq 1 ]]; then
        printf "\n${YELLOW}Stack left up${RESET}: %s\n" "$URL"
        printf "To remove it:\n  cd %s && docker compose -p %s down -v && rm -rf %s\n" \
            "$WORKDIR" "$PROJECT" "$WORKDIR"
        return $code
    fi
    step "Cleanup"
    (cd "$WORKDIR" && compose down -v >/dev/null 2>&1) && ok "stack removed"
    # Containers write as root into the mounted directory (Authelia
    # database, generated configuration, locks): an rm run by the user trips
    # over them. We go through a container, which has the rights.
    if ! rm -rf "$WORKDIR" 2>/dev/null; then
        docker run --rm -v /tmp:/tmp-host alpine \
            rm -rf "/tmp-host/$(basename "$WORKDIR")" >/dev/null 2>&1
    fi
    if [[ -d "$WORKDIR" ]]; then
        fail "temporary directory not removed: $WORKDIR"
    else
        ok "temporary directory removed"
    fi
    return $code
}
trap cleanup EXIT

# --- Repository copy -------------------------------------------------------
# git archive rather than cp: only VERSIONED files are copied, so the test
# starts from exactly what someone cloning gets. A file left out by
# .gitignore yet required will show up here, and nowhere else.
step "Repository copy (versioned files only)"
if ! git -C "$REPO" archive HEAD | tar -x -C "$WORKDIR"; then
    fail "cannot extract the repository"
    exit 1
fi
ok "$(find "$WORKDIR" -type f | wc -l) files extracted into $WORKDIR"

cd "$WORKDIR"

# --- Bootstrap -------------------------------------------------------------
step "bootstrap.sh"
if ./bootstrap.sh >/tmp/e2e-bootstrap.log 2>&1; then
    ok "configuration generated"
else
    fail "bootstrap failed (see /tmp/e2e-bootstrap.log)"
    tail -15 /tmp/e2e-bootstrap.log
    exit 1
fi

# --- Isolation -------------------------------------------------------------
# bootstrap pins the public URL to port 30443, taken by the development
# installation. We move it, along with everything bearing a fixed name:
# containers, volumes and network would otherwise collide.
step "Isolating the test stack"
sed -i "s|pacs.localhost:30443|pacs.localhost:${PORT_HTTPS}|g" \
    .env services/authelia/config/configuration.yml
sed -i \
    -e '/^    container_name: /d' \
    -e "s|\"30080:80\"|\"${PORT_HTTP}:80\"|" \
    -e "s|\"30443:443\"|\"${PORT_HTTPS}:443\"|" \
    -e 's|name: orthanc_nginx_ssl|name: e2e_nginx_ssl|' \
    -e 's|name: orthanc_postgres_data|name: e2e_postgres_data|' \
    -e 's|name: orthanc-network|name: e2e-network|' \
    docker-compose.yml
if compose config >/dev/null 2>&1; then
    ok "ports ${PORT_HTTP}/${PORT_HTTPS}, project ${PROJECT}"
else
    fail "compose invalid after isolation"
    compose config 2>&1 | tail -5
    exit 1
fi

# --- Start-up --------------------------------------------------------------
step "Starting the stack"
if compose up -d >/tmp/e2e-up.log 2>&1; then
    ok "containers started"
else
    fail "cannot start (see /tmp/e2e-up.log)"
    tail -15 /tmp/e2e-up.log
    exit 1
fi

info "waiting for the login page (120 s max)"
ready=0
for _ in $(seq 1 60); do
    if [[ "$(curl -ks -o /dev/null -m 5 -w '%{http_code}' "${URL}/auth/")" == "200" ]]; then
        ready=1; break
    fi
    sleep 2
done
if [[ $ready -eq 1 ]]; then
    ok "stack reachable at ${URL}"
else
    fail "the stack does not answer after 120 s"
    compose ps
    exit 1
fi

# --- Wizard ----------------------------------------------------------------
step "First-run wizard"
COOKIES=$(mktemp)
CSRF=token-e2e

code=$(curl -ks -o /tmp/e2e-setup.json -m 20 -w '%{http_code}' \
    -X POST "${URL}/console/api/setup/create-admin" \
    -H 'Content-Type: application/json' \
    -H "X-CSRF-Token: ${CSRF}" -b "orthanc_admin_csrf=${CSRF}" \
    -d "{\"username\":\"${ADMIN_USER}\",\"displayname\":\"Admin E2E\",\"email\":\"e2e@exemple.fr\",\"password\":\"${ADMIN_PASS}\",\"groups\":[\"admin\"]}")
[[ "$code" == "200" ]] && ok "administrator created" || { fail "admin creation: HTTP $code $(cat /tmp/e2e-setup.json)"; }

code=$(curl -ks -o /tmp/e2e-final.json -m 20 -w '%{http_code}' \
    -X POST "${URL}/console/api/setup/finalize" \
    -H "X-CSRF-Token: ${CSRF}" -b "orthanc_admin_csrf=${CSRF}")
if [[ "$code" == "200" ]]; then
    ok "installation finalised"
    # The bootstrap account must be gone: that is the whole point of the
    # cleanup added at finalisation.
    if grep -q 'bootstrap@localhost' services/authelia/config/users_database.yml 2>/dev/null; then
        fail "the bootstrap account survives finalisation"
    else
        ok "bootstrap account removed"
    fi
else
    fail "finalisation: HTTP $code $(cat /tmp/e2e-final.json)"
fi

# --- Login -----------------------------------------------------------------
step "Login"
curl -ks -o /dev/null -m 20 -c "$COOKIES" "${URL}/auth/"
response=$(curl -ks -m 20 -b "$COOKIES" -c "$COOKIES" \
    -X POST "${URL}/api/firstfactor" \
    -H 'Content-Type: application/json' \
    -H "X-Forwarded-Proto: https" -H "X-Forwarded-Host: pacs.localhost:${PORT_HTTPS}" \
    -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASS}\",\"keepMeLoggedIn\":true}")
if grep -q '"status":"OK"' <<<"$response"; then
    ok "session opened"
else
    fail "login refused: ${response:0:120}"
fi

# --- Authenticated access --------------------------------------------------
step "Authenticated access"

# Wait for Orthanc to answer before checking anything. The login page, the
# only wait condition until now, proves nothing beyond nginx and Authelia
# being up: on a fresh installation Orthanc still has to create its
# PostgreSQL schema and load its plugins, which takes noticeably longer.
# Without this wait the test returned 502s and reported a regression that did
# not exist.
info "waiting for Orthanc (90 s max)"
orthanc_ready=0
for _ in $(seq 1 45); do
    if [[ "$(curl -ks -o /dev/null -m 5 -b "$COOKIES" -w '%{http_code}' "${URL}/system")" == "200" ]]; then
        orthanc_ready=1; break
    fi
    sleep 2
done
if [[ $orthanc_ready -eq 1 ]]; then
    ok "Orthanc ready"
else
    fail "Orthanc does not answer after 90 s"
    (cd "$WORKDIR" && compose logs --tail 15 orthanc 2>&1 | tail -15)
fi
check() {
    local path=$1 expected=$2
    local code
    code=$(curl -ks -o /dev/null -m 20 -b "$COOKIES" -w '%{http_code}' "${URL}${path}")
    if [[ "$code" == "$expected" ]]; then
        ok "$(printf '%-24s %s' "$path" "$code")"
    else
        fail "$(printf '%-24s %s (expected %s)' "$path" "$code" "$expected")"
    fi
}
check /ui/app/            200
check /studies            200
check /system             200
check /dicom-web/studies  200
check /console/           200
check /ohif/app-config.js 200

# The profile returned to Orthanc decides the rights: an unrecognised group
# silently falls back to a read-only profile.
# We check for the presence of the rights that matter, not their count: a
# frozen count breaks as soon as a permission is added, while saying nothing
# useful. admin-permissions is the most telling -- it governs DICOM device
# management, and its absence went unnoticed for a long time because 'all'
# does not cover it.
profile=$(curl -ks -m 20 -b "$COOKIES" "${URL}/ui/api/configuration" \
    | python3 -c '
import json, sys
p = json.load(sys.stdin)["Profile"]
expected = {"all", "admin-permissions", "settings", "delete", "upload", "view"}
missing = expected - set(p["permissions"])
print(p["name"], "|", ",".join(sorted(missing)) if missing else "complete")
' 2>/dev/null)
if [[ "$profile" == "Administrator | complete" ]]; then
    ok "Orthanc profile: Administrator, essential rights present"
else
    fail "Orthanc profile: ${profile:-unreadable}"
fi

# --- Panel features --------------------------------------------------------
# These routes existed server-side without any interface for a long time:
# nothing exercised them, so nothing would have reported them breaking.
step "Administration panel features"

# whoami sets the CSRF cookie, as the SPA does on load. Writes are refused
# without it.
curl -ks -o /dev/null -m 20 -b "$COOKIES" -c "$COOKIES" "${URL}/console/api/admin/whoami"
TOKEN=$(awk '/orthanc_admin_csrf/ {print $NF}' "$COOKIES" | tail -1)
if [[ -n "$TOKEN" ]]; then
    ok "CSRF token obtained"
else
    fail "no CSRF token set by whoami"
fi

check_panel() {
    local method=$1 path=$2 body=$3 label=$4 expected=${5:-200}
    local code
    if [[ -n "$body" ]]; then
        code=$(curl -ks -o /tmp/e2e-panel.json -m 20 -b "$COOKIES" -w '%{http_code}' \
            -X "$method" "${URL}${path}" \
            -H 'Content-Type: application/json' -H "X-CSRF-Token: ${TOKEN}" -d "$body")
    else
        code=$(curl -ks -o /tmp/e2e-panel.json -m 20 -b "$COOKIES" -w '%{http_code}' \
            -X "$method" "${URL}${path}" -H "X-CSRF-Token: ${TOKEN}")
    fi
    if [[ "$code" == "$expected" ]]; then
        ok "$(printf '%-38s %s' "$label" "$code")"
    else
        fail "$(printf '%-38s %s (expected %s): %s' "$label" "$code" "$expected" "$(head -c 80 /tmp/e2e-panel.json)")"
    fi
}

check_panel GET /console/api/admin/backups '' 'backup list'
check_panel GET /console/api/admin/network '' 'public address (read)'
check_panel GET /console/api/admin/audit '' 'activity log'
check_panel GET /console/api/admin/modalities '' 'DICOM devices'

check_panel PATCH "/console/api/admin/users/${ADMIN_USER}" \
    '{"displayname":"Admin E2E renomme"}' 'account update'

# Safety net: the wizard's administrator is the only account. Removing
# oneself from the admin group, or disabling oneself, would leave the stack
# with nobody to administer it. The server must refuse with an explicit 400
# -- not a 500, which is what happened before invariant violations were
# converted.
check_panel PATCH "/console/api/admin/users/${ADMIN_USER}" \
    '{"groups":["doctor"]}' 'refuses to lose the last admin' 400
check_panel PATCH "/console/api/admin/users/${ADMIN_USER}" \
    '{"disabled":true}' 'refuses to disable the last admin' 400

# The administrator created by the wizard is the only account: changing its
# password affects nothing beyond this test's session, deleted afterwards.
check_panel PATCH "/console/api/admin/users/${ADMIN_USER}/password" \
    '{"new_password":"nouveau-mot-de-passe-e2e-123"}' 'password change'

# --- DICOM chain -----------------------------------------------------------
# The rest of the test checks that URLs answer. Here we check the product
# does its job: that a study goes in, gets indexed while keeping its
# metadata, and comes back out through DICOMweb -- which the viewers rely
# on.
step "DICOM chain"

# A valid DICOM, built on the fly: no binary file to version, and the
# identifiers are unique on every run.
cat > /tmp/e2e-gen-dicom.py <<'PYDICOM'
import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

size = 64
y, x = np.ogrid[:size, :size]
img = (x + y).astype(np.uint16) * 16

meta = FileMetaDataset()
meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.7'
meta.MediaStorageSOPInstanceUID = generate_uid()
meta.TransferSyntaxUID = ExplicitVRLittleEndian
meta.ImplementationClassUID = generate_uid()

ds = Dataset()
ds.file_meta = meta
ds.is_little_endian = True
ds.is_implicit_VR = False
ds.PatientName = 'E2E^Patient'
ds.PatientID = 'E2E-0001'
ds.PatientBirthDate = '19700101'
ds.PatientSex = 'O'
ds.StudyInstanceUID = generate_uid()
ds.SeriesInstanceUID = generate_uid()
ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
ds.SOPClassUID = meta.MediaStorageSOPClassUID
ds.StudyDate = '20260729'
ds.StudyTime = '100000'
ds.StudyDescription = 'E2E validation study'
ds.SeriesDescription = 'Synthetic series'
ds.AccessionNumber = 'ACC-E2E-1'
ds.Modality = 'OT'
ds.StudyID = '1'
ds.SeriesNumber = 1
ds.InstanceNumber = 1
ds.SamplesPerPixel = 1
ds.PhotometricInterpretation = 'MONOCHROME2'
ds.Rows = size
ds.Columns = size
ds.BitsAllocated = 16
ds.BitsStored = 16
ds.HighBit = 15
ds.PixelRepresentation = 0
ds.PixelData = img.tobytes()
ds.save_as('/output/e2e.dcm', write_like_original=False)
print('ok')
PYDICOM

if docker run --rm -v /tmp:/output -v /tmp/e2e-gen-dicom.py:/gen.py:ro \
    python:3.11-slim sh -c 'pip install -q pydicom numpy && python /gen.py' \
    >/dev/null 2>&1 && [[ -f /tmp/e2e.dcm ]]; then
    ok "test DICOM built ($(stat -c%s /tmp/e2e.dcm) bytes)"
else
    fail "cannot build the test DICOM"
fi

if [[ -f /tmp/e2e.dcm ]]; then
    # The import endpoint asks for no Authelia session -- it is the path for
    # scripts -- but for proper HTTP Basic authentication, whose credentials
    # are generated by bootstrap.sh.
    UP_USER=$(grep '^UPLOAD_USER=' .env | cut -d= -f2-)
    UP_PASS=$(grep '^UPLOAD_PASSWORD=' .env | cut -d= -f2-)

    # An upload without credentials must be refused: this endpoint accepts
    # medical data without a session, and leaving it open would let anyone on
    # the network feed the database.
    refused=$(curl -ks -o /dev/null -m 20 -w '%{http_code}' \
        -X POST "${URL}/api-upload/instances" \
        -H 'Content-Type: application/dicom' --data-binary @/tmp/e2e.dcm)
    if [[ "$refused" == "401" ]]; then
        ok "upload without credentials refused (401)"
    else
        fail "upload without credentials: $refused instead of 401"
    fi

    code=$(curl -ks -o /tmp/e2e-upload.json -m 60 -w '%{http_code}' \
        -u "${UP_USER}:${UP_PASS}" \
        -X POST "${URL}/api-upload/instances" \
        -H 'Content-Type: application/dicom' --data-binary @/tmp/e2e.dcm)
    if [[ "$code" == "200" ]]; then
        ok "upload accepted by /api-upload/instances"
    else
        fail "upload refused: HTTP $code $(head -c 90 /tmp/e2e-upload.json)"
    fi

    # Indexing: the study must show up in Orthanc's count.
    studies=$(curl -ks -m 20 -b "$COOKIES" "${URL}/statistics" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["CountStudies"])' 2>/dev/null)
    if [[ "$studies" == "1" ]]; then
        ok "study indexed (1 study)"
    else
        fail "indexing: ${studies:-unreadable} study/studies instead of 1"
    fi

    # Metadata: a study that is indexed but has lost its tags is useless --
    # searching by patient or by date would not find it.
    read_back=$(curl -ks -m 20 -b "$COOKIES" "${URL}/studies?expand" \
        | python3 -c 'import json,sys; e=json.load(sys.stdin)[0]; print(e["PatientMainDicomTags"]["PatientID"], e["MainDicomTags"]["AccessionNumber"])' 2>/dev/null)
    if [[ "$read_back" == "E2E-0001 ACC-E2E-1" ]]; then
        ok "metadata preserved (patient and accession number)"
    else
        fail "metadata altered: '${read_back:-unreadable}'"
    fi

    # DICOMweb: this is how the viewers fetch the images.
    count=$(curl -ks -m 20 -b "$COOKIES" "${URL}/dicom-web/studies" \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null)
    if [[ "$count" == "1" ]]; then
        ok "study exposed through DICOMweb"
    else
        fail "DICOMweb returns ${count:-unreadable} study/studies instead of 1"
    fi

    # The DICOM is written by the container, therefore as root: deleting it
    # requires going through a container, as for the working directory.
    rm -f /tmp/e2e-gen-dicom.py
    docker run --rm -v /tmp:/tmp-host alpine rm -f /tmp-host/e2e.dcm >/dev/null 2>&1
fi

# --- File ownership --------------------------------------------------------
# Authelia and auth-service write into the repository. If they run as root
# they take ownership of the directories and any later reinstall fails on a
# "Permission denied" -- the README's reset procedure becomes inapplicable.
# The compose file forces the user's identity on them; we check it is
# applied, since nothing else would report it before the next reinstallation.
step "Ownership of written files"
foreign=$(find services/authelia/config services/orthanc/config data \
    ! -user "$(id -u)" 2>/dev/null | head -5)
if [[ -z "$foreign" ]]; then
    ok "everything belongs to the current user"
else
    fail "files belonging to another user:"
    printf '      %s\n' $foreign
fi

# --- Docker proxy scope ----------------------------------------------------
# The Docker socket amounts to root on the host. The proxy exposes only one
# operation, restarting a container; everything else must be refused.
#
# This test is not theoretical: with CONTAINERS=1, the POST=1 variable opened
# ALL of /containers/*, including POST /containers/create. A privileged
# container mounting the host root was then accepted -- exactly the escape
# this mount must prevent. The regression would fit in a single line of the
# compose file, breaking nothing visible: hence this check.
step "Docker proxy scope"
if compose ps --services 2>/dev/null | grep -qx socket-proxy; then
    # Through python, not curl: the auth-service image ships neither. Calling
    # a missing binary returned "000" for every check, so all three reported
    # an escape that was not one -- a security test that could only cry wolf.
    query_proxy() {
        # $1 = method, $2 = path, $3 = JSON body (optional)
        compose exec -T auth-service python -c '
import sys, urllib.request, urllib.error
method, path = sys.argv[1], sys.argv[2]
body = sys.argv[3].encode() if len(sys.argv) > 3 and sys.argv[3] else None
req = urllib.request.Request("http://socket-proxy:2375" + path,
                             data=body, method=method)
if body:
    req.add_header("Content-Type", "application/json")
try:
    print(urllib.request.urlopen(req, timeout=20).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("000")
' "$1" "$2" "${3:-}" 2>/dev/null | tr -d '\r' || echo "000"
    }

    code=$(query_proxy POST "/containers/create" \
        '{"Image":"alpine","HostConfig":{"Privileged":true,"Binds":["/:/host"]}}')
    if [[ "$code" == "403" ]]; then
        ok "container creation refused (HTTP 403)"
    else
        fail "privileged container creation ACCEPTED (HTTP $code) -- escape possible"
    fi

    code=$(query_proxy POST "/containers/orthanc-server/exec" '{"Cmd":["id"]}')
    if [[ "$code" == "403" ]]; then
        ok "exec in a container refused (HTTP 403)"
    else
        fail "exec in a container ACCEPTED (HTTP $code)"
    fi

    code=$(query_proxy GET "/images/json")
    if [[ "$code" == "403" ]]; then
        ok "access to images refused (HTTP 403)"
    else
        fail "access to images ACCEPTED (HTTP $code)"
    fi
else
    ok "no socket-proxy service, nothing to check"
fi

# --- Summary ---------------------------------------------------------------
step "Summary"
if [[ $FAILURES -eq 0 ]]; then
    printf "  ${GREEN}Full installation validated.${RESET}\n"
    exit 0
fi
printf "  ${RED}%d check(s) failed.${RESET}\n" "$FAILURES"
exit 1
