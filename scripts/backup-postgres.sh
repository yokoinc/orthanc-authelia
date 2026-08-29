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
A_GARDER="${BACKUP_KEEP:-7}"

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
libre_ko=$(df -k "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [ "$libre_ko" -lt 31457280 ]; then     # 30 Gio
    echo "ERREUR : moins de 30 Gio libres sur ${BACKUP_DIR}, sauvegarde annulee." >&2
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
