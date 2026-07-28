#!/usr/bin/env bash
# =============================================================================
# ORTHANC-AUTHELIA — Test de bout en bout
# =============================================================================
# Rejoue une installation complete depuis zero et verifie que la pile repond :
# bootstrap, demarrage, wizard, creation de l'administrateur, connexion, puis
# acces a Orthanc, a DICOMweb et au panel.
#
# La pile de test est ISOLEE : projet Docker distinct, ports distincts,
# volumes et reseau distincts, et une copie du depot dans un dossier
# temporaire. L'installation de developpement n'est jamais touchee, le test
# peut donc etre relance autant de fois que voulu.
#
# Usage :
#   ./scripts/e2e-test.sh              # test complet puis nettoyage
#   ./scripts/e2e-test.sh --keep       # laisse la pile debout pour inspection
#
# Code de sortie 0 si tout passe, 1 sinon.
# =============================================================================

set -uo pipefail

PORT_HTTP=31080
PORT_HTTPS=31443
PROJET=orthanc-e2e
URL="https://pacs.localhost:${PORT_HTTPS}"
ADMIN_USER=e2e.admin
ADMIN_PASS=mot-de-passe-e2e-123456
GARDER=0
[[ "${1:-}" == "--keep" ]] && GARDER=1

DEPOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TRAVAIL=$(mktemp -d /tmp/orthanc-e2e.XXXXXX)

ROUGE=$'\033[31m'; VERT=$'\033[32m'; JAUNE=$'\033[33m'; CYAN=$'\033[36m'; RAZ=$'\033[0m'
ECHECS=0
etape()  { printf "\n${CYAN}▶ %s${RAZ}\n" "$*"; }
ok()     { printf "  ${VERT}✓${RAZ} %s\n" "$*"; }
echec()  { printf "  ${ROUGE}✗${RAZ} %s\n" "$*"; ECHECS=$((ECHECS + 1)); }
info()   { printf "  ${JAUNE}·${RAZ} %s\n" "$*"; }

compose() { docker compose -p "$PROJET" "$@"; }

nettoyer() {
    local code=$?
    if [[ $GARDER -eq 1 ]]; then
        printf "\n${JAUNE}Pile laissee debout${RAZ} : %s\n" "$URL"
        printf "Pour la supprimer :\n  cd %s && docker compose -p %s down -v && rm -rf %s\n" \
            "$TRAVAIL" "$PROJET" "$TRAVAIL"
        return $code
    fi
    etape "Nettoyage"
    (cd "$TRAVAIL" && compose down -v >/dev/null 2>&1) && ok "pile supprimee"
    # Les conteneurs ecrivent en root dans le dossier monte (base Authelia,
    # configuration generee, verrous) : un rm lance par l'utilisateur bute
    # dessus. On repasse par un conteneur, qui a les droits.
    if ! rm -rf "$TRAVAIL" 2>/dev/null; then
        docker run --rm -v /tmp:/tmp-hote alpine \
            rm -rf "/tmp-hote/$(basename "$TRAVAIL")" >/dev/null 2>&1
    fi
    if [[ -d "$TRAVAIL" ]]; then
        echec "dossier temporaire non supprime : $TRAVAIL"
    else
        ok "dossier temporaire supprime"
    fi
    return $code
}
trap nettoyer EXIT

# --- Copie du depot --------------------------------------------------------
# git archive plutot que cp : seuls les fichiers VERSIONNES sont copies, donc
# le test part exactement de ce qu'obtient quelqu'un qui clone. Un fichier
# oublie dans .gitignore mais indispensable se verra ici, et pas autrement.
etape "Copie du depot (fichiers versionnes uniquement)"
if ! git -C "$DEPOT" archive HEAD | tar -x -C "$TRAVAIL"; then
    echec "impossible d'extraire le depot"
    exit 1
fi
ok "$(find "$TRAVAIL" -type f | wc -l) fichiers extraits dans $TRAVAIL"

cd "$TRAVAIL"

# --- Bootstrap -------------------------------------------------------------
etape "bootstrap.sh"
if ./bootstrap.sh >/tmp/e2e-bootstrap.log 2>&1; then
    ok "configuration generee"
else
    echec "bootstrap a echoue (voir /tmp/e2e-bootstrap.log)"
    tail -15 /tmp/e2e-bootstrap.log
    exit 1
fi

# --- Isolation -------------------------------------------------------------
# bootstrap fixe l'URL publique sur le port 30443, occupe par l'installation
# de developpement. On la deplace, ainsi que tout ce qui porte un nom fixe :
# conteneurs, volumes et reseau entreraient sinon en collision.
etape "Isolation de la pile de test"
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
    ok "ports ${PORT_HTTP}/${PORT_HTTPS}, projet ${PROJET}"
else
    echec "compose invalide apres isolation"
    compose config 2>&1 | tail -5
    exit 1
fi

# --- Demarrage -------------------------------------------------------------
etape "Demarrage de la pile"
if compose up -d >/tmp/e2e-up.log 2>&1; then
    ok "conteneurs lances"
else
    echec "demarrage impossible (voir /tmp/e2e-up.log)"
    tail -15 /tmp/e2e-up.log
    exit 1
fi

info "attente de la page de connexion (120 s max)"
pret=0
for _ in $(seq 1 60); do
    if [[ "$(curl -ks -o /dev/null -m 5 -w '%{http_code}' "${URL}/auth/")" == "200" ]]; then
        pret=1; break
    fi
    sleep 2
