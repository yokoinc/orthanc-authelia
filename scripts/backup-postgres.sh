#!/bin/sh
# Sauvegarde de la base PostgreSQL du PACS.
#
# POURQUOI CE SCRIPT EXISTE
#   Verifie sur l'installation le 2026-08-29 : il n'existait AUCUNE sauvegarde
#   de la base. Ni tache HyperBackup (synobackup.conf ne declarait rien), ni
#   instantane Btrfs (@sharesnap vide), ni tache planifiee faisant un pg_dump.
#   La seule copie etait un dump manuel du 11 juillet, pose sur le meme volume
#   que la base -- donc sans effet contre une panne de volume.
#
#   Orthanc range ICI les images ET l'index : 27 Go, 185 patients,
#   127 075 instances. C'est la seule donnee du systeme qui ne se reconstruit
#   pas. Tout le reste -- configuration, comptes, conteneurs -- se readapte en
#   une soiree a partir du depot.
#
# CE QUE FAIT LE SCRIPT
#   Un pg_dump au format « custom » (compresse, restaurable selectivement),
#   verifie apres ecriture, avec rotation. Rien de plus : il ne copie RIEN hors
#   de la machine, et c'est justement ce qui reste a faire.
#
# CE QU'IL NE FAIT PAS -- A LIRE
#   Une sauvegarde qui vit sur le meme volume que la base ne protege que de
#   l'erreur humaine et de la corruption logique. Elle ne protege NI d'une
#   panne du volume, NI d'un rancongiciel, NI d'un vol du NAS. Pour cela il
#   faut une copie AILLEURS -- et c'est le role d'HyperBackup, deja installe,
#   auquel il suffit de designer BACKUP_DIR comme source.
#
# NE JAMAIS MODIFIER CE SCRIPT PENDANT QU'IL TOURNE.
#   Le shell lit un script par decalage d'octets. Le reecrire en cours
#   d'execution decale tout ce qui suit, et l'interpreteur reprend au milieu
#   d'une ligne. Arrive le 2026-08-29 : une modification de la retention a
#   casse l'etape de verification d'une sauvegarde de deux heures. Le dump
#   etait bon -- l'archive s'est relue sans erreur -- mais il est reste en
#   .partiel, faute d'avoir pu etre valide. Editer une copie, puis remplacer.
#
# USAGE
#   ./scripts/backup-postgres.sh
#
#   A planifier dans DSM : Panneau de configuration > Planificateur de taches >
#   Creer > Tache planifiee > Script defini par l'utilisateur. Une fois par
#   nuit suffit.
set -eu

CONTENEUR="${PG_CONTAINER:-postgres-database-15}"
BASE="${PG_DATABASE:-orthanc}"
UTILISATEUR="${PG_USER:-cuffel.gregory}"   # cf. le bloc PostgreSQL d orthanc.json, pas les POSTGRES_* du compose
BACKUP_DIR="${BACKUP_DIR:-/volume2/docker/orthanc-authelia/data/postgres-backups}"
# Trois, pas sept.
#
# Mesure sur cette installation le 2026-08-29 : le dump fait ~28 Go pour une
# base de 27 Go. Le DICOM est deja compresse, pg_dump ne le reduit pas. Sept
# exemplaires demanderaient donc pres de 200 Go -- un tiers de l'espace libre --
# pour une profondeur d'historique qui n'a d'interet que si elle vit AILLEURS.
#
# La copie locale sert a reprendre vite apres une fausse manoeuvre : trois jours
# suffisent. L'historique long est le role d'HyperBackup vers une destination
# externe, qui sait faire de l'incremental et n'occupe pas ce volume.
A_GARDER="${BACKUP_KEEP:-3}"

horodatage=$(date +%Y%m%d-%H%M%S)
cible="${BACKUP_DIR}/orthanc-${horodatage}.dump"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTENEUR"; then
    echo "ERREUR : le conteneur ${CONTENEUR} ne tourne pas." >&2
    exit 1
fi

