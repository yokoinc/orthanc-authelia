#!/bin/sh

envsubst '${PORT}:${PUBLIC_URL}' < /usr/src/default.conf.template  > /etc/nginx/conf.d/default.conf

if [ -n "$APP_CONFIG" ]; then
  echo "$APP_CONFIG" > /usr/share/nginx/html${PUBLIC_URL}app-config.js
  echo "Using custom APP_CONFIG environment variable"
else
  echo "Not using custom APP_CONFIG"
fi

if [ -f /usr/share/nginx/html${PUBLIC_URL}app-config.js ]; then
  if [ -s /usr/share/nginx/html${PUBLIC_URL}app-config.js ]; then
    echo "Detected non-empty app-config.js. Ensuring .gz file is updated..."
    rm -f /usr/share/nginx/html${PUBLIC_URL}app-config.js.gz
    gzip /usr/share/nginx/html${PUBLIC_URL}app-config.js
    touch /usr/share/nginx/html${PUBLIC_URL}app-config.js
    echo "Compressed app-config.js to app-config.js.gz"
  else
    echo "app-config.js is empty. Skipping compression."
  fi
else
  echo "No app-config.js file found. Skipping compression."
fi

echo "Starting Nginx to serve the OHIF Viewer on ${PUBLIC_URL}"

exec "$@"