done
if [[ $pret -eq 1 ]]; then
    ok "pile joignable sur ${URL}"
else
    echec "la pile ne repond pas apres 120 s"
    compose ps
    exit 1
fi

# --- Wizard ----------------------------------------------------------------
etape "Wizard de premiere installation"
BISCUITS=$(mktemp)
CSRF=jeton-e2e

code=$(curl -ks -o /tmp/e2e-setup.json -m 20 -w '%{http_code}' \
    -X POST "${URL}/console/api/setup/create-admin" \
    -H 'Content-Type: application/json' \
    -H "X-CSRF-Token: ${CSRF}" -b "orthanc_admin_csrf=${CSRF}" \
    -d "{\"username\":\"${ADMIN_USER}\",\"displayname\":\"Admin E2E\",\"email\":\"e2e@exemple.fr\",\"password\":\"${ADMIN_PASS}\",\"groups\":[\"admin\"]}")
[[ "$code" == "200" ]] && ok "administrateur cree" || { echec "creation admin : HTTP $code $(cat /tmp/e2e-setup.json)"; }

code=$(curl -ks -o /tmp/e2e-final.json -m 20 -w '%{http_code}' \
    -X POST "${URL}/console/api/setup/finalize" \
    -H "X-CSRF-Token: ${CSRF}" -b "orthanc_admin_csrf=${CSRF}")
if [[ "$code" == "200" ]]; then
    ok "installation finalisee"
    # Le compte d'amorcage doit avoir disparu : c'est tout l'objet du
    # nettoyage ajoute a la finalisation.
    if grep -q 'bootstrap@localhost' services/authelia/config/users_database.yml 2>/dev/null; then
        echec "le compte d'amorcage survit a la finalisation"
    else
        ok "compte d'amorcage supprime"
    fi
else
    echec "finalisation : HTTP $code $(cat /tmp/e2e-final.json)"
fi

# --- Connexion -------------------------------------------------------------
etape "Connexion"
curl -ks -o /dev/null -m 20 -c "$BISCUITS" "${URL}/auth/"
reponse=$(curl -ks -m 20 -b "$BISCUITS" -c "$BISCUITS" \
    -X POST "${URL}/api/firstfactor" \
    -H 'Content-Type: application/json' \
    -H "X-Forwarded-Proto: https" -H "X-Forwarded-Host: pacs.localhost:${PORT_HTTPS}" \
    -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASS}\",\"keepMeLoggedIn\":true}")
if grep -q '"status":"OK"' <<<"$reponse"; then
    ok "session ouverte"
else
    echec "connexion refusee : ${reponse:0:120}"
fi

# --- Acces authentifie -----------------------------------------------------
etape "Acces authentifie"

# Attendre qu'Orthanc reponde avant de verifier quoi que ce soit. La page de
# connexion, seule condition d'attente jusqu'ici, ne prouve que la
# disponibilite de nginx et d'Authelia : sur une installation neuve Orthanc
# doit encore creer son schema PostgreSQL et charger ses plugins, ce qui
# prend nettement plus longtemps. Sans cette attente le test rendait des 502
# et signalait une regression inexistante.
info "attente d'Orthanc (90 s max)"
orthanc_pret=0
for _ in $(seq 1 45); do
    if [[ "$(curl -ks -o /dev/null -m 5 -b "$BISCUITS" -w '%{http_code}' "${URL}/system")" == "200" ]]; then
        orthanc_pret=1; break
    fi
    sleep 2
done
if [[ $orthanc_pret -eq 1 ]]; then
    ok "Orthanc pret"
else
    echec "Orthanc ne repond pas apres 90 s"
    (cd "$TRAVAIL" && compose logs --tail 15 orthanc 2>&1 | tail -15)
fi
verifier() {
    local chemin=$1 attendu=$2
    local code
    code=$(curl -ks -o /dev/null -m 20 -b "$BISCUITS" -w '%{http_code}' "${URL}${chemin}")
    if [[ "$code" == "$attendu" ]]; then
        ok "$(printf '%-24s %s' "$chemin" "$code")"
    else
        echec "$(printf '%-24s %s (attendu %s)' "$chemin" "$code" "$attendu")"
    fi
}
verifier /ui/app/            200
verifier /studies            200
verifier /system             200
verifier /dicom-web/studies  200
verifier /console/           200
verifier /ohif/app-config.js 200

# Le profil renvoye a Orthanc decide des droits : un groupe non reconnu fait
# silencieusement retomber sur un profil en lecture seule.
profil=$(curl -ks -m 20 -b "$BISCUITS" "${URL}/ui/api/configuration" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin)["Profile"]; print(p["name"], len(p["permissions"]))' 2>/dev/null)
if [[ "$profil" == "Administrator 10" ]]; then
    ok "profil Orthanc : Administrator, 10 droits"
else
    echec "profil Orthanc inattendu : ${profil:-illisible}"
fi

# --- Bilan -----------------------------------------------------------------
etape "Bilan"
if [[ $ECHECS -eq 0 ]]; then
    printf "  ${VERT}Installation complete validee.${RAZ}\n"
    exit 0
fi
printf "  ${ROUGE}%d verification(s) en echec.${RAZ}\n" "$ECHECS"
exit 1
