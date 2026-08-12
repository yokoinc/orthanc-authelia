# ORTHANC-AUTHELIA

Medical PACS solution based on Orthanc with Authelia authentication (SSO, 2FA, RBAC), OHIF viewer, and custom token management system.

**Platform Support**: x86-64 Linux only

## Overview

ORTHANC-AUTHELIA is a complete Picture Archiving and Communication System (PACS) for small to medium healthcare structures. It combines:
- **Orthanc PACS** - Industry-standard DICOM server with PostgreSQL storage
- **Authelia** - Modern authentication with SSO and 2FA
- **OHIF Viewer v3.11.0** - Professional medical imaging viewer
- **Custom Auth-Service** - Token-based external sharing with OE2-themed management UI
- **Multiple Viewers** - OHIF, Stone Web Viewer, and VolView for different use cases

## Stack & Versions

Component versions as defined in `docker-compose.yml` (keep this table in sync when bumping images):

| Component | Image | Version |
|-----------|-------|---------|
| Orthanc PACS | `orthancteam/orthanc` | `26.6.1` |
| Authelia | `authelia/authelia` | `4.39.20` |
| Redis | `redis` | `8.0-alpine` |
| OHIF Viewer | `registry.yokoinc.ovh/orthanc-ohif` | `3.11.0` |
| Nginx | `registry.yokoinc.ovh/orthanc-nginx` | `1.1.0` |
| Auth-Service | `registry.yokoinc.ovh/orthanc-auth-service` | `1.0.15` |
| Socket proxy | `tecnativa/docker-socket-proxy` | `0.3.0` |

> **PostgreSQL** is not part of this stack — Orthanc connects to an **external** PostgreSQL instance over the `database` network (see [Database Setup Guide](docs/DATABASE_SETUP.md)).

## Why Authelia over KeyCloak?

- **Lightweight**: Minimal resource usage vs KeyCloak's heavy footprint
- **Simple Configuration**: File-based config vs complex realm management
- **Docker Native**: Built for containerized environments
- **Healthcare Focus**: Perfect for medical environments with simpler needs

## Architecture

```
                  ORTHANC-AUTHELIA - Dual Access Flow
                  ===================================

                             [ BROWSER ]
         Auth Access              │                Shares
        ┌─────────────────────────┴──────────────────────┐
        ▼                                                ▼
 https://pacs/...                          https://pacs/share/?token=xxxxx
        │                                                │
  ┌─────┴──────┐                                   ┌─────┴──────┐
  │ NGINX      │                                   │ NGINX      │
  └─────┬──────┘                                   └─────┬──────┘
        │ auth_request                                   │ direct
        ▼                                                ▼
┌───────────────────┐                          ┌────────────────────┐
│ Authelia          │ ----────> REDIS <─────── │ Auth-Service       │
│ (SSO + 2FA)       │                          │ (token validation) │
└───────────────────┘                          └────────────────────┘
        │                                                │
        ▼                                                ▼
 ┌──────────────┐                              ┌────────────────────┐
 │ Orthanc      │ <─── Authorization Plugin ───┤ • OHIF Viewer      │
 │ + OHIF       │                              │ • Limited access   │
 └──────────────┘                              │ • Token expiry     │
                                               └────────────────────┘
```

**Authentication Flow**:
1. **User Login**: Browser → Authelia → Session cookie → Full access
2. **Token Sharing**: Share link → Auth-Service → Limited study access

## Key Features

- **Dual Authentication**: Authelia for users + token system for external sharing
- **Role-Based Access**: Admin, Doctor, External user roles with granular permissions
- **Three Medical Viewers**: OHIF (primary), Stone Web Viewer (advanced), VolView (3D)
- **Secure Sharing**: Time-limited, usage-limited tokens with copy-to-clipboard links
- **Token Manager**: OE2-themed admin dashboard with patient name resolution from DICOM metadata
- **OE2 Sidebar Integration**: "Partages" button injected directly into Orthanc Explorer 2
- **Programmatic Upload Endpoint**: Optional `/api-upload/instances` route for automated DICOM ingestion (scripts, batch imports) — bypasses Authelia SSO, uses HTTP Basic auth + dedicated `uploader` role restricted to POST-only
- **PostgreSQL Storage**: High-performance database backend
- **SSL Auto-Generation**: Self-signed certificates or custom SSL
- **Easy User Management**: Interactive scripts for user administration

