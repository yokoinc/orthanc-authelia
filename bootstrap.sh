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

# Remplace une chaine par une autre dans un fichier, sans expression
# reguliere. sed ne convient pas ici : les valeurs substituees sont des mots de
# passe et des empreintes argon2id, qui contiennent des $ et des / -- soit les
# delimiteurs et les references arriere de sed. L'expansion ${var//motif/valeur}
# de bash traite les deux comme du texte brut.
#
# Evite aussi une dependance a Python, absent de Git Bash sous Windows : le
# script n'exige plus que bash, docker et openssl, tous trois presents avec
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
# Identite des conteneurs qui ecrivent dans le depot
# ---------------------------------------------------------------------------
# Authelia et auth-service ecrivent dans ./services/*/config. Sans identite
# imposee ils tournent en root et s'approprient ces dossiers, rendant toute
# reinstallation impossible sans reprendre les droits a la main. On leur donne
# celle de l'utilisateur courant : les fichiers crees lui appartiennent, et le
# probleme ne se pose plus du tout.
PUID=$(id -u)
PGID=$(id -g)

# ---------------------------------------------------------------------------
# Proprietaire des dossiers de configuration
# ---------------------------------------------------------------------------
# Authelia et Orthanc tournent en root dans leurs conteneurs et s'approprient
# les dossiers qu'ils montent des le premier demarrage. A la reinstallation,
# la copie des templates echoue alors sur un "Permission denied" laconique,
# sans indiquer quoi faire -- et la procedure de remise a zero documentee dans
# le README devient inapplicable.
#
# On rend la main a l'utilisateur courant, via un conteneur puisque lui-meme
# n'a justement plus les droits. Sans docker en root ni sudo : c'est le
# demon docker qui fait le travail.
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

    # Authelia chiffre sa base de sessions avec STORAGE_ENCRYPTION_KEY. Si la
    # base existe deja, en generer une nouvelle la rend illisible :
    #   "the configured encryption key does not appear to be valid for this
    #    database"
    # et Authelia refuse de demarrer. On conserve donc la cle precedente.
    if [[ -f services/authelia/config/db.sqlite3 ]]; then
        # || true indispensable : sur une installation neuve le .env
        # n'existe pas encore alors que la base, elle, peut etre la.
        # 2>/dev/null masque le message de grep mais pas son code de
        # retour ; sous set -e l'affectation echoue et le script meurt
        # sans rien afficher.
        EXISTING_KEY=$(grep '^AUTHELIA_STORAGE_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || true)
        if [[ -n ${EXISTING_KEY:-} ]]; then
            S2=$EXISTING_KEY
            warn "Base Authelia existante : cle de chiffrement conservee"
            warn "  (pour repartir de zero : supprimer services/authelia/config/db.sqlite3)"
        fi
    fi
    # PostgreSQL n'applique POSTGRES_PASSWORD qu'a l'initialisation de son
    # volume. Si le volume existe deja, en generer un nouveau rendrait la base
    # inaccessible ("password authentication failed for user orthanc") : on
    # conserve alors celui du .env precedent.
    PG_PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)
    if docker volume inspect orthanc_postgres_data >/dev/null 2>&1; then
        # Meme piege : le volume PostgreSQL peut exister sans .env.
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
    # Sans eux, l'entrypoint nginx ne genere pas de fichier htpasswd et
    # l'endpoint refuse tout : il vaut mieux le livrer utilisable, protege par
    # un mot de passe genere, que desactive ou -- pire -- ouvert a tous.
    # Langue de l'interface. Elle est deduite de celle du systeme quand une
    # traduction correspondante existe : sans cela le panel s'affiche en
    # anglais sur un poste francophone, sans que rien n'indique d'ou vient ce
    # choix ni comment en changer.
    LANGUE_SYSTEME=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
    case "$LANGUE_SYSTEME" in
        fr*|FR*) LANGUAGE_VALUE="fr" ;;
        *)       LANGUAGE_VALUE="en" ;;
    esac

    UPLOAD_USER_VALUE="import-dicom"
    UPLOAD_PASS_VALUE=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)

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
        -e "s|^LANGUAGE=.*|LANGUAGE=${LANGUAGE_VALUE}|" \
        -e "s|^UPLOAD_USER=.*|UPLOAD_USER=${UPLOAD_USER_VALUE}|" \
        -e "s|^UPLOAD_PASSWORD=.*|UPLOAD_PASSWORD=${UPLOAD_PASS_VALUE}|" \
        -e "s|^PUID=.*|PUID=${PUID}|" \
        -e "s|^PGID=.*|PGID=${PGID}|" \
        .env.example > .env

    # Compte Orthanc dedie a auth-service (POST /tools/reset). Pas dans
    # .env.example car specifique au panel admin.
    {
        echo ""
        echo "# Compte Orthanc utilise par auth-service pour recharger la config"
        echo "ORTHANC_ADMIN_USER=svc-auth"
        echo "ORTHANC_ADMIN_PASS=$ORTHANC_PASS"
    } >> .env

    ok ".env genere avec 6 secrets aleatoires (Authelia x3 + Postgres + Orthanc + import DICOM)"
    ok "Interface en ${LANGUAGE_VALUE} (d'apres la langue du systeme ; modifiable par LANGUAGE dans .env)"
fi

# ---------------------------------------------------------------------------
# Dossiers ecrits par le panel d'administration
# ---------------------------------------------------------------------------
# Les creer ICI et pas ailleurs : si un dossier bind-monte n'existe pas sur
# l'hote, Docker le cree lui-meme, et il appartient alors a root. Les
# containers tournent sous PUID/PGID (voir .env) et echouent a y ecrire, avec
# un "Permission denied" qui n'a plus rien a voir avec sa cause.
for dossier in data/admin-backups data/app-settings; do
    if [[ ! -d "$dossier" ]]; then
        mkdir -p "$dossier"
        ok "$dossier/ cree"
    fi
done

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
# Mot de passe du plugin Authorization dans orthanc.json
# ---------------------------------------------------------------------------
# Le plugin s'authentifie aupres d'auth-service en Basic auth avec les valeurs
# de la section Authorization. Elles doivent correspondre a AUTH_USERNAME et
# AUTH_PASSWORD du .env, sans quoi /user/get-profile repond 401 et Orthanc
# refuse toute requete (403) sans message explicite.
#
# Les variables ORTHANC__AUTHORIZATION__WEB_SERVICE_* ne conviennent pas :
# Orthanc ne les applique pas a cette section, la valeur du fichier reste
# utilisee. On substitue donc a la copie.
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
# Hash argon2id valide dans users_database.yml
# ---------------------------------------------------------------------------
# Le template contient EXAMPLE_HASH_REPLACE_THIS qui n'est pas un hash argon2
# parsable : Authelia refuse de demarrer ("argon2 decode error"). On genere
# un hash reel avec un mot de passe aleatoire jamais affiche ni conserve.
#
# Ce compte d'amorcage n'existe que parce qu'Authelia refuse aussi de demarrer
# sur une base sans utilisateur ("users: non zero value required"). Il est
# desactive et sans groupe ; la finalisation du wizard le supprime une fois le
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
