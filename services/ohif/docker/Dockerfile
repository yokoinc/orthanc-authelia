# OHIF Viewer - Builder
# This Dockerfile clones the OHIF Viewer repository and integrates custom translations


# Stage 1: Build the application
FROM node:20-slim as builder

RUN apt-get update && apt-get install -y build-essential python3 git


# Clone OHIF Viewer repository
WORKDIR /usr/src
RUN git clone --depth 1 --branch v3.10.2 https://github.com/OHIF/Viewers.git app

WORKDIR /usr/src/app
RUN npm install -g bun
ENV PATH=/usr/src/app/node_modules/.bin:$PATH


# Install dependencies
RUN bun pm cache rm
RUN bun install


# Build here
# After install it should hopefully be stable until the local directory changes
ENV NODE_ENV=production
ENV QUICK_BUILD=false
ENV GENERATE_SOURCEMAP=false
ARG APP_CONFIG=config/default.js
ARG PUBLIC_URL=/ohif/
ENV PUBLIC_URL=${PUBLIC_URL}

RUN bun run show:config
RUN bun run build


# Copy compression script and run it
COPY compressDist.sh /usr/src/app/compressDist.sh
RUN chmod u+x /usr/src/app/compressDist.sh
RUN cd /usr/src/app && ./compressDist.sh


# Stage 2: Bundle the built application into a Docker container
# which runs Nginx using Alpine Linux
FROM nginxinc/nginx-unprivileged:1.26-alpine as final
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