## Quick Start

### Prerequisites

- Docker Engine 20.10+ with Compose plugin v2
- 4GB RAM minimum (8GB recommended)
- openssl (for secret generation, ships with most distros)

### Installation (3 commands)

```bash
git clone https://github.com/yokoinc/orthanc-authelia.git
cd orthanc-authelia
./bootstrap.sh && docker compose up -d
```

`bootstrap.sh` handles everything: generating the random secrets (Authelia ×3, PostgreSQL, the Orthanc service account), copying the configuration templates, substituting variables in the Authelia config, and generating a valid argon2id hash. Nothing to type in by hand.

From there:

1. **Setup wizard** — https://localhost:30443/console/setup
   Create the administrator account. Reachable without authentication on the first run only; the door closes as soon as the wizard is finalised.
2. **Orthanc Explorer** — https://localhost:30443/
   Log in with the account you have just created.
3. **Admin panel** — https://localhost:30443/console/
   User management, Orthanc configuration, component health. Restricted to the `admin` group.

The TLS certificate is generated automatically and self-signed: accept the browser warning on first access.

> **Self-signed certificate and HSTS.** With `SSL_MODE=selfsigned`, the stack
> deliberately sends no HSTS (`max-age=0`). The two are incompatible: once the
> directive is recorded, browsers refuse to offer the certificate exception and
> the site becomes unreachable, a blank page with no explanation. If you visited
> the site back when HSTS was still emitted, clear it once through
> `chrome://net-internals/#hsts`, section "Delete domain security policies".

### Admin interface

The panel is a Vue 3 application (`services/auth-service/frontend/`) compiled by Vite and embedded into the auth-service image at build time. Six tabs:

| Tab | What it does | Mechanism |
|---|---|---|
| **Users** | Create, edit, disable and delete accounts; change a password | Writes `users_database.yml`, which Authelia reloads within seconds |
| **Orthanc configuration** | ~60 settings from `orthanc.json`, grouped by theme and documented — including Explorer's appearance and share links — plus a button to restart Orthanc | Writes the JSON; the restart applies the changes |
| **Devices** | Declare DICOM modalities (scanner, MRI…) and test their connectivity with C-ECHO | Goes through Orthanc's API; effective immediately, no restart |
| **Health** | Redis, Orthanc, configuration files | Read-only |
| **Maintenance** | Public address, interface language, default viewer for share links, backups | Writes `.env`, `data/app-settings/settings.json` and `orthanc.json`; restores a file from `data/admin-backups/` |
| **Audit log** | Who did what and when: accounts, configuration, backups, rejected requests | Reads the audit stream from Redis |

Every write creates a rotating backup under `data/admin-backups/` (the last 10 are kept). The panel **edits** `orthanc.json` rather than regenerating it: only the targeted value is replaced, and the comments documenting each setting survive. Regenerating through `json.dumps()` used to erase them — observed on a real installation, where the first change made from the panel removed the file's 44 comments. The result is read back and compared with the expected structure before being written; if the formatting cannot be preserved, the API response says so instead of letting you find out later. Account management needs no access to the Docker socket: Authelia watches its user file and reloads it on its own.

#### Never having to open `orthanc.json`

This is the panel's design principle: an operator should not need a text editor to run their PACS. `bootstrap.sh` generates the whole installation without asking a single question, and the operational settings are changed from the interface — including Orthanc Explorer's, which still required editing the file: theme, offered viewers, default share-link duration, quick-access buttons.

