#!/usr/bin/env bash
# =============================================================================
# ORTHANC-AUTHELIA — Bootstrap
# =============================================================================
# Prepares a fresh installation with randomly generated secrets and the
# configuration files in the right places.
#
# Usage :
#   ./bootstrap.sh          # setup complet, refuse d'ecraser
#   ./bootstrap.sh --force  # overwrites .env and existing configs
#
# Once done, all that is left is:
#   docker compose up -d
# =============================================================================

set -euo pipefail

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
    FORCE=1
fi

# Replace one string with another in a file, without regular expressions.
# sed will not do here: the substituted values are passwords and argon2id
# hashes, which contain $ and / -- that is, sed's delimiters and
# back-references. Bash's ${var//pattern/value} expansion treats both as
# plain text.
#
# It also avoids a dependency on Python, absent from Git Bash on Windows:
# the script now only requires bash, docker and openssl, all three shipped
# with
# Git for Windows et Docker Desktop.
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
info "Verification des dependances"
for cmd in docker openssl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "$cmd manquant. Installe-le avant de continuer."
        exit 1
    fi
done
if ! docker compose version >/dev/null 2>&1; then
    err "docker compose (v2) manquant. Installe le plugin :"
    err "  sudo apt install docker-compose-v2  # Ubuntu/Debian"
    err "  ou Docker Desktop qui l'embarque"
    exit 1
fi
ok "docker + docker compose + openssl OK"

# ---------------------------------------------------------------------------
# Identity of the containers that write into the repository
# ---------------------------------------------------------------------------
# Authelia and auth-service write into ./services/*/config. Without an
# explicit identity
# imposee ils tournent en root et s'approprient ces dossiers, rendant toute
# reinstalling is impossible without fixing permissions by hand. We give
# them the current user's: the files created belong to them, and
# problem disappears entirely.
PUID=$(id -u)
PGID=$(id -g)

# ---------------------------------------------------------------------------
# Ownership of the configuration directories
# ---------------------------------------------------------------------------
# Authelia and Orthanc run as root inside their containers and take
# ownership of the directories they mount from the very first start. On a
# reinstall, copying the templates then fails on a terse "Permission denied"
# that says nothing about what to do -- and the reset procedure documented in
# the README becomes unusable.
#
# We hand ownership back to the current user, through a container since
# they precisely no longer have the rights. No rootful docker, no sudo: the
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
    info "Dossiers appartenant a un autre utilisateur (conteneurs) :$BESOIN_REPRISE"
    if docker run --rm -v "$PWD:/depot" alpine \
        sh -c "chown -R $(id -u):$(id -g)$(printf ' /depot/%s' $BESOIN_REPRISE)" \
        >/dev/null 2>&1; then
        ok "Proprietaire retabli sur$BESOIN_REPRISE"
    else
        err "Impossible de reprendre la main sur :$BESOIN_REPRISE"
        err "Lance manuellement :"
        err "  docker run --rm -v \"\$PWD:/depot\" alpine chown -R $(id -u):$(id -g) /depot"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# docker-compose.yml
# ---------------------------------------------------------------------------
if [[ -f docker-compose.yml ]]; then
    if [[ $FORCE -eq 1 ]]; then
        warn "docker-compose.yml existant — ecrase (--force)"
        cp docker-compose.yml.example docker-compose.yml
    else
        info "docker-compose.yml existant — conserve"
    fi
else
    cp docker-compose.yml.example docker-compose.yml
    ok "docker-compose.yml cree depuis le template"
fi

# ---------------------------------------------------------------------------
# .env with random secrets
# ---------------------------------------------------------------------------
if [[ -f .env ]] && [[ $FORCE -eq 0 ]]; then
    info ".env existant — conserve. Utilise --force pour regenerer."
