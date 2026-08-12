# Cloudflare Tunnel setup for ORTHANC-AUTHELIA

This guide explains how to set up a Cloudflare tunnel to expose your
ORTHANC-AUTHELIA instance on the Internet securely.

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Installing cloudflared](#installing-cloudflared)
3. [Cloudflare authentication](#cloudflare-authentication)
4. [Tunnel configuration](#tunnel-configuration)
5. [DNS configuration](#dns-configuration)
6. [HTTPS backend configuration](#https-backend-configuration)
7. [Starting the tunnel](#starting-the-tunnel)
8. [Automating with systemd](#automating-with-systemd)
9. [Monitoring and logs](#monitoring-and-logs)
10. [Troubleshooting](#troubleshooting)

## Prerequisites

- A Cloudflare account with a configured domain
- Docker and Docker Compose installed
- ORTHANC-AUTHELIA configured with HTTPS (port 30443)
- SSL certificates generated (self-signed or Let's Encrypt)

## Installing cloudflared

### On Ubuntu/Debian

```bash
# Download and install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
sudo mv cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared
```

### Checking the installation

```bash
cloudflared --version
```

## Cloudflare authentication

### 1. Logging in to your Cloudflare account

```bash
cloudflared tunnel login
```

This command opens your browser for Cloudflare authentication.

### 2. Selecting the domain

Select the domain you want to use (for example `yokoinc.ovh`).

## Tunnel configuration

### 1. Create a new tunnel

```bash
cloudflared tunnel create orthanc-pacs
```

This command creates a tunnel named `orthanc-pacs` and generates a unique UUID.

### 2. Create the configuration file

Create the file `/etc/cloudflared/config.yml`:

```yaml
# Cloudflare tunnel configuration for ORTHANC-AUTHELIA
tunnel: orthanc-pacs
credentials-file: /etc/cloudflared/orthanc-pacs.json

# Log configuration
log-level: info
log-file: /var/log/cloudflared.log

# HTTPS backend configuration
ingress:
  # Main rule for the PACS domain
  - hostname: pacs.yokoinc.ovh
    service: https://localhost:30443
    # TLS configuration for the backend
    originRequest:
      # Disable TLS verification for self-signed certificates
      noTLSVerify: true
      # Force HTTP/2 for better performance
      http2Origin: true
      # Set the headers for the proxy
      proxyHeaders:
        Host: pacs.yokoinc.ovh
      # Connection timeout
      connectTimeout: 30s
      # Read timeout
      tlsTimeout: 10s
  
  # Default rule (mandatory)
  - service: http_status:404
```

### 3. Create the required directories

```bash
sudo mkdir -p /etc/cloudflared
sudo mkdir -p /var/log
```

### 4. Copy the credentials file

```bash
sudo cp ~/.cloudflared/orthanc-pacs.json /etc/cloudflared/
```

## DNS configuration

### 1. Add the DNS record

```bash
cloudflared tunnel route dns orthanc-pacs pacs.yokoinc.ovh
```

### 2. Check the DNS configuration

In your Cloudflare dashboard, check that the CNAME record was created:
- **Type**: CNAME
- **Name**: pacs
- **Target**: `orthanc-pacs.cfargotunnel.com`
- **Proxy**: ✅ Enabled (orange cloud)

## HTTPS backend configuration

### 1. SSL/TLS settings in Cloudflare

In your Cloudflare dashboard, go to **SSL/TLS** > **Overview**:

- **Encryption mode**: Full (strict) or Full
- **Edge certificate**: Automatic
- **Origin certificate**: Enabled

### 2. Additional settings

Under **SSL/TLS** > **Edge certificates**:

- **Minimum TLS version**: 1.2
- **Automatic TLS verification**: Enabled
- **Origin certificate**: Configured

### 3. Page rules (optional)

Create a page rule for `pacs.yokoinc.ovh/*`:

- **Security level**: Medium
- **Cache mode**: Standard
- **HTTPS rewrite**: Enabled

## Starting the tunnel

### 1. Testing the configuration

```bash
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
```

### 2. Connectivity test

```bash
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://pacs.yokoinc.ovh/
```

### 3. Manual start (for testing)

```bash
sudo cloudflared tunnel --config /etc/cloudflared/config.yml run
```

### 4. Verification

Open your browser and go to `https://pacs.yokoinc.ovh`. You should see the
Authelia login page.

## Automating with systemd

### 1. Create the systemd service

Create the file `/etc/systemd/system/cloudflared.service`:

```ini
[Unit]
Description=Cloudflare Tunnel
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2. Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudflared.service
sudo systemctl start cloudflared.service
```

### 3. Check the status

```bash
sudo systemctl status cloudflared.service
```

## Monitoring and logs

### 1. Live logs

```bash
sudo journalctl -u cloudflared.service -f
```

### 2. Cloudflared logs

```bash
sudo tail -f /var/log/cloudflared.log
```

### 3. Cloudflare metrics

In your Cloudflare dashboard, look at:
- **Analytics** > **Traffic**: traffic statistics
- **Analytics** > **Security**: security events
- **Analytics** > **Performance**: performance metrics

## Troubleshooting

### SSL connection error

```bash
# Check the SSL certificates
openssl s_client -connect localhost:30443 -servername pacs.yokoinc.ovh

# Test with curl
curl -k -H "Host: pacs.yokoinc.ovh" https://localhost:30443/auth/
```

### 502 Bad Gateway

1. Check that ORTHANC-AUTHELIA is running:
   ```bash
   docker compose ps
   curl -k https://localhost:30443/auth/
   ```

2. Check the cloudflared configuration:
   ```bash
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
   ```

### DNS problems

```bash
# Check DNS resolution
nslookup pacs.yokoinc.ovh
dig pacs.yokoinc.ovh

# Check the Cloudflare records
cloudflared tunnel route dns orthanc-pacs pacs.yokoinc.ovh
```

### Authentication error

```bash
# Re-authenticate
cloudflared tunnel login

# List the tunnels
cloudflared tunnel list
```

## Advanced configuration

### 1. Automatic HTTPS redirect

In your nginx configuration, add:

```nginx
server {
    listen 80;
    server_name pacs.yokoinc.ovh;
    return 301 https://$server_name$request_uri;
}
```

### 2. Security headers

Add to your nginx configuration:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Frame-Options DENY always;
add_header X-Content-Type-Options nosniff always;
```

### 3. Rate limiting

In Cloudflare, configure rate-limiting rules:
- **Rule**: `pacs.yokoinc.ovh/auth/*`
- **Limit**: 10 requests per minute per IP
- **Action**: block temporarily

## Security

### 1. Firewall

Configure your firewall to block direct access to port 30443:

```bash
sudo ufw deny 30443
sudo ufw allow from 127.0.0.1 to any port 30443
```

### 2. Two-factor authentication

Make sure two-factor authentication is enabled in Authelia:

```yaml
totp:
  issuer: ORTHANC-AUTHELIA
  period: 30
  skew: 1
```

### 3. Access monitoring

Watch the access logs:

```bash
docker compose logs nginx | grep "GET /auth/"
docker compose logs authelia | grep "Access to"
```

## Maintenance

### 1. Updating cloudflared

```bash
# Download the latest version
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared

# Replace the current version
sudo systemctl stop cloudflared
sudo mv /tmp/cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared
sudo systemctl start cloudflared
```

### 2. Certificate renewal

With self-signed certificates, remember to renew them from time to time. The
nginx entrypoint reissues one when it is missing, expired, or no longer matches
`DOMAIN`, so deleting it and restarting the container is enough. The
certificate lives in the `orthanc_nginx_ssl` volume, not in the repository:

```bash
docker compose exec nginx rm -f /etc/nginx/ssl/cert.pem /etc/nginx/ssl/key.pem
docker compose restart nginx
```

## Support

- **Cloudflare documentation**: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
- **cloudflared on GitHub**: https://github.com/cloudflare/cloudflared
- **System logs**: `/var/log/cloudflared.log`
- **Service status**: `systemctl status cloudflared`

---

*This guide was written for ORTHANC-AUTHELIA v1.0. For the latest updates, see the official documentation.*