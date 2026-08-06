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
# On verifie la presence des droits qui comptent, et non leur nombre : un
# compte fige casse des qu'une permission est ajoutee, sans rien dire d'utile.
# admin-permissions est le plus revelateur -- c'est lui qui gouverne la
# gestion des equipements DICOM, et son absence est passee inapercue
# longtemps parce que 'all' ne le couvre pas.
profil=$(curl -ks -m 20 -b "$BISCUITS" "${URL}/ui/api/configuration" \
    | python3 -c '
import json, sys
p = json.load(sys.stdin)["Profile"]
attendus = {"all", "admin-permissions", "settings", "delete", "upload", "view"}
manquants = attendus - set(p["permissions"])
print(p["name"], "|", ",".join(sorted(manquants)) if manquants else "complet")
' 2>/dev/null)
if [[ "$profil" == "Administrator | complet" ]]; then
    ok "profil Orthanc : Administrator, droits essentiels presents"
else
    echec "profil Orthanc : ${profil:-illisible}"
fi

# --- Fonctions du panel ----------------------------------------------------
# Ces routes ont longtemps existe cote serveur sans aucune interface : rien ne
# les exercait, donc rien n'aurait signale qu'elles cassaient.
etape "Fonctions du panel d'administration"

# whoami pose le cookie CSRF, comme le fait le SPA a son chargement. Les
# ecritures sont refusees sans lui.
curl -ks -o /dev/null -m 20 -b "$BISCUITS" -c "$BISCUITS" "${URL}/console/api/admin/whoami"
JETON=$(awk '/orthanc_admin_csrf/ {print $NF}' "$BISCUITS" | tail -1)
if [[ -n "$JETON" ]]; then
    ok "jeton CSRF obtenu"
else
    echec "aucun jeton CSRF pose par whoami"
fi

verifier_panel() {
    local methode=$1 chemin=$2 corps=$3 libelle=$4 attendu=${5:-200}
    local code
    if [[ -n "$corps" ]]; then
        code=$(curl -ks -o /tmp/e2e-panel.json -m 20 -b "$BISCUITS" -w '%{http_code}' \
            -X "$methode" "${URL}${chemin}" \
            -H 'Content-Type: application/json' -H "X-CSRF-Token: ${JETON}" -d "$corps")
    else
        code=$(curl -ks -o /tmp/e2e-panel.json -m 20 -b "$BISCUITS" -w '%{http_code}' \
            -X "$methode" "${URL}${chemin}" -H "X-CSRF-Token: ${JETON}")
    fi
    if [[ "$code" == "$attendu" ]]; then
        ok "$(printf '%-38s %s' "$libelle" "$code")"
    else
        echec "$(printf '%-38s %s (attendu %s) : %s' "$libelle" "$code" "$attendu" "$(head -c 80 /tmp/e2e-panel.json)")"
    fi
}

verifier_panel GET /console/api/admin/backups '' 'liste des sauvegardes'
verifier_panel GET /console/api/admin/network '' 'adresse publique (lecture)'
verifier_panel GET /console/api/admin/audit '' 'journal d activite'
verifier_panel GET /console/api/admin/modalities '' 'equipements DICOM'

verifier_panel PATCH "/console/api/admin/users/${ADMIN_USER}" \
    '{"displayname":"Admin E2E renomme"}' 'modification de compte'

# Garde-fou : l'administrateur du wizard est le seul compte. Se retirer du
# groupe admin, ou se desactiver, laisserait la pile sans personne pour
# l'administrer. Le serveur doit refuser avec un 400 explicite -- et non une
# erreur 500, ce qui etait le cas avant que les violations d'invariant soient
# converties.
verifier_panel PATCH "/console/api/admin/users/${ADMIN_USER}" \
    '{"groups":["doctor"]}' 'refus de perdre le dernier admin' 400
verifier_panel PATCH "/console/api/admin/users/${ADMIN_USER}" \
    '{"disabled":true}' 'refus de desactiver le dernier admin' 400

# L'administrateur cree par le wizard est le seul compte : changer son mot de
# passe n'affecte rien d'autre que la session de ce test, supprimee ensuite.
verifier_panel PATCH "/console/api/admin/users/${ADMIN_USER}/password" \
    '{"new_password":"nouveau-mot-de-passe-e2e-123"}' 'changement de mot de passe'

# --- Chaine DICOM ----------------------------------------------------------
# Le reste du test verifie que les URLs repondent. Ici on verifie que le
# produit fait son travail : qu'un examen entre, s'indexe en conservant ses
# metadonnees, et ressort par DICOMweb -- ce dont dependent les visionneuses.
etape "Chaine DICOM"

# Un DICOM valide, fabrique a la volee : pas de fichier binaire a versionner,
# et les identifiants sont uniques a chaque execution.
cat > /tmp/e2e-gen-dicom.py <<'PYDICOM'
import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

taille = 64
y, x = np.ogrid[:taille, :taille]
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
ds.StudyDescription = 'Examen de validation E2E'
ds.SeriesDescription = 'Serie synthetique'
ds.AccessionNumber = 'ACC-E2E-1'
ds.Modality = 'OT'
ds.StudyID = '1'
ds.SeriesNumber = 1
ds.InstanceNumber = 1
ds.SamplesPerPixel = 1
ds.PhotometricInterpretation = 'MONOCHROME2'
ds.Rows = taille
ds.Columns = taille
ds.BitsAllocated = 16
ds.BitsStored = 16
ds.HighBit = 15
ds.PixelRepresentation = 0
ds.PixelData = img.tobytes()
ds.save_as('/sortie/e2e.dcm', write_like_original=False)
print('ok')
PYDICOM

if docker run --rm -v /tmp:/sortie -v /tmp/e2e-gen-dicom.py:/gen.py:ro \
    python:3.11-slim sh -c 'pip install -q pydicom numpy && python /gen.py' \
    >/dev/null 2>&1 && [[ -f /tmp/e2e.dcm ]]; then
    ok "DICOM de test fabrique ($(stat -c%s /tmp/e2e.dcm) octets)"
else
    echec "impossible de fabriquer le DICOM de test"
fi

if [[ -f /tmp/e2e.dcm ]]; then
    # L'endpoint d'import ne demande pas de session Authelia -- c'est la voie
    # des scripts -- mais une authentification HTTP Basic propre, dont les
    # identifiants sont generes par bootstrap.sh.
    UP_USER=$(grep '^UPLOAD_USER=' .env | cut -d= -f2-)
    UP_PASS=$(grep '^UPLOAD_PASSWORD=' .env | cut -d= -f2-)

    # Un envoi sans identifiants doit etre refuse : cet endpoint accepte des
    # donnees medicales sans session, le laisser ouvert permettrait a
    # quiconque sur le reseau d'alimenter la base.
    refus=$(curl -ks -o /dev/null -m 20 -w '%{http_code}' \
        -X POST "${URL}/api-upload/instances" \
        -H 'Content-Type: application/dicom' --data-binary @/tmp/e2e.dcm)
    if [[ "$refus" == "401" ]]; then
        ok "envoi sans identifiants refuse (401)"
    else
        echec "envoi sans identifiants : $refus au lieu de 401"
    fi

    code=$(curl -ks -o /tmp/e2e-upload.json -m 60 -w '%{http_code}' \
        -u "${UP_USER}:${UP_PASS}" \
        -X POST "${URL}/api-upload/instances" \
        -H 'Content-Type: application/dicom' --data-binary @/tmp/e2e.dcm)
    if [[ "$code" == "200" ]]; then
        ok "envoi accepte par /api-upload/instances"
    else
        echec "envoi refuse : HTTP $code $(head -c 90 /tmp/e2e-upload.json)"
    fi

    # Indexation : l'examen doit apparaitre dans le compte d'Orthanc.
    etudes=$(curl -ks -m 20 -b "$BISCUITS" "${URL}/statistics" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["CountStudies"])' 2>/dev/null)
    if [[ "$etudes" == "1" ]]; then
        ok "examen indexe (1 etude)"
    else
        echec "indexation : ${etudes:-illisible} etude(s) au lieu de 1"
    fi

    # Metadonnees : un examen indexe mais dont les tags sont perdus ne sert a
    # rien -- la recherche par patient ou par date ne le retrouverait pas.
    lu=$(curl -ks -m 20 -b "$BISCUITS" "${URL}/studies?expand" \
        | python3 -c 'import json,sys; e=json.load(sys.stdin)[0]; print(e["PatientMainDicomTags"]["PatientID"], e["MainDicomTags"]["AccessionNumber"])' 2>/dev/null)
    if [[ "$lu" == "E2E-0001 ACC-E2E-1" ]]; then
        ok "metadonnees conservees (patient et numero d'acces)"
    else
        echec "metadonnees alterees : '${lu:-illisible}'"
    fi

    # DICOMweb : c'est par la que les visionneuses recuperent les images.
    nb=$(curl -ks -m 20 -b "$BISCUITS" "${URL}/dicom-web/studies" \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null)
    if [[ "$nb" == "1" ]]; then
        ok "examen expose par DICOMweb"
    else
        echec "DICOMweb renvoie ${nb:-illisible} examen(s) au lieu de 1"
    fi

    # Le DICOM est ecrit par le conteneur, donc en root : le supprimer
    # demande de repasser par un conteneur, comme pour le dossier de travail.
    rm -f /tmp/e2e-gen-dicom.py
    docker run --rm -v /tmp:/tmp-hote alpine rm -f /tmp-hote/e2e.dcm >/dev/null 2>&1
fi

# --- Proprietaire des fichiers ---------------------------------------------
# Authelia et auth-service ecrivent dans le depot. S'ils tournent en root, ils
# s'approprient les dossiers et toute reinstallation ulterieure echoue sur un
# "Permission denied" -- la procedure de remise a zero du README devient
# inapplicable. Le compose leur impose l'identite de l'utilisateur ; on verifie
# qu'elle est bien appliquee, sinon rien ne le signalerait avant la prochaine
# reinstallation.
etape "Proprietaire des fichiers ecrits"
etrangers=$(find services/authelia/config services/orthanc/config data \
    ! -user "$(id -u)" 2>/dev/null | head -5)
if [[ -z "$etrangers" ]]; then
    ok "tout appartient a l'utilisateur courant"
else
    echec "fichiers appartenant a un autre utilisateur :"
    printf '      %s\n' $etrangers
fi

# --- Bilan -----------------------------------------------------------------
etape "Bilan"
if [[ $ECHECS -eq 0 ]]; then
    printf "  ${VERT}Installation complete validee.${RAZ}\n"
    exit 0
fi
printf "  ${ROUGE}%d verification(s) en echec.${RAZ}\n" "$ECHECS"
exit 1
