#!/bin/sh
# =============================================================================
# Applies a change to the stack, with nothing to remember.
# =============================================================================
# Rationale: on this installation, three distinct outages had the same
# cause -- a change written but never put into service, or only half put
# into service.
#
#   1. Authelia had been running for 5 days on a configuration 3 days old:
#      it only re-reads configuration.yml at startup. Result: everything
#      answered 401, login page included, for 3 days.
#
#   2. nginx.ssl.conf is a TEMPLATE, rendered when the container starts.
#      Editing it changes nothing until nginx has restarted.
#
#   3. nginx resolves its upstreams ONCE, at startup. Recreating a
#      container gives it a new address; nginx keeps hitting the old one.
#      On 2026-08-25, authelia and auth-service swapped theirs and nginx
#      sent authentication to the admin panel. No more menu, no more
#      session, no error visible on the user side.
#
# This script wraps the only correct sequence. Use it instead of calling
# docker-compose by hand.
#
# Usage:
#   scripts/apply.sh                 recreates what changed, then refreshes nginx
#   scripts/apply.sh authelia        same, limited to one service
#   scripts/apply.sh ohif orthanc    same, several services
#
# --no-build is always passed: auth-service carries `pull_policy: build` and
# would otherwise be rebuilt from the local sources, replacing the image in
# service with a never-tested version. To rebuild on purpose, use an explicit
# `docker build`, not this script.
# =============================================================================
set -eu
PATH=/usr/local/bin:$PATH
cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------------
# Safeguard: never (re)start Authelia on an invalid configuration
# ---------------------------------------------------------------------------
# Authelia only checks configuration.yml AT STARTUP. On 2026-09-13, the
# configuration in service no longer passed its own validation -- asset_path
# pointed to /config/assets, a directory gone since -- while Authelia had
# been running unperturbed for two weeks. The next restart, through this
# script or a NAS reboot, would have cut every connection to the PACS.
#
# So validation happens BEFORE touching anything, on every run: one second, and
# the file read is the one on disk (mounted directory), not the one Authelia
# loaded at startup.
if docker ps --format '{{.Names}}' | grep -qx orthanc-authelia; then
    echo "== Validating the Authelia configuration =="
    if ! validation=$(docker exec orthanc-authelia authelia validate-config --config /config/configuration.yml 2>&1); then
        # The only error lines: Authelia mixes its usage help in, in an order
        # that varies (standard output and error interleaved).
        echo "$validation" | grep -E '^[[:space:]]+- ' | sed 's/^[[:space:]]*/   /'
        echo "FAILED: invalid Authelia configuration -- nothing was applied."
        exit 1
    fi
    echo "   configuration valid"
fi

echo "== Applying changes =="
if [ $# -gt 0 ]; then
    echo "   target services: $*"
    docker-compose up -d --no-deps --no-build "$@"
else
    echo "   all services"
    docker-compose up -d --no-build
fi

# ---------------------------------------------------------------------------
# File mounts: detecting detachment
# ---------------------------------------------------------------------------
# Several files are mounted into containers as FILE mounts: .env into
# auth-service (not a directory: mounting the root would give a web-facing
# service write access to docker-compose.yml and to the scripts), the nginx
# template, OHIF's app-config.js, orthanc.json. A file mount follows the
# INODE, not the path.
#
# Consequence: any tool that writes by atomic replacement -- `sed -i`, git,
# most editors -- creates a new file and renames it over the old one. The
# inode changes, the container stays attached to the old one, now orphaned. It
# then reads a frozen version indefinitely, and its own writes go nowhere
# WHILE BELIEVING THEY SUCCEED.
#
# Found on 2026-08-27 for .env: after the secrets rotation (done with
# `sed -i`), the panel was still reading the old values, the ones that had
# leaked. Found again on 2026-09-14 for app-config.js and the nginx template:
# OHIF had been serving its 29 August configuration for two weeks, and a
# restarted nginx would have rendered a stale template. Only .env was checked.
#
# `sed -i` cannot be forbidden to everyone. It can be detected -- on every
# file mount of every running container of the stack.
#
# Prints "container service destination host-inode container-inode" per
# detached mount. The prefix is a parameter so the check can be tested on a
# throwaway container.
detached_mounts() {
    prefix=$1
    for c in $(docker ps --format '{{.Names}}' | grep "^$prefix"); do
        service=$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.service"}}' 2>/dev/null)
        docker inspect "$c" --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{println}}{{end}}{{end}}' 2>/dev/null |
        while IFS='|' read -r source dest; do
            [ -f "$source" ] || continue
            host_inode=$(stat -c %i "$source" 2>/dev/null || echo "?")
            cont_inode=$(docker exec "$c" stat -c %i "$dest" 2>/dev/null || echo "?")
            if [ "$host_inode" != "?" ] && [ "$cont_inode" != "?" ] && [ "$host_inode" != "$cont_inode" ]; then
                echo "$c ${service:-?} $dest $host_inode $cont_inode"
            fi
        done
    done
}

