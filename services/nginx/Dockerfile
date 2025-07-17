FROM nginx:1.28.0-alpine

# Install gettext for envsubst
RUN apk add --no-cache gettext

# Create directories for configuration templates
RUN mkdir -p /etc/nginx/templates /etc/nginx/conf.d.templates

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Copy nginx configuration templates
COPY nginx.ssl.conf /etc/nginx/templates/nginx.conf.template
COPY conf.d/ /etc/nginx/conf.d.templates/

# Copy static assets
COPY logo/ /usr/share/nginx/logo/
COPY error_pages/ /usr/share/nginx/error_pages/

# Set the entrypoint
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]