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
    PG_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    AUTH_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)

    sed \
        -e "s|^AUTHELIA_SESSION_SECRET=.*|AUTHELIA_SESSION_SECRET=$S1|" \
        -e "s|^AUTHELIA_STORAGE_ENCRYPTION_KEY=.*|AUTHELIA_STORAGE_ENCRYPTION_KEY=$S2|" \
        -e "s|^AUTHELIA_JWT_SECRET=.*|AUTHELIA_JWT_SECRET=$S3|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG_PASS|" \
        -e "s|^AUTH_PASSWORD=.*|AUTH_PASSWORD=$AUTH_PASS|" \
        -e "s|^DOMAIN=.*|DOMAIN=localhost|" \
        .env.example > .env
    ok ".env genere avec 4 secrets aleatoires (Authelia x3 + Postgres + Auth)"
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
# Recap
# ---------------------------------------------------------------------------
cat <<EOF

$(printf "\033[32m════════════════════════════════════════════\033[0m")
$(printf "\033[32m Bootstrap complet\033[0m")
$(printf "\033[32m════════════════════════════════════════════\033[0m")

Etapes suivantes :

  1. \033[36mReviser .env\033[0m si besoin (domaine, langue, TZ)

  2. \033[36mDemarrer la stack\033[0m :
       docker compose up -d

  3. \033[36mAcceder\033[0m :
       http://localhost:30080   (HTTP redirige vers HTTPS)
       https://localhost:30443  (cert self-signed, accepte l'avertissement)

  4. \033[36mSetup wizard\033[0m au premier boot :
       https://localhost:30443/auth/setup

Rollback : \`docker compose down\` + supprimer .env pour repartir de zero.

EOF