Two families of fields are deliberately absent. The authentication plumbing (`Authorization.*`, `AuthenticationEnabled`): making it editable would be a security regression. And `OrthancExplorer2.Enable` / `IsDefaultOrthancUI`, which would allow disabling the interface **from** the interface, with no way back other than editing the file — precisely what we are trying to avoid. Tests lock both lists in both directions: what must be settable is, what must not be is not.

Every field declares its type, its default value and, where they are known, its bounds or allowed values. The interface turns these into drop-down lists rather than free fields — nobody has to remember that the share viewer is called `volview-viewer-publication` — and the server refuses anything out of range anyway: a port outside 1–65535, a negative delay, an unknown theme.

#### Restarting Orthanc from the panel

Orthanc's configuration, on the other hand, does not reload live. The `orthancteam` image **generates** `/tmp/orthanc.json` at startup, merging its defaults, the files under `/etc/orthanc/` and the `ORTHANC__*` variables; that generated file is what the process reads. `POST /tools/reset` only re-reads it, and therefore sees no change to our `orthanc.json`, which is merely its source. Only a container restart, which regenerates it, applies a change.

Remotely, SSH access is not always available: with no way out from the panel, changing the configuration leaves the operator stuck there. The **Restart Orthanc** button therefore goes through a dedicated service, `socket-proxy`, reachable only from the internal network (no published port). The Docker socket is **not** mounted into auth-service: handing it to a web-facing service would grant it the equivalent of root on the host.

The proxy's scope is deliberately tiny — `POST=1`, `ALLOW_RESTARTS=1`, and **everything else at 0**, `CONTAINERS` included. That last point is not cosmetic: with `CONTAINERS=1`, `POST=1` opens the whole of `/containers/*`, including `POST /containers/create`. Verified while setting this up, a privileged container mounting the host root was then accepted (HTTP 201) — exactly the escape this mount is meant to prevent. With `CONTAINERS=0`, `create` and `exec` answer 403 and restarting still works. `scripts/e2e-test.sh` replays these three checks, so that a one-line revert in the compose file does not go unnoticed.

The route waits for Orthanc to answer again before returning, rather than concluding as soon as Docker hands back control: a configuration accepted on write may well stop Orthanc from restarting, and that must show immediately. After 60 seconds without an answer, the panel restores the latest backup and restarts again, then reports what happened. Request and outcome are recorded in the audit log (`orthanc.restart.requested`, then `orthanc.restarted` or the matching failure).

To disable the feature, leave `DOCKER_PROXY_URL` empty in `.env` and remove the `socket-proxy` service from the compose file: the panel then shows the command to run by hand.

Two safeguards are worth knowing. The last active administrator can be neither deleted, nor disabled, nor removed from the `admin` group: the stack would be left with nobody to administer it, and the only way out would be editing `users_database.yml` by hand. And the first-run wizard closes for good once finalised — it cannot be used to create a second administrator.

The same tab sets the viewer offered by default when sharing a study from Explorer — OHIF, Stone Web Viewer or VolView. The choice remains changeable for each individual share.

This setting writes `OrthancExplorer2.Tokens.ShareType` into `orthanc.json` and therefore only takes effect after Orthanc restarts. An earlier version used an application setting with immediate effect, relying on the `default-viewer` returned by `/settings/roles`: that was a mistake, because Explorer never consults that field. Its bundle contains no occurrence of it and does `tokenType: this.tokens.ShareType`, that is, its own configuration. The setting wrote, read back — and changed nothing on screen. `/settings/roles` now returns that same field, so a client querying this API does not get an answer contradicting what actually applies.

#### Where each setting lives

Two locations, and the boundary between them is not a matter of taste:

| | `.env` | `data/app-settings/settings.json` |
|---|---|---|
| Contents | Secrets, credentials, `PUID`/`PGID`, `SSL_MODE`, `PUBLIC_URL` | Application preferences (language) |
| Read by | Docker Compose, **before** a container starts | auth-service, while running |
| Written by | `bootstrap.sh`, and the panel for `PUBLIC_URL` | The panel |
| Takes effect | When the stack restarts | Immediately |

