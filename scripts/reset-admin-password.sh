#!/bin/sh
# Reinitialise le mot de passe d'un compte, y compris le dernier administrateur.
#
# POURQUOI CE SCRIPT EXISTE
#   La reinitialisation par courriel est desactivee : le notifier d'Authelia est
#   `filesystem`, il ecrit le lien dans un fichier a l'interieur du conteneur et
#   affiche quand meme « un e-mail a ete envoye ». Aucun courriel ne part.
#
#   Le mot de passe d'un utilisateur se change depuis le panneau
#   d'administration. Mais si c'est le mot de passe ADMINISTRATEUR qui est
#   perdu, plus personne ne peut ouvrir le panneau -- et il n'existe pas de
#   porte de service dans l'interface, volontairement. Ce script est cette
#   porte, et elle exige un acces SSH au NAS.
#
# USAGE (sur le NAS, dans le dossier de la pile)
#   ./scripts/reset-admin-password.sh <adresse@du.compte>
#
# Le hachage est fait par le binaire d'Authelia lui-meme (argon2id), avec les
# parametres de sa propre configuration : pas de risque de produire un hash
# qu'il refuserait.
set -eu

USERS_YML="services/authelia/config/users_database.yml"
CONTENEUR="orthanc-authelia"

if [ $# -ne 1 ]; then
    echo "usage: $0 <adresse@du.compte>" >&2
    exit 2
fi
COMPTE="$1"

if [ ! -f "$USERS_YML" ]; then
    echo "introuvable : $USERS_YML (lancez depuis la racine de la pile)" >&2
    exit 1
fi

if ! grep -q "^  ${COMPTE}:" "$USERS_YML"; then
    echo "aucun compte '${COMPTE}' dans $USERS_YML. Comptes presents :" >&2
    grep -oE '^  [^ ]+:' "$USERS_YML" | tr -d ' :' >&2
    exit 1
fi

printf 'Nouveau mot de passe pour %s : ' "$COMPTE"
stty -echo 2>/dev/null || true
read -r MDP
stty echo 2>/dev/null || true
echo

if [ ${#MDP} -lt 12 ]; then
    echo "refuse : 12 caracteres minimum (meme regle que le panneau)." >&2
    exit 1
fi

# Le binaire d'Authelia produit le hash. --config lui fait reprendre les
# parametres argon2 de cette installation.
HASH=$(docker exec -i "$CONTENEUR" authelia crypto hash generate argon2 \
        --config /config/configuration.yml --password "$MDP" \
        | sed -n 's/^Digest: //p')

if [ -z "$HASH" ]; then
    echo "le hachage a echoue -- le conteneur $CONTENEUR tourne-t-il ?" >&2
    exit 1
fi

# Sauvegarde avant d'ecrire. Le fichier est monte dans le conteneur : on ecrit
# DANS l'inode existant, un mv detacherait le montage et Authelia continuerait
# de lire l'ancien fichier.
cp "$USERS_YML" "${USERS_YML}.bak.$(date +%Y%m%d-%H%M%S)"

awk -v compte="  ${COMPTE}:" -v hash="$HASH" '
    $0 == compte { dans = 1; print; next }
    dans && /^  [^ ]/ { dans = 0 }
    dans && /^    password:/ { print "    password: " hash; remplace = 1; next }
    { print }
    END { if (!remplace) exit 3 }
' "$USERS_YML" > "${USERS_YML}.tmp" || {
    echo "aucune ligne 'password:' trouvee sous ${COMPTE} -- rien ecrit." >&2
    rm -f "${USERS_YML}.tmp"
    exit 1
}

cat "${USERS_YML}.tmp" > "$USERS_YML"   # ecrit dans l'inode monte
rm -f "${USERS_YML}.tmp"

echo "Mot de passe de ${COMPTE} remplace."
echo "Authelia surveille ce fichier (watch: true) : effectif sous une seconde."
