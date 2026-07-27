# Visionneuse OHIF, servie sous /ohif/ derriere le reverse proxy.
#
# Cette image etait publiee sur un registre prive, ce qui empechait quiconque
# de demarrer le projet apres un clone.
#
# Pourquoi reconstruire depuis les sources plutot que partir de l'image
# officielle ohif/app : OHIF fige son chemin de base dans le bundle au moment
# du build. L'image officielle est construite pour la racine et son HTML
# reference /assets/..., /manifest.json. Servie sous /ohif/, elle demanderait
# ces fichiers a la racine du domaine et ne chargerait pas. Deplacer les
# fichiers ne change rien : les chemins sont ecrits en dur dans le HTML et les
# bundles. Il faut donc un build avec PUBLIC_URL=/ohif/.

FROM node:20-slim AS builder

# git pour recuperer les sources, python3 et build-essential pour les
# dependances natives compilees pendant yarn install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
ARG OHIF_VERSION=v3.11.0
RUN git clone --depth 1 --branch ${OHIF_VERSION} https://github.com/OHIF/Viewers.git .

# Chemin de base fige dans le bundle. Le slash final compte : sans lui les
# URLs generees collent le nom de fichier au prefixe (/ohifassets/...).
ENV PUBLIC_URL=/ohif/
ENV APP_CONFIG=config/default.js
# Le build depasse la limite memoire par defaut de Node sur ce projet.
ENV NODE_OPTIONS=--max-old-space-size=8192

RUN yarn config set network-timeout 600000 \
    && yarn install --frozen-lockfile
RUN yarn run build

# nginx sert les fichiers pre-compresses via gzip_static ; on les genere ici
# plutot qu'a chaque demarrage.
COPY docker/compressDist.sh /src/compressDist.sh
RUN sh /src/compressDist.sh


FROM nginx:1.26-alpine

# envsubst, utilise par l'entrypoint pour injecter PORT et PUBLIC_URL dans le
# template de configuration.
RUN apk add --no-cache gettext

COPY --from=builder /src/platform/app/dist /usr/share/nginx/html/ohif
COPY docker/default.conf.template /usr/src/default.conf.template
COPY docker/entrypoint.sh /usr/src/entrypoint.sh
RUN chmod +x /usr/src/entrypoint.sh

# PUBLIC_URL sert ici de CHEMIN (et non d'URL) : l'entrypoint et le template
# s'en servent pour situer app-config.js et la cible du try_files.
ENV PORT=8080
ENV PUBLIC_URL=/ohif/

EXPOSE 8080
ENTRYPOINT ["/usr/src/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