A setting only auth-service consults has no business in `.env`: putting it there forces mounting that file writable inside the container, rewriting it in place — a `rename` fails on a file bind-mount — and mixes interface preferences with passwords. The settings file holds no secret, is written atomically, and a test checks it.

Existing installations do not break: a setting absent from the file is taken from its former environment variable, and the first change made from the panel moves it over.

The interface language follows the same path. It used to be frozen at module load from `LANGUAGE`: changing it meant recreating the container, for a display preference. Translations are now resolved at display time — with a cache invalidated by the settings file's modification time, so it is not re-read for every label — and the panel changes it live. `bootstrap.sh` still derives it from the system language on first run, but writes it into the settings rather than into `.env`.

The Maintenance tab also changes the public URL (`PUBLIC_URL`), updating `.env` and the Authelia configuration in one move. The change takes effect when the stack restarts, and requires logging back in at the new address — the session cookie being bound to the previous domain.

To develop the frontend with hot reload:

```bash
cd services/auth-service/frontend
npm install
npm run dev    # proxies /api to localhost:8000
```

### Full reset

```bash
docker compose down -v      # stop + supprime volumes (data included)
rm -rf services/authelia/config/{configuration.yml,users_database.yml}
rm -rf services/orthanc/config/orthanc.json .env docker-compose.yml
./bootstrap.sh              # regenerates everything from scratch
docker compose up -d
```

### Advanced: external PostgreSQL database

If you already run a postgres elsewhere, create a `docker-compose.override.yml` at the root:

```yaml
services:
  postgres:
    profiles: ["disabled"]    # never starts
  orthanc:
    networks:
      - orthanc-network
      - database              # your existing external network
    environment:
      - ORTHANC__POSTGRESQL__HOST=database
networks:
  database:
    external: true
```

See the [Database Setup Guide](docs/DATABASE_SETUP.md) for details.

## Access Points

Default ports: `30080` (HTTP) and `30443` (HTTPS)

