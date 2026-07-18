# ORTHANC-AUTHELIA

Medical PACS solution based on Orthanc with Authelia authentication (SSO, 2FA, RBAC), OHIF viewer, and custom token management system.

**Platform Support**: x86-64 Linux only

## Overview

ORTHANC-AUTHELIA is a complete Picture Archiving and Communication System (PACS) for small to medium healthcare structures. It combines:
- **Orthanc PACS** - Industry-standard DICOM server with PostgreSQL storage
- **Authelia** - Modern authentication with SSO and 2FA
- **OHIF Viewer v3.12.0** - Professional medical imaging viewer
- **Custom Auth-Service** - Token-based external sharing with OE2-themed management UI
- **Multiple Viewers** - OHIF, Stone Web Viewer, and VolView for different use cases

## Stack & Versions

Component versions as defined in `docker-compose.yml` (keep this table in sync when bumping images):

| Component | Image | Version |
|-----------|-------|---------|
| Orthanc PACS | `orthancteam/orthanc` | `26.6.1` |
| Authelia | `authelia/authelia` | `4.39.20` |
| Redis | `redis` | `8.0-alpine` |
| OHIF Viewer | `registry.yokoinc.ovh/orthanc-ohif` | `3.12.0` |
| Nginx | `registry.yokoinc.ovh/orthanc-nginx` | `1.1.1` |
| Auth-Service | `registry.yokoinc.ovh/orthanc-auth-service` | `1.0.15` |

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

- Docker Engine 20.10+
- Docker Compose 2.0+
- External PostgreSQL database (or use optional local container)
- 4GB RAM minimum (8GB recommended)

### Installation

1. **Clone and setup configuration**:
```bash
git clone <repository-url>
cd orthanc-authelia

# Copy example files
cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
cp authelia-configuration.yml.example services/authelia/config/configuration.yml
cp authelia-users.yml.example services/authelia/config/users_database.yml
cp orthanc.json.example services/orthanc/config/orthanc.json
```

2. **Configure environment** (`.env`):
```bash
# Set your domain
DOMAIN=pacs.yourdomain.com

# Generate secrets
openssl rand -base64 64  # Use for AUTHELIA_SESSION_SECRET
openssl rand -base64 64  # Use for AUTHELIA_STORAGE_ENCRYPTION_KEY
openssl rand -base64 64  # Use for AUTHELIA_JWT_SECRET

# Set auth-service credentials
AUTH_USERNAME=share-user
AUTH_PASSWORD=your-secure-password
```

3. **Setup database**:
```bash
# Create network for external PostgreSQL
docker network create database

# Connect your PostgreSQL container
docker network connect database your-postgres-container

# Update credentials in docker-compose.yml and services/orthanc/config/orthanc.json
```

See [Database Setup Guide](docs/DATABASE_SETUP.md) for detailed instructions.

4. **Create users**:
```bash
./manage-authelia-users.sh
```

5. **Start the stack**:
```bash
docker-compose up -d
```

### First Login

Access the system at `https://your-domain:30443` (or via your reverse proxy).

Login with the admin account you created:
1. Access `https://your-domain/auth/` to login
2. Open OHIF viewer at `https://your-domain/ohif/`
3. Access Orthanc Explorer 2 at `https://your-domain/ui/`
4. Upload test DICOM images
5. Manage tokens at `https://your-domain/auth/tokens/manage` (admin only)

## Access Points

Default ports: `30080` (HTTP) and `30443` (HTTPS)

- **Main Interface**: `https://your-domain/` (requires authentication)
- **OHIF Viewer**: `https://your-domain/ohif/` (primary medical viewer)
- **Orthanc Explorer 2**: `https://your-domain/ui/` (PACS administration)
- **Stone Web Viewer**: `https://your-domain/stone-webviewer/` (advanced viewer)
- **VolView**: `https://your-domain/volview/` (3D volumetric viewer)
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

Create or modify users:
```bash
./manage-authelia-users.sh
```

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

Custom images hosted at `registry.yokoinc.ovh`:

- `orthanc-nginx:1.1.1` - Nginx with SSL auto-generation
- `orthanc-ohif:3.12.0` - OHIF viewer with French translation
- `orthanc-auth-service:1.0.16` - Custom authentication service

### Using Your Own Registry

Build images locally:
```bash
# Auth-Service
cd services/auth-service/sources
docker build -t your-registry/orthanc-auth-service:1.0.16 .

# Nginx
cd services/nginx
docker build -t your-registry/orthanc-nginx:1.1.1 .

# OHIF (long build ~15min)
cd services/ohif/docker
docker build -t your-registry/orthanc-ohif:3.12.0 .
```

Update `docker-compose.yml` with your registry URLs.

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

## Troubleshooting

### Quick Checks

```bash
# Check all services are running
docker-compose ps

# View logs
docker-compose logs -f

# Restart services
docker-compose restart

# Check SSL certificates
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
```

### Common Issues

- **Can't login**: Run `./manage-authelia-users.sh` and restart Authelia
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
