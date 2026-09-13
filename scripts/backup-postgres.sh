#!/bin/sh
# Backup of the PACS PostgreSQL database.
#
# WHY THIS SCRIPT EXISTS
#   Checked on the installation on 2026-08-29: there was NO backup of the
#   database. No HyperBackup task (synobackup.conf declared nothing), no
#   Btrfs snapshot (@sharesnap empty), no scheduled task running pg_dump.
#   The only copy was a manual dump from 11 July, stored on the same volume
#   as the database -- so useless against a volume failure.
#
#   Orthanc stores the images AND the index HERE: 27 GB, 185 patients,
#   127,075 instances. It is the only data in the system that cannot be
#   rebuilt. Everything else -- configuration, accounts, containers -- can
#   be set up again in an evening from the repository.
#
# WHAT THE SCRIPT DOES
#   A pg_dump in "custom" format (compressed, selectively restorable),
#   verified after writing, with rotation. Nothing more: it copies NOTHING
#   off the machine, and that is precisely what remains to be done.
#
# WHAT IT DOES NOT DO -- READ THIS
#   A backup living on the same volume as the database only protects against
#   human error and logical corruption. It protects NEITHER against a volume
#   failure, NOR ransomware, NOR theft of the NAS. For that a copy ELSEWHERE
#   is needed -- and that is the role of HyperBackup, already installed,
#   which only needs BACKUP_DIR designated as a source.
#
# NEVER EDIT THIS SCRIPT WHILE IT IS RUNNING.
#   The shell reads a script by byte offset. Rewriting it during execution
#   shifts everything after it, and the interpreter resumes in the middle of
#   a line. Happened on 2026-08-29: a change to the retention broke the
#   verification step of a two-hour backup. The dump was good -- the archive
#   read back without error -- but it stayed as .partiel, for lack of being
#   validated. Edit a copy, then replace.
#
# USAGE
#   ./scripts/backup-postgres.sh
#
#   To schedule in DSM: Control Panel > Task Scheduler > Create > Scheduled
#   Task > User-defined script. Once a night is enough.
set -eu

# DSM's scheduler runs scripts with a minimal PATH (/usr/bin:/bin), where
# docker -- installed under /usr/local/bin -- cannot be found. Without this
# line, the scheduled task would have failed every night on "docker: command
# not found", with nobody looking. Checked on 2026-09-13, before the very first
# scheduling.
PATH=/usr/local/bin:$PATH

CONTENEUR="${PG_CONTAINER:-postgres-database-15}"
BASE="${PG_DATABASE:-orthanc}"
UTILISATEUR="${PG_USER:-cuffel.gregory}"   # see the PostgreSQL block of orthanc.json, not the compose's POSTGRES_*
BACKUP_DIR="${BACKUP_DIR:-/volume2/docker/orthanc-authelia/data/postgres-backups}"
# Three, not seven.
#
# Measured on this installation on 2026-08-29: the dump is ~28 GB for a 27 GB
# database. DICOM is already compressed, pg_dump does not shrink it. Seven
# copies would therefore need nearly 200 GB -- a third of the free space -- for
# a history depth that is only useful if it lives ELSEWHERE.
#
# The local copy is for recovering quickly after a mistake: three days are
# enough. Long history is HyperBackup's job, towards an external destination,
# which can do incremental backups and does not use this volume.
A_GARDER="${BACKUP_KEEP:-3}"

horodatage=$(date +%Y%m%d-%H%M%S)
cible="${BACKUP_DIR}/orthanc-${horodatage}.dump"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTENEUR"; then
    echo "ERROR: container ${CONTENEUR} is not running." >&2
    exit 1
fi

# Space: refuse to start if less than the size of the database remains. A dump
# interrupted by a full disk leaves a truncated file that looks like a backup.
# The threshold is computed from the actual database size, not fixed.
#
# It used to be a hard-coded 30 GiB, chosen without measuring. Yet the database
# holds 60 GB of logical data (pg_database_size), 59 GB of which are large
# objects -- that is where Orthanc stores the images -- and the resulting dump
# is 30 GB. A fixed threshold becomes wrong as soon as the database grows, and
# that is precisely the day it should protect: a dump interrupted by a full
# disk leaves a truncated file that resembles a backup.
#
# So enough space to write a complete dump is required, with margin.
taille_base_o=$(docker exec "$CONTENEUR" psql -U "$UTILISATEUR" -d "$BASE" -tAc                 "SELECT pg_database_size('${BASE}')" 2>/dev/null | tr -d ' ')
if [ -n "$taille_base_o" ] && [ "$taille_base_o" -gt 0 ] 2>/dev/null; then
    requis_ko=$(( taille_base_o / 1024 ))          # margin: the whole database
else
    requis_ko=31457280                             # 30 GiB, for lack of anything better
    echo "WARNING: database size unknown, using the default threshold." >&2
fi
libre_ko=$(df -k "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [ "$libre_ko" -lt "$requis_ko" ]; then
    echo "ERROR: $(( libre_ko / 1048576 )) GiB free on ${BACKUP_DIR}," >&2
    echo "       at least $(( requis_ko / 1048576 )) are needed. Backup cancelled." >&2
    exit 1
fi

echo "Backing up ${BASE} to ${cible}..."

# -Fc: custom format. Compressed, and pg_restore can extract a single table
# from it -- which a raw SQL dump does not allow. Writing goes first to
# .partiel: an incomplete file must never carry the name of a valid backup.
if ! docker exec "$CONTENEUR" pg_dump -U "$UTILISATEUR" -d "$BASE" -Fc \
        > "${cible}.partiel" 2>/tmp/pgdump-erreur.$$; then
    echo "ERROR: pg_dump failed:" >&2
    cat /tmp/pgdump-erreur.$$ >&2
    rm -f "${cible}.partiel" /tmp/pgdump-erreur.$$
    exit 1
fi
rm -f /tmp/pgdump-erreur.$$

# Verification: a dump that has not been read back is not a backup, it is a
# file. pg_restore --list fails on a truncated or corrupted archive.
if ! docker exec -i "$CONTENEUR" pg_restore --list < "${cible}.partiel" > /dev/null 2>&1; then
    echo "ERROR: the archive produced is unreadable, it has been discarded." >&2
    rm -f "${cible}.partiel"
    exit 1
fi

mv "${cible}.partiel" "$cible"
chmod 600 "$cible"
taille=$(du -h "$cible" | cut -f1)
echo "Backup complete: ${cible} (${taille}), archive read back and valid."

# Rotation.
nb=$(ls -1 "${BACKUP_DIR}"/orthanc-*.dump 2>/dev/null | wc -l)
if [ "$nb" -gt "$A_GARDER" ]; then
    ls -1t "${BACKUP_DIR}"/orthanc-*.dump | tail -n +$((A_GARDER + 1)) | while read -r vieux; do
        echo "Rotation: deleting $(basename "$vieux")"
        rm -f "$vieux"
    done
fi

echo
echo "REMINDER: this copy is on the SAME volume as the database."
echo "It protects neither against a volume failure, nor ransomware, nor theft."
echo "Set ${BACKUP_DIR} as the source of a HyperBackup task to an"
echo "external destination -- otherwise the only copy of the 185 patients stays"
echo "on the disk that can fail."