- **Main Interface**: `https://your-domain/` (requires authentication)
- **OHIF Viewer**: `https://your-domain/ohif/` (primary medical viewer)
- **Orthanc Explorer 2**: `https://your-domain/ui/` (PACS administration)
- **Stone Web Viewer**: `https://your-domain/stone-webviewer/` (advanced viewer)
- **VolView**: `https://your-domain/volview/` (3D volumetric viewer)
- **Panel d'administration**: `https://your-domain/console/` (groupe `admin`)
- **Token Management**: `https://your-domain/auth/tokens/manage` (admin only)
- **External Shares**: `https://your-domain/share/?token=xxx` (no auth required)
- **Programmatic Upload**: `POST https://your-domain/api-upload/instances` (HTTP Basic auth, see [Programmatic Upload Endpoint](#programmatic-upload-endpoint))

## Configuration

### Essential Files

| File | Purpose | Example |
|------|---------|---------|
| `.env` | Environment variables | `.env.example` |
| `docker-compose.yml` | Service orchestration | `docker-compose.yml.example` |
| `services/authelia/config/configuration.yml` | Authelia config | `authelia-configuration.yml.example` |
| `services/authelia/config/users_database.yml` | User accounts | `authelia-users.yml.example` |
| `services/orthanc/config/orthanc.json` | Orthanc PACS config | `orthanc.json.example` |

### Critical: Credential Synchronization

These credentials **must match** across files:
- `.env`: `AUTH_USERNAME` / `AUTH_PASSWORD`
- `orthanc.json`: `WebServiceUsername` / `WebServicePassword`

### SSL Configuration

Three modes available via `SSL_MODE` in `.env`:

- **`selfsigned`** (default): Auto-generated certificates, perfect for development
- **`disabled`**: HTTP only, use when behind reverse proxy
- **`custom`**: Your own certificates (Let's Encrypt, commercial CA)

See [SSL Setup Guide](docs/SSL_SETUP.md) for detailed configuration.

## User Management

Accounts are managed from the administration panel, Users tab:

```
https://<your-domain>/console/
```

The panel is the only path that enforces the invariants -- argon2id hash, at
least one active administrator, an audit entry per change. Should it become
unreachable, `./manage-authelia-users.sh` does the same work from a console
on the host, without any of those guarantees.

Available user groups:
- **`admin`**: Full access including token management
- **`doctor`**: Medical data access (OHIF, Orthanc Explorer)
- **`external`**: Limited read-only access
- **`user`**: Basic authenticated access

After modifying users:
```bash
docker-compose restart authelia
```

## Programmatic Upload Endpoint

For automated DICOM ingestion (CD/DVD import scripts, batch jobs, modality integration), the stack exposes an optional `POST /api-upload/instances` route that **bypasses Authelia SSO** and uses HTTP Basic authentication instead. This is necessary because Authelia is designed for interactive browser logins and cannot be authenticated programmatically.

### Threat model and defense in depth

This endpoint is intentionally restricted to **upload only**:

- **Path scope**: only `/api-upload/instances` is exposed — no other Orthanc API
- **Auth chain**: HTTP Basic (nginx `htpasswd`) + dedicated `uploader` role (Orthanc Authorization plugin)
- **Role restriction**: the `uploader` role can ONLY `POST /instances`. Any other operation (read, list, delete, modify, share, system access) is denied by the auth-service
- **Rate limiting**: 2 requests/second sustained, burst of 5
- **Body size**: capped at 4 GB (large enough for ZIP archives of full DICOM CDs)

**If the htpasswd credentials are ever compromised**, the worst an attacker can do is fill the storage with bogus DICOM files (DoS by disk fill). No existing patient data can be read, listed, exfiltrated, or deleted through this endpoint.

For additional protection, the endpoint can be placed behind a Cloudflare Access Service Token, IP allowlist, or mTLS at the reverse-proxy layer.

### Enabling the endpoint

Set both variables in `.env` (leave empty to disable — nginx will return 500 on `/api-upload/*` requests, fail-closed):

```bash
UPLOAD_USER=upload-service
UPLOAD_PASSWORD=$(openssl rand -base64 24)
```

The nginx entrypoint generates `/etc/nginx/htpasswd` from these values at container start (SHA-256 hashed via `openssl passwd -5`). No manual file management required.

### Client usage

Single DICOM file:
```bash
curl -u "$UPLOAD_USER:$UPLOAD_PASSWORD" \
     --data-binary @image.dcm \
     -H "Content-Type: application/dicom" \
     https://your-domain/api-upload/instances
```

ZIP archive containing multiple DICOM files (Orthanc auto-detects the archive):
```bash
curl -u "$UPLOAD_USER:$UPLOAD_PASSWORD" \
     --data-binary @study.zip \
     -H "Content-Type: application/zip" \
     https://your-domain/api-upload/instances
```

The credentials are those generated by `bootstrap.sh` into `.env` (`UPLOAD_USER` / `UPLOAD_PASSWORD`). The endpoint asks for no Authelia session — it is the path for scripts — but it refuses unauthenticated requests: it accepts medical data, and leaving it open would let anyone on the network feed the database.

Note: if your reverse proxy or CDN enforces a body size limit smaller than your typical DICOM payload (e.g. Cloudflare Free/Pro caps at 100 MB), upload files individually rather than as a single archive.

### Internals

Request flow for `POST /api-upload/instances`:

1. **nginx** receives the request, matches `location ~ ^/api-upload/(instances)(?:/|$)`
2. **nginx Basic auth** validates `Authorization: Basic ...` against `/etc/nginx/htpasswd` (no Authelia call)
3. **nginx rewrites** the URL: `/api-upload/instances` → `/instances`
4. **nginx injects** `Remote-User: uploader` (and `X-Auth-User: uploader`, `Remote-Groups: uploader`)
5. **Orthanc Authorization plugin** reads `Remote-User` and POSTs to `auth-service:/tokens/validate`
6. **auth-service** maps `uploader` → `uploader-role` and grants the request **only if** `method == "post"` and `level == "instance"`
7. **Orthanc** ingests the DICOM into its standard storage pipeline (PostgreSQL + filesystem)

## Docker Registry

Three images are published on `registry.yokoinc.ovh`:

- `orthanc-nginx:1.1.0` — nginx, certificate generation, delegated authentication
- `orthanc-ohif:3.11.0` — visionneuse OHIF servie sous `/ohif/`
- `orthanc-auth-service:1.0.16` — service d'authentification, panel et partages

**No access to that registry is required.** Each service declares both
`image:` and `build:`: the published image is used when available, and built
from the repository otherwise. A fresh clone therefore starts without pulling
anything.

To force a local build:

```bash
docker compose build                 # all three
docker compose build nginx           # un seul
```

The OHIF build takes about fifteen minutes the first time: the viewer is
compiled from source. This is unavoidable — OHIF freezes its base path into the
bundle at build time, and the official `ohif/app` image, meant to be served at
the root, does not work under `/ohif/`.

To publish to your own registry, replace the `image:` entries of the
`docker-compose.yml` puis `docker compose build && docker compose push`.

## Documentation

Detailed guides available in `docs/`:

- **[Configuration Guide](docs/CONFIGURATION.md)** - Complete variable reference
- **[SSL Setup Guide](docs/SSL_SETUP.md)** - All SSL modes and reverse proxy setup
- **[Database Setup Guide](docs/DATABASE_SETUP.md)** - PostgreSQL configuration
- **[Troubleshooting Guide](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[Authelia User Management](docs/AUTHELIA_USER_MANAGEMENT.md)** - User permissions
- **[Token Sharing Guide](docs/TOKEN_SHARING.md)** - External sharing workflow
- **[Auth-Service Overview](docs/AUTH_SERVICE.md)** - Authentication service details
- **[Nginx Configuration](docs/NGINX_CONFIGURATION.md)** - Reverse proxy details
- **[Cloudflare Tunnel](docs/CLOUDFLARE_TUNNEL.md)** - Exposure without opening a port

## Troubleshooting

### Quick Checks

```bash
# Service status
docker compose ps

# Journaux
docker compose logs -f

# Redemarrage
docker compose restart

# Certificats
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
```

To validate a complete installation without touching the running one:

```bash
./scripts/e2e-test.sh
```

### Common Issues

- **Blank page, the site no longer answers**: most often an HSTS record kept
  while the certificate is self-signed — the browser then refuses any
  exception. Purge it through `chrome://net-internals/#hsts`. Check the
  server side first with `curl -k https://your-domain/auth/`: if it answers
  200, the problem is indeed in the browser.
- **Can't login**: check the account in the panel (Users tab); if the panel
  itself is unreachable, `./manage-authelia-users.sh` works from a console
- **Database connection failed**: Verify PostgreSQL is on `database` network
- **Port conflicts**: Change ports in `docker-compose.yml`
- **SSL warnings**: Normal for self-signed certificates

See [Troubleshooting Guide](docs/TROUBLESHOOTING.md) for complete solutions.

## Enabled Orthanc Plugins

- **PostgreSQL**: High-performance storage/index
- **DICOMweb**: Modern web DICOM protocol
- **Authorization**: Custom permission validation
- **Explorer 2**: Modern web interface
- **Stone Web Viewer**: High-performance viewer
- **VolView**: 3D volumetric visualization
- **Housekeeper**: Automatic maintenance
- **GDCM**: Enhanced DICOM codec support

## Sources and Acknowledgments

Built upon excellent open-source projects:

- **Orthanc PACS** - Sébastien Jodogne, UCLouvain - [orthanc-server.com](https://orthanc-server.com)
- **Authelia** - Modern authentication server
- **OHIF Viewer** - Open Health Imaging Foundation
- **PostgreSQL** - High-performance database
- **Redis** - In-memory data store

Forked and enhanced by **Grégory Cuffel** for the open-source medical imaging community.
