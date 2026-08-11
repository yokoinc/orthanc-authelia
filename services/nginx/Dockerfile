# The stack's reverse proxy: terminates TLS, applies Authelia
# authentication (auth_request) and dispatches to Orthanc, OHIF and
# auth-service.
#
# This image used to be published on a private registry, which stopped anyone
# from starting the project after a clone. It is now built from the
# repository (see the `build` section of docker-compose).

FROM nginx:1.28-alpine

# openssl: generates the self-signed certificate when SSL_MODE=selfsigned.
# gettext: provides envsubst, which the entrypoint uses to inject PUBLIC_URL
#          and DOMAIN into the configuration templates.
RUN apk add --no-cache openssl gettext

COPY error_pages/ /usr/share/nginx/error_pages/
COPY logo/ /usr/share/nginx/logo/
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# The templates (nginx.ssl.conf and conf.d/) are deliberately NOT copied
# here: docker-compose bind-mounts them from the repository. The nginx
# configuration therefore changes with a plain
# `docker compose restart nginx`, without rebuilding or republishing an
# image. An image embedding them would freeze the configuration at build
# time.
#
# Consequence: the image does not start without those mounts. That is
# assumed -- they are part of the docker-compose we ship.

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
