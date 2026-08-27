#!/bin/sh
# =============================================================================
# Applique un changement a la pile, sans rien avoir a retenir.
# =============================================================================
# Raison d'etre : sur cette installation, trois pannes distinctes ont eu la
# meme cause -- un changement ecrit mais jamais mis en service, ou mis en
# service a moitie.
#
#   1. Authelia tournait depuis 5 jours sur une configuration vieille de 3
#      jours : elle ne relit configuration.yml qu'au demarrage. Resultat :
#      tout repondait 401, page de connexion comprise, pendant 3 jours.
#
#   2. nginx.ssl.conf est un TEMPLATE, rendu au demarrage du conteneur.
#      L'editer ne change rien tant que nginx n'a pas redemarre.
#
#   3. nginx resout ses upstreams UNE SEULE FOIS, au demarrage. Recreer un
#      conteneur lui donne une nouvelle adresse ; nginx continue de taper sur
#      l'ancienne. Le 2026-08-25, authelia et auth-service ont echange les
#      leurs et nginx a envoye l'authentification vers le panneau
#      d'administration. Plus de menu, plus de session, aucune erreur visible
#      cote utilisateur.
#
# Ce script encapsule la seule sequence correcte. Utilise-le au lieu
# d'appeler docker-compose a la main.
#
# Usage :
#   scripts/apply.sh                 recree ce qui a change, puis rafraichit nginx
#   scripts/apply.sh authelia        idem, limite a un service
#   scripts/apply.sh ohif orthanc    idem, plusieurs services
#
# --no-build est toujours passe : auth-service porte `pull_policy: build` et
# serait sinon reconstruit depuis les sources locales, remplacant l'image en
# service par une version jamais testee. Pour reconstruire volontairement,
# c'est `docker build` explicite, pas ce script.
# =============================================================================
set -eu
PATH=/usr/local/bin:$PATH
cd "$(dirname "$0")/.."

echo "== Application des changements =="
if [ $# -gt 0 ]; then
    echo "   services vises : $*"
    docker-compose up -d --no-deps --no-build "$@"
else
    echo "   tous les services"
    docker-compose up -d --no-build
fi

# ---------------------------------------------------------------------------
# Montage de .env : detection du decrochage
# ---------------------------------------------------------------------------
# .env est monte DANS auth-service en montage de FICHIER (et non de dossier :
# monter la racine donnerait a un service expose au web l'acces en ecriture a
# docker-compose.yml et aux scripts). Un montage de fichier suit l'INODE, pas
# le chemin.
#
# Consequence : tout outil qui ecrit par remplacement atomique -- `sed -i`, la
# plupart des editeurs -- cree un nouveau fichier et le renomme par-dessus.
# L'inode change, le conteneur reste accroche a l'ancien, devenu orphelin. Il
# lit alors indefiniment une version figee, et ses propres ecritures partent
# dans le vide EN CROYANT REUSSIR.
#
# Constate le 2026-08-27 : apres la rotation des secrets (faite au `sed -i`),
# le panneau lisait encore les anciennes valeurs, celles qui avaient fuite.
# Rien ne le signalait.
#
# On ne peut pas interdire `sed -i` a tout le monde. On peut le detecter.
if [ -f .env ] && docker ps --format '{{.Names}}' | grep -q '^orthanc-auth-service$'; then
    INODE_HOTE=$(stat -c %i .env 2>/dev/null || echo "?")
    INODE_CONTENEUR=$(docker exec orthanc-auth-service stat -c %i /host/env/.env 2>/dev/null || echo "?")
    if [ "$INODE_HOTE" != "?" ] && [ "$INODE_CONTENEUR" != "?" ] \
       && [ "$INODE_HOTE" != "$INODE_CONTENEUR" ]; then
        echo "== .env decroche du conteneur (inode $INODE_CONTENEUR contre $INODE_HOTE) =="
        echo "   Le panneau lisait un fichier fantome. Recreation d'auth-service."
        docker-compose up -d --force-recreate --no-deps --no-build auth-service >/dev/null
        sleep 4
    fi
fi

# Toujours, sans condition. Un redemarrage de nginx coute deux secondes ;
# oublier de le faire coute une panne d'authentification silencieuse.
echo "== Rafraichissement des adresses vues par nginx =="
docker restart orthanc-nginx >/dev/null
sleep 6

echo "== Verification =="
docker ps --filter name=orthanc- --format '   {{.Names}} | {{.Status}}'

# Le domaine se lit dans .env, jamais en dur. nginx reecrit Host vers DOMAIN et
# Authelia refuse tout ce qui ne correspond pas a son domaine de cookie : tester
# avec un domaine perime renvoie des 403 qui font croire a une panne, ou pire,
# des 200 rassurants sur la mauvaise cible. Le panneau sait changer le domaine
# (onglet reseau, il couvre .env et les onze occurrences de configuration.yml),
# donc cette valeur bouge sans prevenir.
DOMAINE=$(grep -E '^DOMAIN=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r')
if [ -z "$DOMAINE" ]; then
    echo "   DOMAIN introuvable dans .env -- verification des routes ignoree."
else
    echo "   --- routes (domaine : $DOMAINE) ---"
    for r in /auth/ /api/state /ui/app/ /ohif/; do
        code=$(curl -sk -o /dev/null -w '%{http_code}' -H "Host: $DOMAINE" \
               "https://localhost:30443$r" 2>/dev/null || echo '???')
        printf '   %-14s %s\n' "$r" "$code"
    done
    echo "   attendu : /auth/ 200, /api/state 200, /ui/app/ 302, /ohif/ 302"
fi
echo "   --- erreurs nginx sur la derniere minute ---"
docker logs --since 60s orthanc-nginx 2>&1 | grep -c 'Connection refused' || true
echo "   (0 = les upstreams sont bien resolus)"
