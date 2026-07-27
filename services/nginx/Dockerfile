# Reverse proxy de la pile : termine le TLS, applique l'authentification
# Authelia (auth_request) et repartit vers Orthanc, OHIF et auth-service.
#
# Cette image etait auparavant publiee sur un registre prive, ce qui empechait
# quiconque de demarrer le projet apres un clone. Elle se construit desormais
# depuis le depot (cf. la section `build` du docker-compose).

FROM nginx:1.28-alpine

# openssl : genere le certificat auto-signe quand SSL_MODE=selfsigned.
# gettext : fournit envsubst, dont l'entrypoint se sert pour injecter
#           PUBLIC_URL et DOMAIN dans les templates de configuration.
RUN apk add --no-cache openssl gettext

COPY error_pages/ /usr/share/nginx/error_pages/
COPY logo/ /usr/share/nginx/logo/
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Les templates (nginx.ssl.conf et conf.d/) ne sont volontairement PAS copies
# ici : le docker-compose les bind-monte depuis le depot. La configuration
# nginx se modifie donc par un simple `docker compose restart nginx`, sans
# reconstruire ni republier d'image. Une image qui les embarquerait figerait
# la configuration au moment du build.
#
# Consequence : l'image ne demarre pas sans ces montages. C'est assume, ils
# font partie du docker-compose fourni.

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