else
    if [[ -f .env ]]; then
        cp .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
        warn "Backup de l'ancien .env"
    fi
    # Generate 64-character hex secrets
    S1=$(openssl rand -hex 32)
    S2=$(openssl rand -hex 32)
    S3=$(openssl rand -hex 32)

    # Authelia encrypts its session database with STORAGE_ENCRYPTION_KEY. If
    # the database already exists, generating a new one makes it unreadable:
    #   "the configured encryption key does not appear to be valid for this
    #    database"
    # and Authelia refuses to start. So we keep the previous key.
    if [[ -f services/authelia/config/db.sqlite3 ]]; then
        # The || true is essential: on a fresh install .env does not exist
        # yet while the database may already be there. 2>/dev/null hides
        # grep's message but not its exit status; under set -e the
        # assignment fails and the script dies without printing anything.
        EXISTING_KEY=$(grep '^AUTHELIA_STORAGE_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || true)
        if [[ -n ${EXISTING_KEY:-} ]]; then
            S2=$EXISTING_KEY
            warn "Base Authelia existante : cle de chiffrement conservee"
            warn "  (pour repartir de zero : supprimer services/authelia/config/db.sqlite3)"
        fi
    fi
    # PostgreSQL n'applique POSTGRES_PASSWORD qu'a l'initialisation de son
    # volume. If the volume already exists, generating a new one would make
    # the database
    # inaccessible ("password authentication failed for user orthanc") : on
    # therefore keeps the one from the previous .env.
    PG_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    if docker volume inspect orthanc_postgres_data >/dev/null 2>&1; then
        # Same trap: the PostgreSQL volume can exist without a .env.
        EXISTING_PG=$(grep '^POSTGRES_PASSWORD=' .env 2>/dev/null | cut -d= -f2- || true)
        if [[ -n ${EXISTING_PG:-} ]]; then
            PG_PASS=$EXISTING_PG
            warn "Volume PostgreSQL existant : mot de passe conserve"
            warn "  (pour repartir de zero : docker compose down -v)"
        fi
    fi
    AUTH_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    ORTHANC_PASS=$(openssl rand -hex 32)
    # Identifiants de l'endpoint d'import programmatique (/api-upload/).
    # Without them, the nginx entrypoint generates no htpasswd file and
    # the endpoint refuses everything: better to ship it usable, protected
    # by a generated password, than disabled or -- worse -- open to all.
    # Interface language. Derived from the system's when a matching
    # translation exists: without that the panel shows up in
    # English on a French-speaking machine, with nothing to say where that
    # choix ni comment en changer.
    LANGUE_SYSTEME=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
    case "$LANGUE_SYSTEME" in
        fr*|FR*) LANGUAGE_VALUE="fr" ;;
        *)       LANGUAGE_VALUE="en" ;;
    esac

    UPLOAD_USER_VALUE="import-dicom"
    UPLOAD_PASS_VALUE=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)

    # Default PUBLIC_URL: full local URL, compose port included.
    # The host name (pacs.localhost) must contain a dot, otherwise Authelia
    # refuses the cookie domain (RFC 6265).
    sed \
        -e "s|^AUTHELIA_SESSION_SECRET=.*|AUTHELIA_SESSION_SECRET=$S1|" \
        -e "s|^AUTHELIA_STORAGE_ENCRYPTION_KEY=.*|AUTHELIA_STORAGE_ENCRYPTION_KEY=$S2|" \
        -e "s|^AUTHELIA_JWT_SECRET=.*|AUTHELIA_JWT_SECRET=$S3|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG_PASS|" \
        -e "s|^AUTH_PASSWORD=.*|AUTH_PASSWORD=$AUTH_PASS|" \
        -e "s|^PUBLIC_URL=.*|PUBLIC_URL=https://pacs.localhost:30443|" \
        -e "s|^UPLOAD_USER=.*|UPLOAD_USER=${UPLOAD_USER_VALUE}|" \
        -e "s|^UPLOAD_PASSWORD=.*|UPLOAD_PASSWORD=${UPLOAD_PASS_VALUE}|" \
        -e "s|^PUID=.*|PUID=${PUID}|" \
        -e "s|^PGID=.*|PGID=${PGID}|" \
        .env.example > .env

    # Orthanc account dedicated to auth-service (POST /tools/reset). Not in
    # .env.example, being specific to the admin panel.
    {
        echo ""
        echo "# Compte Orthanc utilise par auth-service pour recharger la config"
        echo "ORTHANC_ADMIN_USER=svc-auth"
        echo "ORTHANC_ADMIN_PASS=$ORTHANC_PASS"
    } >> .env

    ok ".env genere avec 6 secrets aleatoires (Authelia x3 + Postgres + Orthanc + import DICOM)"
    ok "Interface en ${LANGUAGE_VALUE} (d'apres la langue du systeme ; modifiable depuis le panel)"
fi

# ---------------------------------------------------------------------------
# Directories written to by the admin panel
# ---------------------------------------------------------------------------
# Create them HERE and nowhere else: when a bind-mounted directory does not
# exist on the host, Docker creates it itself, and it then belongs to root.
# The containers run under PUID/PGID (see .env) and fail to write into it,
# with a "Permission denied" that no longer resembles its cause.
for dossier in data/admin-backups data/app-settings; do
    if [[ ! -d "$dossier" ]]; then
        mkdir -p "$dossier"
        ok "$dossier/ cree"
    fi
done

# Interface language. It lives with the application settings rather than in
# .env: it is a display preference, changeable from the panel without
# recreating a single container.
if [[ ! -f data/app-settings/settings.json ]]; then
    printf '{\n  "langue": "%s"\n}\n' "${LANGUAGE_VALUE:-en}" \
        > data/app-settings/settings.json
    ok "langue de l'interface : ${LANGUAGE_VALUE:-en}"
fi

# ---------------------------------------------------------------------------
# Configs Authelia + Orthanc
# ---------------------------------------------------------------------------
copy_if_missing() {
    local src=$1
    local dest=$2
    mkdir -p "$(dirname "$dest")"
    if [[ -f $dest ]] && [[ $FORCE -eq 0 ]]; then
        info "$dest existant — conserve"
    else
        cp "$src" "$dest"
        ok "$dest copie depuis $src"
    fi
}

copy_if_missing "authelia-configuration.yml.example" "services/authelia/config/configuration.yml"
copy_if_missing "authelia-users.yml.example"         "services/authelia/config/users_database.yml"
copy_if_missing "orthanc.json.example"               "services/orthanc/config/orthanc.json"

