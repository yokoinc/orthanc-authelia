# OHIF Viewer - Builder
# Clone le depot OHIF a un tag fige et l'empaquette derriere nginx sous /ohif/.
#
# Depuis 3.13 le depot amont a change d'outillage : "engines" exige node >= 24
# et pnpm >= 11, bun et yarn ne sont plus supportes. Le Dockerfile amont utilise
# COPY --parents (syntaxe 1.7-labs, donc BuildKit) pour mettre en cache
# l'installation ; on garde ici le clone dans l'image, qui n'a besoin d'aucune
# extension de syntaxe -- le demon du NAS n'a pas buildx.


# Stage 1: Build the application
FROM node:24.15.0-slim as builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# .npmrc du depot pose manage-package-manager-versions=false : c'est bien ce
# pnpm-la qui sera utilise, pas celui que corepack irait chercher.
RUN npm install -g pnpm@11


# Clone OHIF Viewer repository
#
# Le clone remplace le contexte de build, donc le .dockerignore amont ne
# s'applique pas : on retire a la main ce qu'il ecarte. platform/docs est le
# poids lourd -- c'est un membre du workspace declare par pnpm-workspace.yaml
# (`platform/*`), donc tout Docusaurus s'installerait pour un site de
# documentation dont l'image n'a que faire. `pnpm run build` se reduit a
# `pnpm --filter @ohif/app run build:viewer` : rien de tout cela n'est lu.
WORKDIR /usr/src
ARG OHIF_VERSION=v3.13.4
RUN git clone --depth 1 --branch ${OHIF_VERSION} https://github.com/OHIF/Viewers.git app \
    && cd app \
    && rm -rf platform/docs .circleci .github .netlify testdata .git

# Correctif maison, applique sur les sources avant le build.
#
# Une etude portant un compte rendu structure (Dose Record de scanner) ouvrait
# le viewer sur un bandeau d'erreur, alors que les images s'affichaient. Le
# panneau d'etudes reclamait une vignette a une instance sans pixels. Le detail
# et la marche a suivre en cas de montee de version sont dans le script.
#
# Il echoue si le motif a disparu des sources amont : le build s'arrete alors,
# plutot que de produire une image ou le correctif serait passe a la trappe.
COPY patch-sr-thumbnail.py /usr/src/patch-sr-thumbnail.py
COPY patch-prefetch-nonimage.py /usr/src/patch-prefetch-nonimage.py
RUN cd /usr/src/app \
    && python3 /usr/src/patch-sr-thumbnail.py \
    && python3 /usr/src/patch-prefetch-nonimage.py


# Install dependencies
# Avant NODE_ENV=production, sans quoi les devDependencies -- donc la chaine de
# build elle-meme (babel, rsbuild, les loaders webpack) -- seraient omises.
# Ce monorepo ne supporte pas --prod : le build vit dans les devDependencies.
#
# Cypress est une devDependency de tests end-to-end et pnpm-workspace.yaml
# autorise son postinstall (`allowBuilds`), qui telecharge et deballe ~200 Mo
# de binaire. Rien de tout cela ne sert a construire le viewer.
#
# --no-frozen-lockfile est desormais obligatoire : on vient de retirer un
# membre du workspace que le lockfile connait, pnpm doit reconcilier.
WORKDIR /usr/src/app
ENV PATH=/usr/src/app/node_modules/.bin:$PATH
ENV CYPRESS_INSTALL_BINARY=0
ENV PUPPETEER_SKIP_DOWNLOAD=1
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

RUN pnpm install --no-frozen-lockfile


# Build here
# After install it should hopefully be stable until the local directory changes
ENV NODE_ENV=production
ENV QUICK_BUILD=false
ENV GENERATE_SOURCEMAP=false
# Le build 3.13 depasse le tas par defaut de node sur une machine a 4 coeurs.
ENV NODE_OPTIONS=--max-old-space-size=4096
ARG APP_CONFIG=config/default.js
ARG PUBLIC_URL=/ohif/
ENV PUBLIC_URL=${PUBLIC_URL}

RUN pnpm run show:config
RUN pnpm run build


# Copy compression script and run it
COPY compressDist.sh /usr/src/app/compressDist.sh
RUN chmod u+x /usr/src/app/compressDist.sh
RUN cd /usr/src/app && ./compressDist.sh


# Stage 2: Bundle the built application into a Docker container
# which runs Nginx using Alpine Linux
FROM nginxinc/nginx-unprivileged:1.27-alpine as final
#RUN apk add --no-cache bash

ARG PUBLIC_URL=/ohif/
ENV PUBLIC_URL=${PUBLIC_URL}
ARG PORT=8080
ENV PORT=${PORT}

RUN rm /etc/nginx/conf.d/default.conf
USER nginx

COPY --chown=nginx:nginx entrypoint.sh /usr/src/entrypoint.sh
COPY --chown=nginx:nginx default.conf.template /usr/src/default.conf.template
RUN chmod 777 /usr/src/entrypoint.sh

COPY --from=builder /usr/src/app/platform/app/dist /usr/share/nginx/html${PUBLIC_URL}

# dicom-microscopy-viewer se charge depuis la racine du serveur et non depuis
# PUBLIC_URL : il faut donc une seconde copie a /, comme le fait l'amont.
COPY --from=builder /usr/src/app/platform/app/dist/dicom-microscopy-viewer /usr/share/nginx/html/dicom-microscopy-viewer


# In entrypoint.sh, app-config.js might be overwritten, so chmod it to be writeable.
# The nginx user cannot chmod it, so change to root.
USER root
RUN chown -R nginx:nginx /usr/share/nginx/html
USER nginx

# Health check - simple port check
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD nc -z localhost ${PORT} || exit 1

ENTRYPOINT ["/usr/src/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
