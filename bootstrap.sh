#!/usr/bin/env bash
# =============================================================================
# ORTHANC-AUTHELIA — Bootstrap
# =============================================================================
# Prepares a fresh installation with randomly generated secrets
# and the config files in the right places.
#
# Usage:
#   ./bootstrap.sh          # full setup, refuses to overwrite
#   ./bootstrap.sh --force  # overwrites .env and the existing configs
#
# At the end, all that is left to do is:
#   docker compose up -d
# =============================================================================

set -euo pipefail

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
    FORCE=1
fi

# Replaces one string with another in a file, without a regular expression. sed
# does not fit here: the substituted values are passwords and argon2id hashes,
# which contain $ and / -- that is, sed's delimiters and back-references.
# Bash's ${var//pattern/value} expansion treats both as plain text.
#
# Also avoids a dependency on Python, absent from Git Bash on Windows: the
# script now requires only bash, docker and openssl, all three present with Git
# for Windows and Docker Desktop.
remplacer_dans() {
    local fichier=$1 motif=$2 valeur=$3 contenu
    contenu=$(<"$fichier")
    printf '%s\n' "${contenu//"$motif"/"$valeur"}" > "$fichier"
}

info()  { printf "\033[36m→\033[0m %s\n" "$*"; }
ok()    { printf "\033[32m✓\033[0m %s\n" "$*"; }
warn()  { printf "\033[33m!\033[0m %s\n" "$*"; }
err()   { printf "\033[31m✗\033[0m %s\n" "$*" >&2; }

# ---------------------------------------------------------------------------
# Dependances
# ---------------------------------------------------------------------------
info "Checking dependencies"
for cmd in docker openssl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "$cmd is missing. Install it before continuing."
        exit 1
    fi
done
if ! docker compose version >/dev/null 2>&1; then
    err "docker compose (v2) is missing. Install the plugin:"
    err "  sudo apt install docker-compose-v2  # Ubuntu/Debian"
    err "  or Docker Desktop, which ships it"
    exit 1
fi
# `docker compose version` and `command -v docker` only query the client: they
# succeed even when the account cannot reach the daemon. The script then passed
# this check, and died silently while generating the argon2id hash (first
# `docker run`), leaving a user database Authelia refuses to start on -- and
# nginx with it. Found on 2026-09-13 on a fresh install under WSL Ubuntu, with
# an account outside the docker group.
if ! docker info >/dev/null 2>&1; then
    err "The Docker daemon cannot be reached from this account."
    err "Either it is not running (Docker Desktop, docker service),"
    err "or this account is not allowed to use it (\"permission denied\""
    err "on /var/run/docker.sock). In that case:"
    err "  sudo usermod -aG docker \$USER"
    err "then close and reopen the terminal, and run ./bootstrap.sh again"
    exit 1
fi
ok "docker + docker compose + openssl OK"

# ---------------------------------------------------------------------------
# Identity of the containers that write into the repository
# ---------------------------------------------------------------------------
# Authelia and auth-service write into ./services/*/config. Without an
# imposed identity they run as root and take ownership of those directories,
# making any reinstallation impossible without fixing permissions by hand.
# They are given the current user's identity: the files they create belong
# to that user, and the problem does not arise at all.
PUID=$(id -u)
PGID=$(id -g)

# ---------------------------------------------------------------------------
# Owner of the configuration directories
# ---------------------------------------------------------------------------
# Authelia and Orthanc run as root in their containers and take ownership of
# the directories they mount from the very first start. On reinstallation,
# copying the templates then fails with a terse "Permission denied", with no
# hint about what to do -- and the reset procedure documented in the README
# becomes unusable.
#
# Ownership is handed back to the current user, through a container since that
# user precisely no longer has the rights. No docker as root, no sudo: the
# docker daemon does the work.
CONFIG_DIRS="services/authelia/config services/orthanc/config data"
BESOIN_REPRISE=""
for d in $CONFIG_DIRS; do
    [[ -d $d ]] || continue
    if [[ ! -w $d ]]; then
        BESOIN_REPRISE="$BESOIN_REPRISE $d"
    fi