DETACHED=$(detached_mounts orthanc-)
if [ -n "$DETACHED" ]; then
    echo "== File mount(s) detached from their container =="
    echo "$DETACHED" | while read -r c service dest host_inode cont_inode; do
        echo "   $c: $dest (inode $cont_inode, file on disk is now $host_inode)"
    done
    echo "   The container was reading a ghost file. Recreating:"
    for service in $(echo "$DETACHED" | awk '$2 != "?" {print $2}' | sort -u); do
        echo "   - $service"
        docker-compose up -d --force-recreate --no-deps --no-build "$service" >/dev/null
    done
    sleep 4
    STILL=$(detached_mounts orthanc-)
    if [ -n "$STILL" ]; then
        echo "   STILL DETACHED after recreation:"
        echo "$STILL" | sed 's/^/     /'
        MOUNT_FAILURES=1
    fi
fi

# Always, unconditionally. Restarting nginx costs two seconds, forgetting to do
# it costs a silent authentication outage.
echo "== Refreshing the addresses seen by nginx =="
docker restart orthanc-nginx >/dev/null
sleep 6

echo "== Verification =="
docker ps --filter name=orthanc- --format '   {{.Names}} | {{.Status}}'

# The domain is read from .env, never hard-coded. nginx rewrites Host to DOMAIN
# and Authelia refuses anything that does not match its cookie domain: testing
# with a stale domain returns 403s that look like an outage, or worse,
# reassuring 200s on the wrong target. The panel can change the domain (network
# tab, it covers .env and the eleven occurrences in configuration.yml), so this
# value moves without warning.
DOMAINE=$(grep -E '^DOMAIN=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r')
ECHECS=${MOUNT_FAILURES:-0}

if [ -z "$DOMAINE" ]; then
    echo "   DOMAIN not found in .env -- route check skipped."
    ECHECS=$((ECHECS + 1))
else
    echo "   --- routes (domain: $DOMAINE) ---"
    # Each route carries the code EXPECTED from it, and the two are compared.
    #
    # The script used to merely print the codes and then an "expected: ..."
    # line, without ever comparing the two, and always exited successfully. An
    # /auth/ at 502 was shown next to "expected 200" and the deployment looked
    # successful -- you had to read it yourself, every time.
    for paire in "/auth/:200" "/api/state:200" "/ui/app/:302" "/ohif/:302"; do
        route=${paire%:*}
        attendu=${paire##*:}
        code=$(curl -sk -o /dev/null -w '%{http_code}' -H "Host: $DOMAINE" \
               "https://localhost:30443$route" 2>/dev/null || echo '000')
        if [ "$code" = "$attendu" ]; then
            printf '   %-14s %s\n' "$route" "$code"
        else
            printf '   %-14s %s   <-- EXPECTED %s\n' "$route" "$code" "$attendu"
            ECHECS=$((ECHECS + 1))
        fi
    done
fi

echo "   --- secrets ---"
# Authelia does NO interpolation in its YAML: its configuration literally
# carries `secret: ${AUTHELIA_SESSION_SECRET}`, and it is the AUTHELIA_*
# environment variables that override it. If one disappears from .env, Authelia
# does not complain -- it takes the string "${AUTHELIA_SESSION_SECRET}" as is,
# as its session secret. And that string is published in the repository: every
# session would become forgeable, without the slightest message.
MANQUANTS=0
for v in AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_ENCRYPTION_KEY AUTHELIA_JWT_SECRET AUTH_PASSWORD; do
    val=$(grep -E "^${v}=" .env 2>/dev/null | cut -d= -f2- | tr -d '')
    if [ -z "$val" ]; then
        echo "   $v: MISSING from .env"
        MANQUANTS=$((MANQUANTS + 1))
    elif [ "${val#*\$\{}" != "$val" ]; then
        echo "   $v: contains an unsubstituted placeholder -- $val"
        MANQUANTS=$((MANQUANTS + 1))
    elif [ ${#val} -lt 16 ]; then
        echo "   $v: suspiciously short (${#val} characters)"
        MANQUANTS=$((MANQUANTS + 1))
    fi
done
if [ "$MANQUANTS" -eq 0 ]; then
    echo "   4 secrets present and substituted"
else
    ECHECS=$((ECHECS + MANQUANTS))
fi

echo "   --- nginx errors over the last minute ---"
# The pattern only looked for "Connection refused". Yet the outage met on
# 2026-08-29 -- Authelia refusing to start -- made nginx write "host not found
# in upstream", which this filter did not see: it showed 0 and announced
# well-resolved upstreams while the stack was down.
MOTIFS='Connection refused|host not found in upstream|no live upstreams|upstream timed out|\[emerg\]|\[alert\]'
ERREURS=$(docker logs --since 60s orthanc-nginx 2>&1 | grep -cE "$MOTIFS" || true)
echo "   $ERREURS"
if [ "$ERREURS" -gt 0 ]; then
    echo "   ^ unreachable upstream or rejected configuration -- excerpt:"
    docker logs --since 60s orthanc-nginx 2>&1 | grep -E "$MOTIFS" | tail -3 | sed 's/^/     /'
    ECHECS=$((ECHECS + 1))
else
    echo "   (0 = no unreachable upstream, no configuration error)"
fi

echo
if [ "$ECHECS" -eq 0 ]; then
    echo "== Deployment verified: everything as expected =="
    exit 0
fi
echo "== WARNING: $ECHECS check(s) failed, see above =="
echo "   The stack may be running anyway, but it does not respond as"
echo "   expected. Do not consider this deployment successful."
exit 1