# Espace : on refuse de commencer s'il reste moins que la taille de la base.
# Un dump interrompu par un disque plein laisse un fichier tronque qui a l'air
# d'une sauvegarde.
# Le seuil est calcule sur la taille reelle de la base, pas fige.
#
# Il valait 30 Gio en dur, choisi sans mesurer. Or la base fait 60 Go de
# donnees logiques (pg_database_size), dont 59 Go de large objects -- c'est la
# qu'Orthanc range les images -- et le dump produit fait 30 Go. Un seuil fixe
# devient faux des que la base grossit, et c'est precisement le jour ou il
# devrait proteger : un dump interrompu par un disque plein laisse un fichier
# tronque qui ressemble a une sauvegarde.
#
# On exige donc de quoi ecrire un dump complet, avec de la marge.
taille_base_o=$(docker exec "$CONTENEUR" psql -U "$UTILISATEUR" -d "$BASE" -tAc                 "SELECT pg_database_size('${BASE}')" 2>/dev/null | tr -d ' ')
if [ -n "$taille_base_o" ] && [ "$taille_base_o" -gt 0 ] 2>/dev/null; then
    requis_ko=$(( taille_base_o / 1024 ))          # marge : la base entiere
else
    requis_ko=31457280                             # 30 Gio, faute de mieux
    echo "AVERTISSEMENT : taille de la base indeterminee, seuil par defaut." >&2
fi
libre_ko=$(df -k "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [ "$libre_ko" -lt "$requis_ko" ]; then
    echo "ERREUR : $(( libre_ko / 1048576 )) Gio libres sur ${BACKUP_DIR}," >&2
    echo "        il en faut au moins $(( requis_ko / 1048576 )). Sauvegarde annulee." >&2
    exit 1
fi

echo "Sauvegarde de ${BASE} vers ${cible}..."

# -Fc : format custom. Compresse, et pg_restore peut en extraire une table
# seule -- ce qu'un dump SQL brut ne permet pas.
# On ecrit d'abord sous .partiel : un fichier incomplet ne doit jamais porter
# le nom d'une sauvegarde valide.
if ! docker exec "$CONTENEUR" pg_dump -U "$UTILISATEUR" -d "$BASE" -Fc \
        > "${cible}.partiel" 2>/tmp/pgdump-erreur.$$; then
    echo "ERREUR : pg_dump a echoue :" >&2
    cat /tmp/pgdump-erreur.$$ >&2
    rm -f "${cible}.partiel" /tmp/pgdump-erreur.$$
    exit 1
fi
rm -f /tmp/pgdump-erreur.$$

# Verification : un dump qu'on n'a pas relu n'est pas une sauvegarde, c'est un
# fichier. pg_restore --list echoue sur une archive tronquee ou corrompue.
if ! docker exec -i "$CONTENEUR" pg_restore --list < "${cible}.partiel" > /dev/null 2>&1; then
    echo "ERREUR : l'archive produite est illisible, elle est jetee." >&2
    rm -f "${cible}.partiel"
    exit 1
fi

mv "${cible}.partiel" "$cible"
chmod 600 "$cible"
taille=$(du -h "$cible" | cut -f1)
echo "Sauvegarde terminee : ${cible} (${taille}), archive relue et valide."

# Rotation.
nb=$(ls -1 "${BACKUP_DIR}"/orthanc-*.dump 2>/dev/null | wc -l)
if [ "$nb" -gt "$A_GARDER" ]; then
    ls -1t "${BACKUP_DIR}"/orthanc-*.dump | tail -n +$((A_GARDER + 1)) | while read -r vieux; do
        echo "Rotation : suppression de $(basename "$vieux")"
        rm -f "$vieux"
    done
fi

echo
echo "RAPPEL : cette copie est sur le MEME volume que la base."
echo "Elle ne protege ni d'une panne de volume, ni d'un rancongiciel, ni d'un vol."
echo "Designez ${BACKUP_DIR} comme source d'une tache HyperBackup vers une"
echo "destination externe -- sans quoi la seule copie des 185 patients reste"
echo "sur le disque qui peut tomber."