done

if [[ -n ${BESOIN_REPRISE// /} ]]; then
    info "Directories owned by another user (containers):$BESOIN_REPRISE"
    if docker run --rm -v "$PWD:/depot" alpine \
        sh -c "chown -R $(id -u):$(id -g)$(printf ' /depot/%s' $BESOIN_REPRISE)" \
        >/dev/null 2>&1; then
        ok "Ownership restored on$BESOIN_REPRISE"
    else
        err "Could not take back ownership of:$BESOIN_REPRISE"
        err "Run manually:"
        err "  docker run --rm -v \"\$PWD:/depot\" alpine chown -R $(id -u):$(id -g) /depot"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# docker-compose.yml
# ---------------------------------------------------------------------------
if [[ -f docker-compose.yml ]]; then
    if [[ $FORCE -eq 1 ]]; then
        warn "docker-compose.yml exists — overwritten (--force)"
        cp docker-compose.yml.example docker-compose.yml
    else
        info "docker-compose.yml exists — kept"
    fi
else
    cp docker-compose.yml.example docker-compose.yml
    ok "docker-compose.yml created from the template"
fi

# ---------------------------------------------------------------------------
# .env with random secrets
# ---------------------------------------------------------------------------
if [[ -f .env ]] && [[ $FORCE -eq 0 ]]; then
    info ".env exists — kept. Use --force to regenerate it."
else
    if [[ -f .env ]]; then
        cp .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
        warn "Previous .env backed up"
    fi
    # Generate secrets, 64 hex chars each
    S1=$(openssl rand -hex 32)
    S2=$(openssl rand -hex 32)
    S3=$(openssl rand -hex 32)

    # Authelia encrypts its session database with STORAGE_ENCRYPTION_KEY. If
    # the database already exists, generating a new key makes it unreadable:
    #   "the configured encryption key does not appear to be valid for this
    #    database"
    # and Authelia refuses to start. The previous key is therefore kept.
    if [[ -f services/authelia/config/db.sqlite3 ]]; then
        # || true is essential: on a fresh install .env does not exist
        # yet, while the database may already be there. 2>/dev/null hides
        # grep's message but not its exit code; under set -e the
        # assignment fails and the script dies without printing anything.
        EXISTING_KEY=$(grep '^AUTHELIA_STORAGE_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || true)
        if [[ -n ${EXISTING_KEY:-} ]]; then
            S2=$EXISTING_KEY
            warn "Existing Authelia database: encryption key kept"
            warn "  (to start from scratch: delete services/authelia/config/db.sqlite3)"
        fi
    fi
    # Password of the embedded PostgreSQL. Like Authelia's storage key, it must
    # not change once the database is created: PostgreSQL reads
    # POSTGRES_PASSWORD only when the volume is initialised. A --force that
    # drew a new one would leave Orthanc without access to its own images, on
    # an authentication failure nothing links back to this script. The one from
    # the previous .env is therefore kept if it exists; `docker compose down
    # -v` (which erases the volume) is the only reset that justifies changing
    # it.
    PG_PASS=$(openssl rand -hex 24)
    EXISTING_PG=$(grep '^POSTGRES_PASSWORD=' .env 2>/dev/null | cut -d= -f2- || true)
    if [[ -n ${EXISTING_PG:-} ]]; then
        PG_PASS=$EXISTING_PG
        warn "Existing PostgreSQL password kept (the database depends on it)"
    fi
    AUTH_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    ORTHANC_PASS=$(openssl rand -hex 32)
    # Credentials for the programmatic import endpoint (/api-upload/). Without
    # them, the nginx entrypoint generates no htpasswd file and the endpoint
    # refuses everything: better to ship it usable, protected by a generated
    # password, than disabled or -- worse -- open to all.
    #
    # Interface language. It is derived from the system's when a matching
    # translation exists: otherwise the panel shows in English on a
    # French-speaking machine, with nothing saying where that choice comes from
    # or how to change it.
    #
    # Any language whose translation file exists is accepted, not only fr and
    # en: adding a language does not require modifying this script. It is only
    # an initial value -- the wizard offers the browser's language and saves
    # it, and the panel lets you change it.
    LANGUE_SYSTEME=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
    LANGUE_SYSTEME=$(printf '%s' "$LANGUE_SYSTEME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[_.@:-].*$//')
    if [[ -n $LANGUE_SYSTEME && -f "services/auth-service/sources/translations/${LANGUE_SYSTEME}.json" ]]; then
        LANGUAGE_VALUE=$LANGUE_SYSTEME
    else
        LANGUAGE_VALUE="en"
    fi

    UPLOAD_USER_VALUE="import-dicom"
    UPLOAD_PASS_VALUE=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)

    # Two questions, not one URL to compose. Everything else is generated or
    # has a default; these cannot be guessed. https is not asked: it is the
    # only scheme this stack serves.
    #
    # BOOTSTRAP_DOMAIN and BOOTSTRAP_HTTPS_PORT answer without a terminal (CI);
    # BOOTSTRAP_PUBLIC_URL, kept for compatibility, fills both at once.
    DOMAINE_DEFAUT="pacs.localhost"
    PORT_DEFAUT="30443"
    if [[ -n ${BOOTSTRAP_PUBLIC_URL:-} ]]; then
        BOOTSTRAP_DOMAIN=${BOOTSTRAP_DOMAIN:-$(printf '%s' "$BOOTSTRAP_PUBLIC_URL" | sed -E 's#^https?://##; s#[:/].*$##')}
        BOOTSTRAP_HTTPS_PORT=${BOOTSTRAP_HTTPS_PORT:-$(printf '%s' "$BOOTSTRAP_PUBLIC_URL" | sed -nE 's#^https?://[^/:]+:([0-9]+).*#\1#p')}
    fi

    DOMAIN_SAISI=${BOOTSTRAP_DOMAIN:-}
    if [[ -z $DOMAIN_SAISI && -t 0 ]]; then
        printf "\n  1. Domain name this PACS is reached at, https only.\n"
        printf "     A real name for an installation on the Internet (pacs.example.org),\n"
        printf "     or the local default for a test on this machine.\n"
        printf "     [%s] > " "$DOMAINE_DEFAUT"
        read -r DOMAIN_SAISI || true
    fi
    DOMAIN_SAISI=${DOMAIN_SAISI:-$DOMAINE_DEFAUT}
    # Forgiving: a pasted https://pacs.example.org:30443/ gives the name, and
    # its port becomes the default of the next question.
    PORT_COLLE=$(printf '%s' "$DOMAIN_SAISI" | sed -nE 's#^(https?://)?[^/:]+:([0-9]+).*#\2#p')
    DOMAIN_SAISI=$(printf '%s' "$DOMAIN_SAISI" | sed -E 's#^https?://##; s#[:/].*$##' | tr -d '[:space:]')
    [[ -n $PORT_COLLE ]] && PORT_DEFAUT=$PORT_COLLE

    # A host name without a dot makes the browser reject the cookie (RFC 6265):
    # Authelia authenticates, sets its cookie, and the next request goes out
    # anonymous again -- a login loop with no error message. "localhost" is the
    # only accepted exception.
    if [[ -z $DOMAIN_SAISI ]]; then
        err "Empty domain name."
        exit 1
    fi
    if [[ $DOMAIN_SAISI != *.* && $DOMAIN_SAISI != "localhost" ]]; then
        err "'$DOMAIN_SAISI' contains no dot: the browser will reject the"
        err "session cookie and sign-in will loop without any message."
        err "Use a qualified name, for example pacs.example.org"
        exit 1
    fi

    # The port this machine listens on, and the port in the public address --
    # they are the same thing, which is what an installation answering on
    # 30443 while announcing 30003 got wrong.
    HTTPS_PORT_VALUE=${BOOTSTRAP_HTTPS_PORT:-}
    if [[ -z $HTTPS_PORT_VALUE && -t 0 ]]; then
        printf "\n  2. HTTPS port of this machine.\n"
        printf "     443 to serve the standard port directly; keep %s behind a\n" "$PORT_DEFAUT"
        printf "     Cloudflare tunnel, a reverse proxy, or for a local test.\n"
        printf "     [%s] > " "$PORT_DEFAUT"
        read -r HTTPS_PORT_VALUE || true
    fi
    HTTPS_PORT_VALUE=$(printf '%s' "${HTTPS_PORT_VALUE:-$PORT_DEFAUT}" | tr -d '[:space:]')
    if [[ ! $HTTPS_PORT_VALUE =~ ^[0-9]+$ ]] || (( HTTPS_PORT_VALUE < 1 || HTTPS_PORT_VALUE > 65535 )); then
        err "'$HTTPS_PORT_VALUE' is not a port number (1-65535)."
        exit 1
    fi
    HTTP_PORT_VALUE=30080
    [[ $HTTPS_PORT_VALUE == 30080 ]] && HTTP_PORT_VALUE=30081
    [[ $HTTPS_PORT_VALUE == 443 ]] && HTTP_PORT_VALUE=80

    # The public address is derived, never typed: https is the only scheme, and
    # 443 is not written -- a cookie carries no port and neither should the URL.
    if [[ $HTTPS_PORT_VALUE == 443 ]]; then
        PUBLIC_URL_VALUE="https://${DOMAIN_SAISI}"
    else
        PUBLIC_URL_VALUE="https://${DOMAIN_SAISI}:${HTTPS_PORT_VALUE}"
    fi
    ok "Public address: ${PUBLIC_URL_VALUE}"

    # Optional Cloudflare tunnel: publishes the PACS without opening a port on
    # the router. The token may also come from the environment, for an
    # unattended run. Read without echo: it is a secret.
    TUNNEL_TOKEN_VALUE=${CLOUDFLARE_TUNNEL_TOKEN:-}
    if [[ -z $TUNNEL_TOKEN_VALUE && -t 0 ]]; then
        printf "\n  Cloudflare tunnel token, to publish the PACS without opening a port\n"
        printf "  (Zero Trust -> Networks -> Tunnels -> Create -> Docker: the string\n"
        printf "  after --token). Press Enter to skip.\n"
        printf "  > "
        read -rs TUNNEL_TOKEN_VALUE || true
        printf "\n"
    fi
    TUNNEL_TOKEN_VALUE=$(printf '%s' "$TUNNEL_TOKEN_VALUE" | tr -d '[:space:]')
    if [[ -n $TUNNEL_TOKEN_VALUE && ! $TUNNEL_TOKEN_VALUE =~ ^[A-Za-z0-9_.=+/-]+$ ]]; then
        err "The tunnel token contains unexpected characters: paste only the"
        err "string that follows --token in the command Cloudflare shows."
        exit 1
    fi
    TUNNEL_PROFILE_VALUE=""
    [[ -n $TUNNEL_TOKEN_VALUE ]] && TUNNEL_PROFILE_VALUE="tunnel"

    # A host name without a dot makes the browser reject the cookie (RFC 6265):
    # Authelia authenticates, sets its cookie, and the next request goes out
    # anonymous again -- a login loop with no error message. "localhost" is the
    # only accepted exception.
    DOMAIN_SAISI=$(printf '%s' "$PUBLIC_URL_VALUE" | sed -E 's#^https?://##; s#:[0-9]+$##; s#/.*$##')
    if [[ $DOMAIN_SAISI != *.* && $DOMAIN_SAISI != "localhost" ]]; then
        err "'$DOMAIN_SAISI' contains no dot: the browser will reject the"
        err "session cookie and sign-in will loop without any message."
        err "Use a qualified name, for example https://pacs.example.org"
        exit 1
    fi
    if [[ $PUBLIC_URL_VALUE != https://* ]]; then
        err "The public address must start with https:// (got: $PUBLIC_URL_VALUE)"
        exit 1
    fi

    # Default PUBLIC_URL: full local URL, including the compose port. The host
    # name (pacs.localhost) must contain a dot, otherwise Authelia rejects the
    # cookie domain (RFC 6265). None of these values is meant to be typed by a
    # human: the .env.example template only carries the SHAPE of the file, this
    # script fills it. The three Authelia secrets are never even displayed.
    #
    # DOMAIN and PUBLIC_URL get a local default: the real domain is set later
    # from the panel, network tab, which knows how to cover the twelve places
    # where it lives. pacs.localhost carries a dot, without which Authelia
    # rejects the cookie (RFC 6265).
    sed \
        -e "s|^AUTHELIA_SESSION_SECRET=.*|AUTHELIA_SESSION_SECRET=$S1|" \
        -e "s|^AUTHELIA_STORAGE_ENCRYPTION_KEY=.*|AUTHELIA_STORAGE_ENCRYPTION_KEY=$S2|" \
        -e "s|^AUTHELIA_JWT_SECRET=.*|AUTHELIA_JWT_SECRET=$S3|" \
        -e "s|^AUTH_PASSWORD=.*|AUTH_PASSWORD=$AUTH_PASS|" \
        -e "s|^PUBLIC_URL=.*|PUBLIC_URL=${PUBLIC_URL_VALUE}|" \
        -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN_SAISI}|" \
        -e "s|^HTTPS_PORT=.*|HTTPS_PORT=${HTTPS_PORT_VALUE}|" \
        -e "s|^HTTP_PORT=.*|HTTP_PORT=${HTTP_PORT_VALUE}|" \
        -e "s|^LANGUAGE=.*|LANGUAGE=${LANGUAGE_VALUE}|" \
        -e "s|^UPLOAD_USER=.*|UPLOAD_USER=${UPLOAD_USER_VALUE}|" \
        -e "s|^UPLOAD_PASSWORD=.*|UPLOAD_PASSWORD=${UPLOAD_PASS_VALUE}|" \
        -e "s|^ORTHANC_ADMIN_PASS=.*|ORTHANC_ADMIN_PASS=$ORTHANC_PASS|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG_PASS|" \
        -e "s|^CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${TUNNEL_TOKEN_VALUE}|" \
        -e "s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=${TUNNEL_PROFILE_VALUE}|" \
        .env.example > .env

    ok ".env generated: 6 random secrets (Authelia x3, Orthanc service, DICOM import, PostgreSQL), nothing to type"
    ok "Interface language: ${LANGUAGE_VALUE} (from the system locale; can be changed in the setup wizard and the admin panel)"
    if [[ -n $TUNNEL_TOKEN_VALUE ]]; then
        ok "Cloudflare tunnel enabled: in the dashboard, point ${DOMAIN_SAISI} to"
        ok "  HTTPS nginx:443, No TLS Verify on (see docs/CLOUDFLARE_TUNNEL.md)"
        if [[ $PUBLIC_URL_VALUE == *localhost* ]]; then
            warn "The public address is still ${PUBLIC_URL_VALUE}: set the tunnel's"
            warn "hostname in the admin panel, network tab, or sign-in will fail."
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Directories written by the admin panel
# ---------------------------------------------------------------------------
# Create them HERE and nowhere else: if a bind-mounted directory does not
# exist on the host, Docker creates it itself, and it then belongs to root.
# The containers run under PUID/PGID (see .env) and fail to write there, with
# a "Permission denied" that no longer has anything to do with its cause.
for dossier in data/admin-backups certs; do
    if [[ ! -d "$dossier" ]]; then
        mkdir -p "$dossier"
        ok "$dossier/ created"
    fi
done

# Interface language: no more data/app-settings/settings.json file. This script
# used to write it, but no container mounts that directory -- the setting was
# read by nothing, and "editable from the panel" was false. The initial value
# is LANGUAGE in .env; the wizard and the panel then save the choice in
# data/admin-backups/settings.json, which takes precedence.

# ---------------------------------------------------------------------------
# Configs Authelia + Orthanc
# ---------------------------------------------------------------------------
copy_if_missing() {
    local src=$1
    local dest=$2
    mkdir -p "$(dirname "$dest")"
    if [[ -f $dest ]] && [[ $FORCE -eq 0 ]]; then
        info "$dest exists — kept"
    else
        cp "$src" "$dest"
        ok "$dest copied from $src"
    fi
}

# server.asset_path points to /config/assets, and Authelia refuses to START if
# that directory does not exist ("error occurred reading the '/config/assets'
# directory"). It is only checked at startup: on the original installation it
# had disappeared afterwards, and Authelia was running on a configuration that
# no longer passed its own validation -- a guaranteed outage at the next
# restart. Found on 2026-09-13. Empty is enough.
mkdir -p services/authelia/config/assets
copy_if_missing "authelia-configuration.yml.example" "services/authelia/config/configuration.yml"
copy_if_missing "authelia-users.yml.example"         "services/authelia/config/users_database.yml"
copy_if_missing "orthanc.json.example"               "services/orthanc/config/orthanc.json"

# ---------------------------------------------------------------------------
# Permissions on the files that carry secrets
# ---------------------------------------------------------------------------
# users_database.yml holds the argon2id hashes of every account, and
# Authelia re-reads it live (watch: true): world-readable, it can be
# brute-forced offline; world-WRITABLE, replacing the administrator's hash
# is enough to take over the PACS in a second. notification.txt carries the
# reset links, db.sqlite3 the sessions. The backups directory holds copies
# of all of that.
#
# Found on a real installation on 2026-08-29: everything was 777. The
# containers run as root, tightening does not bother them.
#
# `|| true`: on an ACL share (Synology), chmod may fail for an unprivileged
# user. The message below then says what to do.
for f in services/authelia/config/* data/admin-backups/*; do
    [ -f "$f" ] && chmod 600 "$f" 2>/dev/null || true
done
chmod 700 services/authelia/config data/admin-backups 2>/dev/null || true

# .env carries ALL the secrets, orthanc.json two of them (PostgreSQL database
# and auth-service's service account). They were created with the current
# umask, hence 644 on a fresh install -- readable by any user of the machine.
chmod 600 .env services/orthanc/config/orthanc.json 2>/dev/null || true

# The compose files define WHAT RUNS. World-writable, they allow adding a mount
# of the host root to a container: that is no longer a leak, it is privilege
# escalation. Same reasoning for the scripts, which get executed -- rewriting
# them means getting your own code run at the next launch.
chmod 600 docker-compose.yml docker-compose.override.yml 2>/dev/null || true
chmod 700 bootstrap.sh scripts/*.sh 2>/dev/null || true

for f in .env docker-compose.yml services/orthanc/config/orthanc.json; do
    [ -f "$f" ] || continue
    droits=$(stat -c '%a' "$f" 2>/dev/null || echo '?')
    case "$droits" in
        600|400) ;;
        *) warn "$f is mode $droits, expected 600 -- an ACL share (Synology)"
           warn "may force 777 back. Fix it by hand: chmod 600 $f" ;;
    esac
done

if [ "$(stat -c '%a' services/authelia/config/users_database.yml 2>/dev/null)" != "600" ]; then
    warn "users_database.yml is not 600 (current mode: $(stat -c '%a' services/authelia/config/users_database.yml 2>/dev/null))."
    warn "It holds the password hashes. Fix it from the container:"
    warn "  docker exec orthanc-authelia sh -c 'find /config -type f -exec chmod 600 {} \\;'"
fi

# ---------------------------------------------------------------------------
# Substitution of ${VAR} in the Authelia config
# ---------------------------------------------------------------------------
# Authelia does NOT do shell expansion in its YAML: the template's
# ${AUTHELIA_DOMAIN} and ${REDIS_PORT:-6379} stay literal and crash the
# startup ("option 'domain' is not a valid cookie domain", "cannot parse
# value as 'int'"). They are substituted here, once, at copy time.
AUTHELIA_CFG="services/authelia/config/configuration.yml"
if grep -q '\${' "$AUTHELIA_CFG" 2>/dev/null; then
    # || true is essential: under `set -e` with pipefail, a grep with no match
    # kills the script here WITHOUT PRINTING ANYTHING -- exit code 1,
    # configuration.yml left with its seventeen literal ${...}, and Authelia
    # then refusing to start on "option 'domain' is not a valid cookie
    # domain". Found on the test bench on 2026-08-27.
    # shellcheck disable=SC1091
    PUBLIC_URL_VALUE=$(grep '^PUBLIC_URL=' .env 2>/dev/null | cut -d= -f2- || true)
    if [[ -z ${PUBLIC_URL_VALUE:-} ]]; then
        err "PUBLIC_URL missing from .env: cannot substitute the domain"
        err "in configuration.yml. Check that .env.example has it."
        exit 1
    fi
    # Bare host name, no scheme or port: that is what Authelia's cookie domain
    # expects (a cookie never carries a port).
    DOMAIN_VALUE=$(echo "$PUBLIC_URL_VALUE" | sed -E 's#^https?://##; s#:[0-9]+$##; s#/.*$##')
    sed -i \
        -e "s|\${AUTHELIA_DOMAIN}|${DOMAIN_VALUE}|g" \
        -e "s|\${PUBLIC_URL}|${PUBLIC_URL_VALUE}|g" \
        -e "s|\${REDIS_HOST:-redis}|redis|g" \
        -e "s|\${REDIS_PORT:-6379}|6379|g" \
        -e "s|\${REDIS_DB:-0}|0|g" \
        "$AUTHELIA_CFG"
    ok "configuration.yml: domain ${DOMAIN_VALUE}, public address ${PUBLIC_URL_VALUE}"
fi

# ---------------------------------------------------------------------------
# Authorization plugin password in orthanc.json
# ---------------------------------------------------------------------------
# The plugin authenticates to auth-service with Basic auth, using the values
# of the Authorization section. They must match AUTH_USERNAME and
# AUTH_PASSWORD in .env, otherwise /user/get-profile answers 401 and Orthanc
# refuses every request (403) with no explicit message.
#
# The ORTHANC__AUTHORIZATION__WEB_SERVICE_* variables do not work: Orthanc does
# not apply them to this section, the file's value keeps being used. So it is
# substituted at copy time.
ORTHANC_CFG="services/orthanc/config/orthanc.json"
if grep -q 'set-via-env-AUTH_PASSWORD' "$ORTHANC_CFG" 2>/dev/null; then
    AUTH_USER_VALUE=$(grep '^AUTH_USERNAME=' .env | cut -d= -f2-)
    AUTH_PASS_VALUE=$(grep '^AUTH_PASSWORD=' .env | cut -d= -f2-)
    remplacer_dans "$ORTHANC_CFG" \
        '"WebServiceUsername": "share-user"' \
        "\"WebServiceUsername\": \"${AUTH_USER_VALUE}\""
    remplacer_dans "$ORTHANC_CFG" \
        '"WebServicePassword": "set-via-env-AUTH_PASSWORD"' \
        "\"WebServicePassword\": \"${AUTH_PASS_VALUE}\""
    ok "orthanc.json: Authorization plugin credentials synchronised"
fi

# ---------------------------------------------------------------------------
# PostgreSQL password: .env -> orthanc.json
# ---------------------------------------------------------------------------
# A .env older than the embedded PostgreSQL lacks the line, and a bootstrap
# without --force keeps it as is: docker compose would then refuse to start
# (POSTGRES_PASSWORD missing). The line is added without touching the rest.
if ! grep -qE '^POSTGRES_PASSWORD=.+' .env 2>/dev/null; then
    sed -i '/^POSTGRES_PASSWORD=/d' .env
    printf '\n# Embedded PostgreSQL -- generated by bootstrap.sh, do not change\nPOSTGRES_PASSWORD=%s\n' \
        "$(openssl rand -hex 24)" >> .env
    ok ".env: PostgreSQL password added"
fi
if grep -q 'set-via-env-POSTGRES_PASSWORD' "$ORTHANC_CFG" 2>/dev/null; then
    PG_PASS_VALUE=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
    remplacer_dans "$ORTHANC_CFG" \
        '"Password": "set-via-env-POSTGRES_PASSWORD"' \
        "\"Password\": \"${PG_PASS_VALUE}\""
    ok "orthanc.json: PostgreSQL password synchronised"
fi

# ---------------------------------------------------------------------------
# Valid argon2id hash in users_database.yml
# ---------------------------------------------------------------------------
# The template contains EXAMPLE_HASH_REPLACE_THIS, which is not a parsable
# argon2 hash: Authelia refuses to start ("argon2 decode error"). A real
# hash is generated from a random password that is never displayed or kept.
#
# This bootstrap account exists only because Authelia also refuses to start on
# a database with no user ("users: non zero value required"). It is disabled,
# has no group, and its password is known to nobody: inert. It REMAINS after
# the wizard -- this comment used to claim finalisation deleted it, which it
# does not (checked on 2026-09-13). It shows as disabled in the Users tab, from
# which it can be deleted.
USERS_DB="services/authelia/config/users_database.yml"
if grep -q 'EXAMPLE_HASH_REPLACE_THIS' "$USERS_DB" 2>/dev/null; then
    info "Generating an argon2id hash (with the Authelia image)…"
    THROWAWAY=$(openssl rand -base64 32)
    # `|| true`: under `set -euo pipefail`, a docker failure made the
    # assignment fail and killed the script HERE, with no message (stderr is
    # discarded). The error case below was never reached.
    REAL_HASH=$(docker run --rm authelia/authelia:4.39.20 \
        authelia crypto hash generate argon2 --password "$THROWAWAY" 2>/dev/null \
        | sed 's/^Digest: //') || true
    if [[ $REAL_HASH == '$argon2id$'* ]]; then
        remplacer_dans "$USERS_DB" \
            '$argon2id$v=19$m=65536,t=3,p=4$EXAMPLE_HASH_REPLACE_THIS' \
            "$REAL_HASH"
        ok "users_database.yml: valid argon2id hash (inactive bootstrap account)"
    else
        # An error, not a warning: without this hash, Authelia does not start,
        # nor does nginx, and a "Bootstrap complete" displayed afterwards would
        # be a lie. Re-running the script resumes at this step.
        err "argon2id hash generation failed: Authelia would refuse to start."
        err "Check that docker works without sudo (docker run --rm hello-world),"
        err "then run ./bootstrap.sh again: it will resume at this step."
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Recap
# ---------------------------------------------------------------------------
G=$'\033[32m'; C=$'\033[36m'; R=$'\033[0m'
# The address actually configured, not "localhost": the Authelia session is
# bound to the PUBLIC_URL domain, another host name has no access to it.
URL=$(grep '^PUBLIC_URL=' .env 2>/dev/null | cut -d= -f2- || true)
URL=${URL:-https://pacs.localhost:30443}
cat <<EOF

${G}════════════════════════════════════════════${R}
${G} Bootstrap complete${R}
${G}════════════════════════════════════════════${R}

Next steps:

  1. ${C}Review .env${R} if needed (domain, language, TZ)

  2. ${C}Start the stack${R}:
       docker compose up -d

  3. ${C}Setup wizard${R} — create the first administrator:
       ${URL}/auth/setup
       (self-signed certificate: accept the browser warning)

  4. ${C}After the wizard${R}:
       ${URL}/                Orthanc Explorer
       ${URL}/auth/admin      Administration panel

Start over (deletes every stored image):
  docker compose down -v
  rm -rf .env docker-compose.yml data/admin-backups \\
         services/authelia/config/{configuration.yml,users_database.yml} \\
         services/orthanc/config/orthanc.json
  ./bootstrap.sh

EOF
