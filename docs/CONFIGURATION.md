# Configuration Guide

Complete reference for all configuration variables and files in ORTHANC-AUTHELIA.

## Table of Contents

- [Environment Variables (.env)](#environment-variables-env)
- [Docker Compose Variables](#docker-compose-variables)
- [Orthanc Configuration](#orthanc-configuration)
- [Authelia Configuration](#authelia-configuration)
- [Authelia Users](#authelia-users)
- [Configuration Checklist](#configuration-checklist)
- [Common Configuration Mistakes](#common-configuration-mistakes)

## Environment Variables (.env)

### Required Variables

| Variable | Description | Example | Where It's Used |
|----------|-------------|---------|-----------------|
| `DOMAIN` | Your domain name or IP address | `pacs.yourdomain.com` or `192.168.1.100` | Nginx, Authelia |
| `AUTH_USERNAME` | API username for auth-service | `share-user` | Auth-service, Orthanc |
| `AUTH_PASSWORD` | API password for auth-service | `your-secure-password` | Auth-service, Orthanc |
| `AUTHELIA_SESSION_SECRET` | Secret for session encryption | Generate with `openssl rand -base64 64` | Authelia |
| `AUTHELIA_STORAGE_ENCRYPTION_KEY` | Secret for storage encryption | Generate with `openssl rand -base64 64` | Authelia |
| `AUTHELIA_JWT_SECRET` | Secret for JWT tokens | Generate with `openssl rand -base64 64` | Authelia |

### Optional Variables

| Variable | Description | Default | Notes |
|----------|-------------|---------|-------|
| `SSL_MODE` | SSL certificate mode | `selfsigned` | Options: `selfsigned`, `disabled`, `custom` |
| `TZ` | Timezone | `UTC` | See [TZ database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| `LANGUAGE` | Interface language | `en` | Options: `en`, `fr` |
| `LOG_LEVEL` | Logging verbosity | `INFO` | Options: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `AUTHELIA_LOG_LEVEL` | Authelia log level | `info` | Options: `trace`, `debug`, `info`, `warn`, `error` |
| `DEFAULT_TOKEN_MAX_USES` | Max uses per sharing token | `50` | Limit token reuse |
| `DEFAULT_TOKEN_VALIDITY_SECONDS` | Token expiration time | `604800` (7 days) | In seconds |
| `CACHE_VALIDITY_USER_SESSION` | User session cache duration | `300` (5 min) | In seconds |
| `CACHE_VALIDITY_SHARE_TOKEN` | Token cache duration | `60` (1 min) | In seconds |
| `AUDIT_RETENTION_DAYS` | Audit log retention | `90` | Days to keep logs |

**Generate secure secrets**:
```bash
# Generate random 64-character base64 strings
openssl rand -base64 64 | tr -d '\n'
```

## Docker Compose Variables

### PostgreSQL Connection (Orthanc service)

These variables **must match** your external PostgreSQL database configuration:

```yaml
orthanc:
  environment:
    - POSTGRES_HOST=database        # PostgreSQL container name or hostname
    - POSTGRES_PORT=5432            # PostgreSQL port (usually 5432)
    - POSTGRES_DB=orthanc           # Database name
    - POSTGRES_USER=orthanc         # Database username
    - POSTGRES_PASSWORD=change_this # Database password (CHANGE THIS!)
```

⚠️ **Critical**: These credentials must also be configured in `services/orthanc/config/orthanc.json`.

### Port Mappings

```yaml
nginx:
  ports:
    - "30080:80"    # HTTP port (always available)
    - "30443:443"   # HTTPS port (when SSL_MODE is selfsigned or custom)
```

Change these if ports are already in use on your system.

## Orthanc Configuration

File: `services/orthanc/config/orthanc.json`

### PostgreSQL Connection

Must **exactly match** the values in `docker-compose.yml`:

```json
"PostgreSQL": {
  "Host": "database",
  "Port": 5432,
  "Database": "orthanc",
  "Username": "orthanc",
  "Password": "change_this",
  "EnableIndex": true,
  "EnableStorage": true
}
```

### Auth-Service Credentials

Must **exactly match** `AUTH_USERNAME` and `AUTH_PASSWORD` from `.env`:

```json
"Authorization": {
  "WebServiceUrl": "http://auth-service:8080/orthanc/access",
  "WebServiceUsername": "share-user",
  "WebServicePassword": "your-secure-password",
  "TokenHttpHeaders": ["token"],
  "TokenGetArguments": ["token"]
}
```

⚠️ **Critical Synchronization**: If these credentials don't match `.env`, Orthanc authorization will fail.

### OHIF Viewer Base URL

Set this to match your domain:

```json
"OHIF": {
  "BaseUrl": "https://pacs.yourdomain.com/ohif/"
}
```

## Authelia Configuration

File: `services/authelia/config/configuration.yml`

### Domain Configuration

Must match your `DOMAIN` from `.env`:

```yaml
session:
  domain: pacs.yourdomain.com  # Change to your domain
  name: authelia_session
```

### Access Control Rules

Configure which user groups can access which routes:

```yaml
access_control:
  default_policy: deny

  rules:
    # Admin-only routes
    - domain: pacs.yourdomain.com
      policy: two_factor
      subject:
        - "group:admin"
      resources:
        - "^/auth/tokens.*$"

    # Doctor access
    - domain: pacs.yourdomain.com
      policy: two_factor
      subject:
        - "group:admin"
        - "group:doctor"
      resources:
        - "^/ui.*$"
        - "^/ohif.*$"
        - "^/dicom-web.*$"
```

### Redis Connection

Should match your Redis service name in docker-compose.yml:

```yaml
session:
  redis:
    host: redis
    port: 6379
```

## Authelia Users

File: `services/authelia/config/users_database.yml`

**DO NOT edit this file manually!** Use the provided script instead:

```bash
./manage-authelia-users.sh
```

The script will:
- Hash passwords with Argon2id
- Create proper user structure
- Assign groups correctly

User groups available:
- `admin`: Full access (including token management)
- `doctor`: Medical data access (OHIF, Orthanc Explorer)
- `external`: Limited read-only access
- `user`: Basic authenticated access

## Configuration Checklist

Use this checklist when deploying:

- [ ] **Copy all example files** to their final locations
- [ ] **Generate 3 unique secrets** for Authelia (session, storage, JWT)
- [ ] **Set DOMAIN** in `.env` to your actual domain or IP
- [ ] **Create AUTH_USERNAME and AUTH_PASSWORD** in `.env`
- [ ] **Update PostgreSQL credentials** in both:
  - [ ] `docker-compose.yml` (orthanc service environment)
  - [ ] `services/orthanc/config/orthanc.json` (PostgreSQL section)
- [ ] **Synchronize auth credentials** between:
  - [ ] `.env` (AUTH_USERNAME, AUTH_PASSWORD)
  - [ ] `services/orthanc/config/orthanc.json` (WebServiceUsername, WebServicePassword)
- [ ] **Update OHIF BaseUrl** in `orthanc.json` to match your domain
- [ ] **Set session domain** in `services/authelia/config/configuration.yml`
- [ ] **Configure SSL_MODE** in `.env` based on your deployment
- [ ] **Create Authelia users** with `./manage-authelia-users.sh`
- [ ] **Create database network**: `docker network create database`
- [ ] **Connect PostgreSQL** to database network

## Common Configuration Mistakes

### 1. Mismatched PostgreSQL credentials
- **Symptom**: Orthanc fails to start with "Connection refused"
- **Fix**: Ensure `docker-compose.yml` and `orthanc.json` have identical database credentials

### 2. Mismatched Auth-Service credentials
- **Symptom**: "Authorization denied" in Orthanc logs
- **Fix**: Ensure `.env` AUTH_USERNAME/AUTH_PASSWORD match `orthanc.json` WebServiceUsername/WebServicePassword

### 3. Wrong domain in Authelia
- **Symptom**: Session cookies not working, constant re-login
- **Fix**: Update `session.domain` in `configuration.yml` to match your actual domain

### 4. Weak or missing secrets
- **Symptom**: Authelia fails to start, security warnings
- **Fix**: Generate proper 64-character random secrets with `openssl rand -base64 64`

### 5. SSL mode mismatch
- **Symptom**: Browser can't connect, certificate errors
- **Fix**: Set `SSL_MODE` appropriately for your setup (see [SSL Setup Guide](SSL_SETUP.md))
