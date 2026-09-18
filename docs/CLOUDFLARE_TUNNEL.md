# Cloudflare Tunnel

Publishes the PACS on the Internet **without opening any port on the router**.
The `cloudflared` container dials out to Cloudflare; visitors reach
`https://pacs.example.org`, where Cloudflare serves a real certificate and
renews it by itself.

```
browser --HTTPS (Cloudflare certificate)--> Cloudflare
        --tunnel, encrypted, opened from the inside--> cloudflared (container)
        --HTTPS, self-signed, Docker network only--> nginx --> Authelia, Orthanc, OHIF
```

The last hop uses nginx's self-signed certificate without verifying it. That is
deliberate and safe: it never leaves the Docker network, there is nobody in
between to impersonate nginx.

## 1. Create the tunnel

Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** →
**Create a tunnel** → type **Cloudflared** → give it a name → environment
**Docker**. Cloudflare shows a command ending in `--token eyJ…`: copy the string
after `--token`. Nothing to run from that page.

## 2. Give the token to the installation

**Fresh install**: `./bootstrap.sh` asks for it, after the public address. Answer
the address with the tunnel's hostname, without a port:
`https://pacs.example.org`.

**Existing install**: in `.env`,

```ini
CLOUDFLARE_TUNNEL_TOKEN=eyJ...
COMPOSE_PROFILES=tunnel
```

then `docker compose up -d`, and set the public address in the admin panel,
**Network** tab. The panel updates the twelve places the domain lives in.

`COMPOSE_PROFILES=tunnel` is what starts the `orthanc-cloudflared` container.
Without it, the service is ignored.

## 3. Public hostname

Back in the tunnel's page → **Public Hostname** → **Add a public hostname**:

| Field | Value |
|---|---|
| Subdomain / Domain | `pacs` / `example.org` |
| Service | `HTTPS` `nginx:443` |
| Additional settings → TLS → **No TLS Verify** | on |
| Additional settings → HTTP Settings → **HTTP Host Header** | `pacs.example.org` |
| Additional settings → HTTP Settings → **HTTP2 connection** | on |

`nginx:443` works because `cloudflared` sits on the stack's Docker network.
A `cloudflared` running elsewhere on the host (`network_mode: host`, or a
system install) uses `https://localhost:30443` instead, with the same three
settings.

## 4. Check

```bash
docker logs orthanc-cloudflared 2>&1 | grep -E "Registered tunnel connection|ERR"
curl -sI https://pacs.example.org/auth/ | head -1     # HTTP/2 200
```

In the browser, the sign-in page shows and the padlock names a Cloudflare
certificate.

## Things worth knowing

- **Real visitor address.** Every request reaches nginx from the tunnel
  container. nginx takes the visitor's address from `CF-Connecting-IP`, but only
  from the Docker subnet (`set_real_ip_from` in `nginx.ssl.conf`), which is why
  `cloudflared` has a fixed address in that subnet. Without it the rate limits
  would count every visitor as one.
- **Programmatic uploads** (`/api-upload/`, the Windows import tool) are meant
  to go through **Cloudflare Access** with a service token, checked again at
  the origin: see the admin panel, Cloudflare Access tab. Until it is
  configured, those uploads are refused.
- **Upload size.** Cloudflare limits a request to 100 MB on Free and Pro plans:
  the import tool sends one file per request for that reason.
- **The token is a secret.** Whoever holds it can serve their own content under
  your tunnel's hostnames. It lives in `.env`, which is not versioned; to
  revoke it, delete or rotate the tunnel in the dashboard.
- **Ports 30080/30443** stay published on the host for local access. Behind the
  tunnel you can close them on the router side: nothing from outside needs
  them.
