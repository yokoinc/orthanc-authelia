#!/bin/sh
# Resets an account's password, including the last administrator's.
#
# WHY THIS SCRIPT EXISTS
#   Email reset is disabled: Authelia's notifier is `filesystem`, it writes
#   the link to a file inside the container and still displays "an email
#   has been sent". No email goes out.
#
#   A user's password is changed from the admin panel. But if the
#   ADMINISTRATOR password is lost, nobody can open the panel any more --
#   and there is deliberately no back door in the interface: it would be a
#   door for anyone. This script is that door, and it requires SSH access.
#
# USAGE (on the NAS, from the stack root)
#   ./scripts/reset-admin-password.sh <account@address>
set -eu

USERS_YML="services/authelia/config/users_database.yml"
CONTENEUR="orthanc-authelia"

if [ $# -ne 1 ]; then
    echo "usage: $0 <account@address>" >&2
    exit 2
fi
COMPTE="$1"

if [ ! -f "$USERS_YML" ]; then
    echo "not found: $USERS_YML (run from the stack root)" >&2
    exit 1
fi

if ! grep -q "^  ${COMPTE}:" "$USERS_YML"; then
    echo "no account '${COMPTE}' in $USERS_YML. Existing accounts:" >&2
    grep -oE '^  [^ ]+:' "$USERS_YML" | tr -d ' :' >&2
    exit 1
fi

# Authelia's binary produces the hash, and asks for the password ITSELF.
#
# Deliberately no --password: the argument would be visible in the NAS process
# list during the call, and picked up in the Docker daemon logs. Authelia reads
# it from the terminal (hence -it) and has it typed twice. --config makes it
# reuse THIS installation's argon2 parameters: no risk of producing a hash it
# would later reject.
echo "Authelia will ask for the new password of ${COMPTE}."
echo "Twelve characters minimum, as in the panel."
HASH=$(docker exec -it "$CONTENEUR" authelia crypto hash generate argon2 \
        --config /config/configuration.yml \
        | tr -d '\r' | sed -n 's/^Digest: //p')

if [ -z "$HASH" ]; then
    echo "hashing failed -- is container ${CONTENEUR} running?" >&2
    exit 1
fi

# Backup before writing.
cp "$USERS_YML" "${USERS_YML}.bak.$(date +%Y%m%d-%H%M%S)"

# Replaces the `password:` line of THIS account, and no other: only the lines
# between the account's entry and the next one are touched.
awk -v compte="  ${COMPTE}:" -v hash="$HASH" '
    $0 == compte { dans = 1; print; next }
    dans && /^  [^ ]/ { dans = 0 }
    dans && /^    password:/ { print "    password: " hash; remplace = 1; next }
    { print }
    END { if (!remplace) exit 3 }
' "$USERS_YML" > "${USERS_YML}.tmp" || {
    echo "no 'password:' line under ${COMPTE} -- nothing written." >&2
    rm -f "${USERS_YML}.tmp"
    exit 1
}

# `cat >` and not `mv`: the file is bind-mounted into the container, and an mv
# would replace the inode. The mount would follow the old one: Authelia would
# keep reading the previous file, without reporting anything.
cat "${USERS_YML}.tmp" > "$USERS_YML"
rm -f "${USERS_YML}.tmp"

echo "Password of ${COMPTE} replaced."
echo "Authelia watches this file (watch: true): effective within a second."
