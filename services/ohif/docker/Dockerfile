# OHIF Viewer - Builder
#
# Clones the OHIF repository at a pinned tag and packages it behind nginx under
# /ohif/.
#
# Since 3.13 upstream has changed tooling: "engines" requires node >= 24 and
# pnpm >= 11, bun and yarn are no longer supported. The upstream Dockerfile
# uses COPY --parents (1.7-labs syntax, hence BuildKit) to cache the
# installation. Here the clone is kept inside the image, which needs no syntax
# extension -- the NAS daemon has no buildx.


# Stage 1: Build the application
FROM node:24.15.0-slim as builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The repository's .npmrc sets manage-package-manager-versions=false: it really
# is this pnpm that will be used, not the one corepack would fetch.
RUN npm install -g pnpm@11


# Clone OHIF Viewer repository
#
# The clone replaces the build context, so upstream's .dockerignore does not
# apply: what it excludes is removed by hand. platform/docs is the heavyweight
# -- it is a workspace member declared by pnpm-workspace.yaml (`platform/*`),
# so all of Docusaurus would be installed for a documentation site the image
# has no use for. `pnpm run build` boils down to `pnpm --filter @ohif/app run
# build:viewer`: none of that is read.
WORKDIR /usr/src
ARG OHIF_VERSION=v3.13.4
RUN git clone --depth 1 --branch ${OHIF_VERSION} https://github.com/OHIF/Viewers.git app \
    && cd app \
    && rm -rf platform/docs .circleci .github .netlify testdata .git

# In-house fix, applied to the sources before the build.
#
# A study carrying a structured report (CT Dose Record) opened the viewer on an
# error banner, while the images displayed. The study panel requested a
# thumbnail from an instance without pixels. The details, and what to do on a
# version upgrade, are in the script.
#
# It fails if the pattern has disappeared from the upstream sources: the build
# then stops, rather than producing an image in which the fix silently went
# missing.
COPY patch-sr-thumbnail.py /usr/src/patch-sr-thumbnail.py
COPY patch-prefetch-nonimage.py /usr/src/patch-prefetch-nonimage.py
RUN cd /usr/src/app \
    && python3 /usr/src/patch-sr-thumbnail.py \
    && python3 /usr/src/patch-prefetch-nonimage.py


# Install dependencies
#
# Before NODE_ENV=production, otherwise the devDependencies -- hence the build
# chain itself (babel, rsbuild, the webpack loaders) -- would be omitted. This
# monorepo does not support --prod: the build lives in the devDependencies.
#
# Cypress is an end-to-end testing devDependency and pnpm-workspace.yaml allows
# its postinstall (`allowBuilds`), which downloads and unpacks ~200 MB of
# binary. None of that is needed to build the viewer.
#
# --no-frozen-lockfile is now mandatory: a workspace member the lockfile knows
# about has just been removed, pnpm must reconcile.
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
# The 3.13 build exceeds node's default heap on a 4-core machine.
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

# dicom-microscopy-viewer loads from the server root and not from PUBLIC_URL: a
# second copy at / is therefore needed, as upstream does.
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