# ---------------------------------------------------------------------------
# Substituting ${VAR} in the Authelia configuration
# ---------------------------------------------------------------------------
# Authelia does NOT perform shell expansion in its YAML: the
# ${AUTHELIA_DOMAIN} and ${REDIS_PORT:-6379} of the template stay literal and
# crash the
# demarrage ("option 'domain' is not a valid cookie domain", "cannot parse
# value as 'int'"). We substitute them here, once, at copy time.
AUTHELIA_CFG="services/authelia/config/configuration.yml"
if grep -q '\${' "$AUTHELIA_CFG" 2>/dev/null; then
    # shellcheck disable=SC1091
    PUBLIC_URL_VALUE=$(grep '^PUBLIC_URL=' .env | cut -d= -f2-)
    # Host name alone, without scheme or port: that is what Authelia's
    # cookie domain expects (a cookie never carries a port).
    DOMAIN_VALUE=$(echo "$PUBLIC_URL_VALUE" | sed -E 's#^https?://##; s#:[0-9]+$##; s#/.*$##')
    sed -i \
        -e "s|\${AUTHELIA_DOMAIN}|${DOMAIN_VALUE}|g" \
        -e "s|\${PUBLIC_URL}|${PUBLIC_URL_VALUE}|g" \
        -e "s|\${REDIS_HOST:-redis}|redis|g" \
        -e "s|\${REDIS_PORT:-6379}|6379|g" \
        -e "s|\${REDIS_DB:-0}|0|g" \
        "$AUTHELIA_CFG"
    ok "configuration.yml : domaine ${DOMAIN_VALUE}, URL publique ${PUBLIC_URL_VALUE}"
fi

# ---------------------------------------------------------------------------
# Authorization plugin password in orthanc.json
# ---------------------------------------------------------------------------
# The plugin authenticates against auth-service with Basic auth, using the
# values from the Authorization section. They must match AUTH_USERNAME and
# AUTH_PASSWORD in .env, failing which /user/get-profile answers 401 and
# Orthanc refuses every request (403) with no explicit message.
#
# The ORTHANC__AUTHORIZATION__WEB_SERVICE_* variables will not do:
# Orthanc does not apply them to this section, so the file's value is what
# is used. Hence the substitution at copy time.
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
    ok "orthanc.json : identifiants du plugin Authorization synchronises"
fi

# ---------------------------------------------------------------------------
# Valid argon2id hash in users_database.yml
# ---------------------------------------------------------------------------
# The template ships EXAMPLE_HASH_REPLACE_THIS, which is not an argon2 hash
# parsable : Authelia refuse de demarrer ("argon2 decode error"). On genere
# a real hash from a random password that is never displayed nor kept.
#
# This bootstrap account exists only because Authelia also refuses to start
# on a database without users ("users: non zero value required"). It is
# disabled and group-less; finalising the wizard removes it once the
# vrai administrateur cree.
USERS_DB="services/authelia/config/users_database.yml"
if grep -q 'EXAMPLE_HASH_REPLACE_THIS' "$USERS_DB" 2>/dev/null; then
    info "Generation d'un hash argon2id (via l'image Authelia)…"
    THROWAWAY=$(openssl rand -base64 32)
    REAL_HASH=$(docker run --rm authelia/authelia:4.39.5 \
        authelia crypto hash generate argon2 --password "$THROWAWAY" 2>/dev/null \
        | sed 's/^Digest: //')
    if [[ -n $REAL_HASH ]]; then
        remplacer_dans "$USERS_DB" \
            '$argon2id$v=19$m=65536,t=3,p=4$EXAMPLE_HASH_REPLACE_THIS' \
            "$REAL_HASH"
        ok "users_database.yml : hash argon2id valide (compte d'amorcage inactif)"
    else
        warn "Generation du hash echouee — Authelia refusera de demarrer."
        warn "Lance manuellement :"
        warn "  docker run --rm authelia/authelia:4.39.5 authelia crypto hash generate argon2 --password 'x'"
    fi
fi

# ---------------------------------------------------------------------------
# Recap
# ---------------------------------------------------------------------------
G=$'\033[32m'; C=$'\033[36m'; R=$'\033[0m'
cat <<EOF

${G}════════════════════════════════════════════${R}
${G} Bootstrap complet${R}
${G}════════════════════════════════════════════${R}

Etapes suivantes :

  1. ${C}Reviser .env${R} si besoin (domaine, langue, TZ)

  2. ${C}Demarrer la stack${R} :
       docker compose up -d

  3. ${C}Setup wizard${R} — creation du premier admin :
       https://localhost:30443/console/setup
       (cert self-signed : accepter l'avertissement du navigateur)

  4. ${C}Apres le wizard${R} :
       https://localhost:30443/          Orthanc Explorer
       https://localhost:30443/console/          Panel d'administration

Repartir de zero :
  docker compose down -v
  rm -rf .env docker-compose.yml data/admin-backups data/app-settings \\
         services/authelia/config/{configuration.yml,users_database.yml} \\
         services/orthanc/config/orthanc.json
  ./bootstrap.sh

EOF
