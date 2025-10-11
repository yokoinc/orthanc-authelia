# SSL Configuration Guide

Complete guide for SSL/TLS setup in ORTHANC-AUTHELIA.

## Table of Contents

- [SSL Mode Options](#ssl-mode-options)
- [Choosing the Right SSL Mode](#choosing-the-right-ssl-mode)
- [Configuration](#configuration)
- [Managing Self-Signed Certificates](#managing-self-signed-certificates)
- [Using Custom Certificates](#using-custom-certificates)
- [Reverse Proxy Configuration](#reverse-proxy-configuration)

## SSL Mode Options

The nginx service supports three SSL modes, configured via the `SSL_MODE` variable in your `.env` file:

### 1. Self-Signed Certificates (Default)

**`SSL_MODE=selfsigned`**

- Automatically generated on first start
- 365-day validity period
- Stored in Docker volume `orthanc_nginx_ssl`
- Best for: Development, internal networks, behind Cloudflare Tunnel

### 2. Disabled SSL

**`SSL_MODE=disabled`**

- HTTP only (port 80)
- No certificates generated
- Best for: Behind a reverse proxy that handles SSL (Traefik, Nginx Proxy Manager, Caddy)

### 3. Custom Certificates

**`SSL_MODE=custom`**

- Use your own certificates (Let's Encrypt, commercial CA)
- Must be manually placed in the nginx volume
- Best for: Production environments with valid SSL certificates

## Choosing the Right SSL Mode

### Use `selfsigned` when:
- Developing locally
- Running on an internal network
- Behind Cloudflare Tunnel (handles external SSL)
- Testing the stack before production

### Use `disabled` when:
- Running behind Traefik, Nginx Proxy Manager, or similar reverse proxy
- The reverse proxy handles SSL termination
- You want to avoid double encryption

### Use `custom` when:
- Running in production with valid certificates
- You have Let's Encrypt or commercial SSL certificates
- External clients connect directly (no reverse proxy)

## Configuration

Edit your `.env` file:

```bash
# For self-signed certificates (default)
SSL_MODE=selfsigned

# For no SSL (HTTP only)
SSL_MODE=disabled

# For custom certificates
SSL_MODE=custom
```

After changing `SSL_MODE`, restart the nginx container:
```bash
docker-compose restart nginx
```

## Managing Self-Signed Certificates

### Check certificate expiration

```bash
docker exec orthanc-nginx openssl x509 -in /etc/nginx/ssl/cert.pem -noout -dates
```

### Regenerate certificates

```bash
# Remove the volume to force regeneration
docker-compose down
docker volume rm orthanc_nginx_ssl
docker-compose up -d
```

### Verify certificates are loaded

```bash
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
```

## Using Custom Certificates

### Step 1: Set SSL mode

Edit `.env` file:
```bash
SSL_MODE=custom
```

### Step 2: Copy your certificates

**Method 1: Copy to running container**
```bash
docker cp your-cert.pem orthanc-nginx:/etc/nginx/ssl/cert.pem
docker cp your-key.pem orthanc-nginx:/etc/nginx/ssl/key.pem
docker-compose restart nginx
```

**Method 2: Use volume mount (recommended for Let's Encrypt)**

In `docker-compose.yml`, add to the nginx service:
```yaml
nginx:
  volumes:
    - nginx-ssl:/etc/nginx/ssl
    - /path/to/letsencrypt/live/yourdomain:/etc/nginx/ssl:ro  # Add this line
```

### Step 3: Verify certificates are loaded

```bash
docker exec orthanc-nginx openssl x509 -in /etc/nginx/ssl/cert.pem -noout -subject -issuer -dates
```

## Reverse Proxy Configuration

If using a reverse proxy (recommended for production), set `SSL_MODE=disabled` and let the proxy handle SSL.

### Traefik Example

1. Set `SSL_MODE=disabled` in `.env`

2. Add Traefik labels to nginx service in `docker-compose.yml`:

```yaml
nginx:
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.orthanc.rule=Host(`pacs.yourdomain.com`)"
    - "traefik.http.routers.orthanc.entrypoints=websecure"
    - "traefik.http.routers.orthanc.tls.certresolver=letsencrypt"
    - "traefik.http.services.orthanc.loadbalancer.server.port=80"
```

### Nginx Proxy Manager

1. Set `SSL_MODE=disabled` in `.env`
2. Configure NPM to proxy to `http://your-server:30080`
3. Enable SSL in NPM with Let's Encrypt

### Cloudflare Tunnel

1. Keep `SSL_MODE=selfsigned` (internal encryption)
2. Configure tunnel to connect to `https://localhost:30443`
3. Cloudflare handles external SSL

### Caddy

1. Set `SSL_MODE=disabled` in `.env`

2. Add to your Caddyfile:

```
pacs.yourdomain.com {
    reverse_proxy localhost:30080
}
```

Caddy automatically handles Let's Encrypt certificates.

## Important Notes

- Port 30080 (HTTP) is **always available**, regardless of SSL mode
- Port 30443 (HTTPS) only works when `SSL_MODE` is `selfsigned` or `custom`
- Certificates in `selfsigned` mode are for internal nginx encryption only
- Browser warnings are normal with self-signed certificates
- Self-signed certificates should NOT be used for production with external access
- Always use a reverse proxy with proper SSL for production deployments

## Testing SSL Configuration

### Test HTTP connection
```bash
curl -I http://your-domain:30080
```

### Test HTTPS connection (self-signed)
```bash
curl -Ik https://your-domain:30443
```

### Test HTTPS connection (custom certificates)
```bash
curl -I https://your-domain:30443
```

### Check certificate details
```bash
openssl s_client -connect your-domain:30443 -servername your-domain
```

## Certificate Renewal

### Self-Signed Certificates
Self-signed certificates expire after 365 days. To renew:
```bash
docker-compose down
docker volume rm orthanc_nginx_ssl
docker-compose up -d
```

### Custom Certificates (Let's Encrypt)
If using Let's Encrypt with a volume mount, certificates renew automatically via your reverse proxy or certbot.

### Custom Certificates (Manual)
Replace certificates manually:
```bash
docker cp new-cert.pem orthanc-nginx:/etc/nginx/ssl/cert.pem
docker cp new-key.pem orthanc-nginx:/etc/nginx/ssl/key.pem
docker-compose restart nginx
```
