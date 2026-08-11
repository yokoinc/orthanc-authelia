# OHIF viewer, served under /ohif/ behind the reverse proxy.
#
# This image used to be published on a private registry, which stopped anyone
# from starting the project after a clone.
#
# Why rebuild from source rather than start from the official ohif/app image:
# OHIF freezes its base path into the bundle at build time. The official
# image is built for the root and its HTML references /assets/...,
# /manifest.json. Served under /ohif/, it would request those files at the
# domain root and fail to load. Moving the files changes nothing: the paths
# are hard-coded into the HTML and the bundles. Hence a build with
# PUBLIC_URL=/ohif/.

FROM node:20-slim AS builder

# git to fetch the sources, python3 and build-essential for the native
# dependencies compiled during yarn install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
ARG OHIF_VERSION=v3.11.0
RUN git clone --depth 1 --branch ${OHIF_VERSION} https://github.com/OHIF/Viewers.git .

# Base path frozen into the bundle. The trailing slash matters: without it
# the generated URLs glue the file name to the prefix (/ohifassets/...).
ENV PUBLIC_URL=/ohif/
ENV APP_CONFIG=config/default.js
# The build exceeds Node's default memory limit on this project.
ENV NODE_OPTIONS=--max-old-space-size=8192

RUN yarn config set network-timeout 600000 \
    && yarn install --frozen-lockfile
RUN yarn run build

# nginx serves pre-compressed files through gzip_static; we generate them
# here rather than on every start.
COPY docker/compressDist.sh /src/compressDist.sh
RUN sh /src/compressDist.sh


FROM nginx:1.26-alpine

# envsubst, used by the entrypoint to inject PORT and PUBLIC_URL into the
# configuration template.
RUN apk add --no-cache gettext

COPY --from=builder /src/platform/app/dist /usr/share/nginx/html/ohif
COPY docker/default.conf.template /usr/src/default.conf.template
COPY docker/entrypoint.sh /usr/src/entrypoint.sh
RUN chmod +x /usr/src/entrypoint.sh

# PUBLIC_URL acts as a PATH here, not a URL: the entrypoint and the template
# use it to locate app-config.js and the try_files target.
ENV PORT=8080
ENV PUBLIC_URL=/ohif/

EXPOSE 8080
ENTRYPOINT ["/usr/src/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
