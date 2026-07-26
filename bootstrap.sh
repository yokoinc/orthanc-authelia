#!/usr/bin/env bash
# =============================================================================
# ORTHANC-AUTHELIA — Bootstrap
# =============================================================================
# Prepare une installation fraiche avec des secrets generes aleatoirement
# et les fichiers de config aux bons endroits.
#
# Usage :
#   ./bootstrap.sh          # setup complet, refuse d'ecraser
#   ./bootstrap.sh --force  # ecrase .env et les configs existantes
#
# A la fin, il ne reste qu'a faire :
#   docker compose up -d
# =============================================================================

set -euo pipefail

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
    FORCE=1
fi

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
# .env avec secrets aleatoires
# ---------------------------------------------------------------------------
if [[ -f .env ]] && [[ $FORCE -eq 0 ]]; then
    info ".env existant — conserve. Utilise --force pour regenerer."
else
    if [[ -f .env ]]; then
        cp .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
        warn "Backup de l'ancien .env"
    fi
    # Genere des secrets 64-char hex chacun
    S1=$(openssl rand -hex 32)
    S2=$(openssl rand -hex 32)
    S3=$(openssl rand -hex 32)
    # PostgreSQL n'applique POSTGRES_PASSWORD qu'a l'initialisation de son
    # volume. Si le volume existe deja, en generer un nouveau rendrait la base
    # inaccessible ("password authentication failed for user orthanc") : on
    # conserve alors celui du .env precedent.
    PG_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    if docker volume inspect orthanc_postgres_data >/dev/null 2>&1; then
        EXISTING_PG=$(grep '^POSTGRES_PASSWORD=' .env 2>/dev/null | cut -d= -f2-)
        if [[ -n ${EXISTING_PG:-} ]]; then
            PG_PASS=$EXISTING_PG
            warn "Volume PostgreSQL existant : mot de passe conserve"
            warn "  (pour repartir de zero : docker compose down -v)"
        fi
    fi
    AUTH_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    ORTHANC_PASS=$(openssl rand -hex 32)

    # PUBLIC_URL par defaut : URL locale complete, port du compose inclus.
    # Le nom d'hote (pacs.localhost) doit contenir un point, sinon Authelia
    # refuse le cookie domain (RFC 6265).
    sed \
        -e "s|^AUTHELIA_SESSION_SECRET=.*|AUTHELIA_SESSION_SECRET=$S1|" \
        -e "s|^AUTHELIA_STORAGE_ENCRYPTION_KEY=.*|AUTHELIA_STORAGE_ENCRYPTION_KEY=$S2|" \
        -e "s|^AUTHELIA_JWT_SECRET=.*|AUTHELIA_JWT_SECRET=$S3|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG_PASS|" \
        -e "s|^AUTH_PASSWORD=.*|AUTH_PASSWORD=$AUTH_PASS|" \
        -e "s|^PUBLIC_URL=.*|PUBLIC_URL=https://pacs.localhost:30443|" \
        .env.example > .env

    # Compte Orthanc dedie a auth-service (POST /tools/reset). Pas dans
    # .env.example car specifique au panel admin.
    {
        echo ""
        echo "# Compte Orthanc utilise par auth-service pour recharger la config"
        echo "ORTHANC_ADMIN_USER=svc-auth"
        echo "ORTHANC_ADMIN_PASS=$ORTHANC_PASS"
    } >> .env

    ok ".env genere avec 5 secrets aleatoires (Authelia x3 + Postgres + Orthanc)"
fi

# ---------------------------------------------------------------------------
# Dossier des backups admin (rotatifs, crees avant chaque ecriture de config)
# ---------------------------------------------------------------------------
if [[ ! -d data/admin-backups ]]; then
    mkdir -p data/admin-backups
    ok "data/admin-backups/ cree"
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
# Substitution des ${VAR} dans la config Authelia
# ---------------------------------------------------------------------------
# Authelia ne fait PAS d'expansion shell dans son YAML : les ${AUTHELIA_DOMAIN}
# et ${REDIS_PORT:-6379} du template restent litteraux et font crasher le
# demarrage ("option 'domain' is not a valid cookie domain", "cannot parse
# value as 'int'"). On les substitue ici, une fois, a la copie.
AUTHELIA_CFG="services/authelia/config/configuration.yml"
if grep -q '\${' "$AUTHELIA_CFG" 2>/dev/null; then
    # shellcheck disable=SC1091
    PUBLIC_URL_VALUE=$(grep '^PUBLIC_URL=' .env | cut -d= -f2-)
    # Nom d'hote seul, sans schema ni port : c'est ce qu'attend le cookie
    # domain d'Authelia (un cookie ne porte jamais de port).
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
# Hash argon2id valide dans users_database.yml
# ---------------------------------------------------------------------------
# Le template contient EXAMPLE_HASH_REPLACE_THIS qui n'est pas un hash argon2
# parsable : Authelia refuse de demarrer ("argon2 decode error"). On genere
# un hash reel avec un mot de passe aleatoire jamais affiche — ces comptes
# de demo sont donc inutilisables, et le setup wizard cree le vrai admin.
USERS_DB="services/authelia/config/users_database.yml"
if grep -q 'EXAMPLE_HASH_REPLACE_THIS' "$USERS_DB" 2>/dev/null; then
    info "Generation d'un hash argon2id (via l'image Authelia)…"
    THROWAWAY=$(openssl rand -base64 32)
    REAL_HASH=$(docker run --rm authelia/authelia:4.39.5 \
        authelia crypto hash generate argon2 --password "$THROWAWAY" 2>/dev/null \
        | sed 's/^Digest: //')
    if [[ -n $REAL_HASH ]]; then
        # Python plutot que sed : le hash contient des $ et / qui cassent
        # les regex sed (back-references, delimiteurs).
        REAL_HASH="$REAL_HASH" python3 - "$USERS_DB" <<'PYEOF'
import os, sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
content = content.replace(
    "$argon2id$v=19$m=65536,t=3,p=4$EXAMPLE_HASH_REPLACE_THIS",
    os.environ["REAL_HASH"],
)
with open(path, "w") as f:
    f.write(content)
PYEOF
        ok "users_database.yml : hash argon2id valide (comptes demo inutilisables)"
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
       https://localhost:30443/auth/ui/setup
       (cert self-signed : accepter l'avertissement du navigateur)

  4. ${C}Apres le wizard${R} :
       https://localhost:30443/          Orthanc Explorer
       https://localhost:30443/auth/ui/admin   Hub d'administration

Repartir de zero :
  docker compose down -v
  rm -rf .env docker-compose.yml data/admin-backups \\
         services/authelia/config/{configuration.yml,users_database.yml} \\
         services/orthanc/config/orthanc.json
  ./bootstrap.sh

EOF
