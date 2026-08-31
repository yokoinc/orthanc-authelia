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
ECHECS=0

if [ -z "$DOMAINE" ]; then
    echo "   DOMAIN introuvable dans .env -- verification des routes ignoree."
    ECHECS=$((ECHECS + 1))
else
    echo "   --- routes (domaine : $DOMAINE) ---"
    # Chaque route porte le code qu'on ATTEND d'elle, et on compare.
    #
    # Le script se contentait d'afficher les codes puis la ligne « attendu :
    # ... », sans jamais confronter les deux, et sortait toujours en succes. Un
    # /auth/ en 502 s'affichait a cote de « attendu 200 » et le deploiement
    # passait pour reussi -- il fallait lire soi-meme, a chaque fois.
    for paire in "/auth/:200" "/api/state:200" "/ui/app/:302" "/ohif/:302"; do
        route=${paire%:*}
        attendu=${paire##*:}
        code=$(curl -sk -o /dev/null -w '%{http_code}' -H "Host: $DOMAINE" \
               "https://localhost:30443$route" 2>/dev/null || echo '000')
        if [ "$code" = "$attendu" ]; then
            printf '   %-14s %s\n' "$route" "$code"
        else
            printf '   %-14s %s   <-- ATTENDU %s\n' "$route" "$code" "$attendu"
            ECHECS=$((ECHECS + 1))
        fi
    done
fi

echo "   --- secrets ---"
# Authelia ne fait AUCUNE interpolation dans son YAML : sa configuration porte
# litteralement `secret: ${AUTHELIA_SESSION_SECRET}`, et ce sont les variables
# d'environnement AUTHELIA_* qui l'ecrasent. Si l'une disparait de .env,
# Authelia ne proteste pas -- il prend la chaine « ${AUTHELIA_SESSION_SECRET} »
# telle quelle comme secret de session. Or cette chaine est publiee dans le
# depot : toutes les sessions deviendraient forgeables, sans le moindre message.
MANQUANTS=0
for v in AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_ENCRYPTION_KEY AUTHELIA_JWT_SECRET AUTH_PASSWORD; do
    val=$(grep -E "^${v}=" .env 2>/dev/null | cut -d= -f2- | tr -d '')
    if [ -z "$val" ]; then
        echo "   $v : ABSENT de .env"
        MANQUANTS=$((MANQUANTS + 1))
    elif [ "${val#*\$\{}" != "$val" ]; then
        echo "   $v : contient un placeholder non substitue -- $val"
        MANQUANTS=$((MANQUANTS + 1))
    elif [ ${#val} -lt 16 ]; then
        echo "   $v : suspicieusement court (${#val} caracteres)"
        MANQUANTS=$((MANQUANTS + 1))
    fi
done
if [ "$MANQUANTS" -eq 0 ]; then
    echo "   4 secrets presents et substitues"
else
    ECHECS=$((ECHECS + MANQUANTS))
fi

echo "   --- erreurs nginx sur la derniere minute ---"
# Le motif ne cherchait que « Connection refused ». Or la panne rencontree le
# 2026-08-29 -- Authelia refusant de demarrer -- faisait ecrire a nginx
# « host not found in upstream », que ce filtre ne voyait pas : il affichait 0
# et annoncait des upstreams bien resolus pendant que la pile etait a terre.
MOTIFS='Connection refused|host not found in upstream|no live upstreams|upstream timed out|\[emerg\]|\[alert\]'
ERREURS=$(docker logs --since 60s orthanc-nginx 2>&1 | grep -cE "$MOTIFS" || true)
echo "   $ERREURS"
if [ "$ERREURS" -gt 0 ]; then
    echo "   ^ upstream injoignable ou configuration refusee -- extrait :"
    docker logs --since 60s orthanc-nginx 2>&1 | grep -E "$MOTIFS" | tail -3 | sed 's/^/     /'
    ECHECS=$((ECHECS + 1))
else
    echo "   (0 = aucun upstream injoignable, aucune erreur de configuration)"
fi

echo
if [ "$ECHECS" -eq 0 ]; then
    echo "== Deploiement verifie : tout est conforme =="
    exit 0
fi
echo "== ATTENTION : $ECHECS verification(s) en echec, voir ci-dessus =="
echo "   La pile tourne peut-etre malgre tout, mais elle ne repond pas comme"
echo "   attendu. Ne pas considerer ce deploiement comme reussi."
exit 1
