FROM nginx:1.28.0-alpine

# Install gettext for envsubst and openssl for certificate generation
RUN apk add --no-cache gettext openssl

# Create directories for configuration templates
RUN mkdir -p /etc/nginx/templates /etc/nginx/conf.d.templates

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Nginx configuration templates are NOT baked into the image anymore — they are
# bind-mounted at runtime from the host to keep the config in git (the source of
# truth) and avoid a full image rebuild for every tweak. See docker-compose.yml
# for the required volume mounts:
#   ./services/nginx/nginx.ssl.conf -> /etc/nginx/templates/nginx.conf.template
#   ./services/nginx/conf.d         -> /etc/nginx/conf.d.templates
# The entrypoint reads from these mount points and runs envsubst -> /etc/nginx/.

# Copy static assets (logo + error pages — rarely change, fine to bake in)
COPY logo/ /usr/share/nginx/logo/
COPY error_pages/ /usr/share/nginx/error_pages/

# Set the entrypoint
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]