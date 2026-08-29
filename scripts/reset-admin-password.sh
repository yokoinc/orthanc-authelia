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
#   porte de service dans l'interface, volontairement : ce serait une porte
#   pour n'importe qui. Ce script est cette porte, et elle exige un acces SSH.
#
# USAGE (sur le NAS, depuis la racine de la pile)
#   ./scripts/reset-admin-password.sh <adresse@du.compte>
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

# Le binaire d'Authelia produit le hash, et il demande LUI-MEME le mot de passe.
#
# Deliberement pas de --password : l'argument serait visible dans la liste des
# processus du NAS pendant l'appel, et repris dans les journaux du demon Docker.
# Authelia le lit sur le terminal (d'ou -it) et le fait saisir deux fois.
# --config lui fait reprendre les parametres argon2 de CETTE installation :
# aucun risque de produire un hash qu'il refuserait ensuite.
echo "Authelia va demander le nouveau mot de passe de ${COMPTE}."
echo "Douze caracteres minimum, comme dans le panneau."
HASH=$(docker exec -it "$CONTENEUR" authelia crypto hash generate argon2 \
        --config /config/configuration.yml \
        | tr -d '\r' | sed -n 's/^Digest: //p')

if [ -z "$HASH" ]; then
    echo "le hachage a echoue -- le conteneur ${CONTENEUR} tourne-t-il ?" >&2
    exit 1
fi

# Sauvegarde avant d'ecrire.
cp "$USERS_YML" "${USERS_YML}.bak.$(date +%Y%m%d-%H%M%S)"

# Remplace la ligne `password:` de CE compte, et d'aucun autre : on n'agit
# qu'entre l'entree du compte et la suivante.
awk -v compte="  ${COMPTE}:" -v hash="$HASH" '
    $0 == compte { dans = 1; print; next }
    dans && /^  [^ ]/ { dans = 0 }
    dans && /^    password:/ { print "    password: " hash; remplace = 1; next }
    { print }
    END { if (!remplace) exit 3 }
' "$USERS_YML" > "${USERS_YML}.tmp" || {
    echo "aucune ligne 'password:' sous ${COMPTE} -- rien ecrit." >&2
    rm -f "${USERS_YML}.tmp"
    exit 1
}

# `cat >` et non `mv` : le fichier est bind-monte dans le conteneur, et un mv
# remplacerait l'inode. Le montage suivrait l'ancien : Authelia continuerait de
# lire le fichier d'avant, sans rien signaler.
cat "${USERS_YML}.tmp" > "$USERS_YML"
rm -f "${USERS_YML}.tmp"

echo "Mot de passe de ${COMPTE} remplace."
echo "Authelia surveille ce fichier (watch: true) : effectif sous une seconde."
