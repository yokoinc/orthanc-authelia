# ORTHANC-AUTHELIA

Medical PACS solution based on Orthanc with Authelia authentication (SSO, F2A, RBAC ...), OHIF viewer, and custom token management system.

## Overview

ORTHANC-AUTHELIA is a complete Picture Archiving and Communication System (PACS) designed for small to medium healthcare structures. It combines the power of Orthanc PACS with modern authentication (Authelia) and a professional DICOM viewer (OHIF v3.11.0).

**Platform Support**: Currently compiled and tested for **x86-64 Linux** only.

## Why Authelia over KeyCloak?

We chose **Authelia** instead of KeyCloak for several key reasons:

- **Lightweight**: Authelia is significantly lighter than KeyCloak, making it ideal for small healthcare structures
- **Simple Configuration**: File-based configuration vs complex KeyCloak realm management
- **Docker Native**: Designed for containerized environments from the ground up
- **Low Resource Usage**: Minimal memory and CPU footprint
- **Healthcare Focus**: Better suited for medical environments with simpler user management needs

KeyCloak, while powerful, is overkill for most PACS deployments and requires substantial resources and expertise to manage properly.

## Architecture

```
                    ORTHANC-AUTHELIA - Dual Access Flow
                    ===================================

                               [ BROWSER ]
           Auth Access              │                Shares
          ┌─────────────────────────┴──────────────────────┐
          ▼                                                ▼
   https://pacs/...                            https://pacs/share/?token=xxxxx
          │                                                │
    ┌─────┴──────┐                                   ┌─────┴──────┐
    │   NGINX    │                                   │   NGINX    │
    └─────┬──────┘                                   └─────┬──────┘
          │ auth_request                                   │ direct access
          ▼                                                ▼
 ┌───────────────────┐                           ┌────────────────────┐
 │  Authelia         │ ────────> REDIS <──────── │ Auth-Service       │
 │  (login + session)│                           │ (token validation) │
 └───────────────────┘                           └────────────────────┘
          │                                                │
          ▼                                                ▼
   ┌──────────────┐                              ┌────────────────────┐
   │ Auth headers │                              │ Token expiry check │
   └──────┬───────┘                              │ Usage count limit  │
          ▼                                      └─────────┬──────────┘
 ┌──────────────────┐                                      ▼
 │    Orthanc       │ <─── LibOrthancAuthorization ───>  Valid
 │    PACS Server   │                                      │
 └──────────────────┘                                      │
          │                                                ▼
          ▼                                       ┌──────────────────┐
 ┌──────────────────┐                             │ • OHIF Viewer    │
 │ Full Access:     │                             │ one token        │
 │                  │                             │ limited study    │
 │ • OHIF Viewer    │                             │ DICOMweb access  │
 │ • Stone Viewer   │                             └──────────────────┘
 │ • VolView        │
 │ • Share Creation │
 └──────────────────┘
```

### NGINX Routing Logic:

**Authentication Routes** → Authelia:
- `/auth/*` - Login interface
- `/api/verify` - Auth verification 
- `/static/*` - Authelia assets

**Token Sharing Routes** → Auth-Service:
- `/share/*` - External sharing (no auth required)

**Protected Routes** → Orthanc/OHIF (require auth_request):
- `/ohif/*` - OHIF medical viewer
- `/ui/*` - Orthanc Explorer  
- `/wado*` - DICOM image retrieval
- `/dicom-web*` - DICOMweb API
- `/studies*`, `/series*`, `/instances*` - DICOM resources
- `/stone-webviewer/*`, `/volview/*` - Secondary viewers

### Authentication Flow:

1. **User Login**: `/auth/` → Authelia → Session cookie
2. **Protected Access**: Protected route → `auth_request /authelia/` → Orthanc
3. **Token Access**: `/share/?token=xyz` → Auth-Service → Direct to Orthanc

## Key Features

### Security & Authentication
- **Dual Authentication System**: Authelia for user sessions + Custom Auth-Service for token management
- **Role-Based Access Control**: Admin, Doctor, External user roles with granular permissions
- **Secure Token Sharing**: Limited-use, time-limited sharing tokens for external access
- **Route Protection**: PACS routes protected with nginx auth_request module, with token-based bypass for external sharing

### Medical Imaging
- **Orthanc PACS**: Industry-standard DICOM server with PostgreSQL storage
- **OHIF Viewer v3.10.2**: Primary medical imaging viewer
- **Stone Web Viewer**: High-performance secondary viewer for advanced imaging
- **VolView**: 3D volumetric visualization for complex medical data
- **Multiple Plugins**: Enhanced functionality with specialized Orthanc plugins

### Management & Administration
- **Custom Token Manager**: Web interface for managing external sharing tokens
- **User Management Scripts**: Command-line tools for user administration
- **Quick Setup**: Automated configuration script for rapid deployment

## Security Implementation

### Route Protection Strategy

1. **Nginx Auth Request**: Most routes require Authelia authentication (`/ohif/`, `/wado`, `/dicom-web`)
2. **Token-Based Sharing**: External sharing via `/share/?token=` route (no auth required)
3. **Token Extraction**: Authenticated routes can extract tokens from URL parameters for API access
4. **Authorization Plugin**: Orthanc LibOrthancAuthorization validates all DICOM operations (both authenticated users and tokens)
5. **Custom Auth-Service**: Manages token lifecycle and permissions, validates tokens from `/share/` route

### Token System

Our custom token system provides:
- **Limited Usage**: Configurable maximum uses per token
- **Time Expiration**: Automatic token expiration
- **Controlled URLs**: Tokens only work with specific OHIF viewer URLs
- **Audit Trail**: Complete logging of token usage

### Permission Levels

1. **Session Level (Authelia)**:
   - `admin`: Full system access
   - `doctor`: Medical data access (read/write)
   - `external`: Limited read-only access

2. **Operation Level (LibOrthancAuthorization)**:
   - Every Orthanc API call validated
   - Resource-level permission checking
   - Integration with our Auth-Service for token validation

## Enabled Orthanc Plugins

- **PostgreSQL Storage/Index**: High-performance database backend
- **DICOMweb**: Modern web-based DICOM protocol support
- **Authorization**: Custom permission validation
- **Explorer 2**: Modern web interface with integrated viewers
- **Stone Web Viewer**: High-performance secondary medical imaging viewer
- **VolView**: 3D volumetric visualization and analysis
- **Housekeeper**: Automatic database maintenance
- **GDCM**: Enhanced DICOM codec support

### Medical Viewer Integration

The system provides **three complementary viewers** for different use cases:

1. **OHIF Viewer v3.11.0** (Primary)
   - Professional medical imaging interface
   - Token-based external sharing
   - Comprehensive measurement tools
   - Multi-planar reconstruction (MPR)

2. **Stone Web Viewer** (Secondary)
   - High-performance rendering engine
   - Advanced visualization capabilities
   - Optimized for large datasets
   - Cross-platform compatibility
   - Specialized medical imaging algorithms

3. **VolView** (Volumetric)
   - 3D volume rendering
   - Advanced volumetric analysis
   - Complex medical data visualization
   - Specialized for CT, MRI, and other volumetric studies
   - Interactive 3D manipulation

All viewers are integrated into Orthanc Explorer 2 and support the same authentication and token-based sharing system.

## Database Requirements

This stack requires an **external PostgreSQL database**. The database must be:
- PostgreSQL 12+ (15 recommended)
- Accessible via Docker network named `database`
- Pre-configured with a database for Orthanc

### External Database Setup

The stack connects to an external PostgreSQL container via the `database` network:

```yaml
networks:
  database:
    external: true
    name: database
```

**Step 1: Create the database network (if not exists)**
```bash
docker network create database
```

**Step 2: Connect your PostgreSQL container to the network**

If using an existing PostgreSQL container:
```bash
docker network connect database your-postgres-container
```

If creating a new PostgreSQL container:
```bash
docker run -d \
  --name postgres-database \
  --network database \
  -e POSTGRES_DB=orthanc \
  -e POSTGRES_USER=orthanc \
  -e POSTGRES_PASSWORD=your-secure-password \
  -v postgres-data:/var/lib/postgresql/data \
  postgres:15-alpine
```

**Step 3: Update credentials in configuration files**

In `docker-compose.yml`:
```yaml
environment:
  - POSTGRES_HOST=postgres-database  # Your container name
  - POSTGRES_USER=orthanc
  - POSTGRES_PASSWORD=your-secure-password
```

In `services/orthanc/config/orthanc.json`:
```json
"PostgreSQL": {
  "Host": "postgres-database",
  "Username": "orthanc",
  "Password": "your-secure-password"
}
```

**Database structure:**
- Orthanc automatically creates tables on first start (no manual setup needed)
- PostgreSQL used for both DICOM index and storage
- Optimized for medical imaging workloads with proper indexes

## Quick Start

### Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- External PostgreSQL database (see database setup below)
- 4GB RAM minimum, 8GB recommended
- x86-64 Linux system

### Initial Setup

1. **Clone the repository**:
```bash
git clone <repository-url>
cd orthanc-authelia
```

2. **Copy example files**:
```bash
cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
cp services/authelia/config/users_database.yml.example services/authelia/config/users_database.yml
cp services/orthanc/config/orthanc.json.example services/orthanc/config/orthanc.json
```

3. **Configure environment variables** (`.env`):
   - Set your `DOMAIN`
   - Generate secure secrets with: `openssl rand -base64 64`
   - Update `AUTH_USERNAME` and `AUTH_PASSWORD`

4. **Configure database** (`docker-compose.yml` and `services/orthanc/config/orthanc.json`):
   - Update PostgreSQL credentials to match your external database
   - Ensure the `database` network exists

5. **Create Authelia users**:
```bash
./manage-users.sh
```

6. **Start the stack**:
```bash
docker-compose up -d
```

### Verify Deployment

```bash
# Check all services are running
docker-compose ps

# View logs
docker-compose logs -f

# Test SSL certificates
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
```

### First Login

After deployment, access the system at `https://your-domain:30443` (or via your reverse proxy).

**Default credentials**: Use the accounts you created with `./manage-users.sh`

Example if you followed the setup:
- Email: `admin@example.com`
- Password: The password you set during user creation

**First steps:**
1. Login with your admin account at `https://your-domain/auth/`
2. Access Orthanc Explorer 2 at `https://your-domain/ui/`
3. Access OHIF viewer at `https://your-domain/ohif/`
4. Upload test DICOM images via Orthanc Explorer 2
5. Test token management at `https://your-domain/auth/tokens/manage` (admin only)

### User Management

To create or modify Authelia users:

```bash
./manage-users.sh
```

The script will prompt for:
- Number of users to create
- Email, display name, password, and group for each user
- Available groups: `admin`, `doctor`, `external`, `user`

After modifying users:
```bash
docker-compose restart authelia
```

### SSL Certificates

Certificates are automatically generated and persisted in a Docker volume:
- Volume: `orthanc_nginx_ssl`
- Auto-generated on first start (365-day validity)
- Self-signed certificates for internal nginx use

**Check certificate expiration**:
```bash
docker exec orthanc-nginx openssl x509 -in /etc/nginx/ssl/cert.pem -noout -dates
```

**Use custom certificates** (optional):
```bash
# Copy your certificates into the nginx container
docker cp your-cert.pem orthanc-nginx:/etc/nginx/ssl/cert.pem
docker cp your-key.pem orthanc-nginx:/etc/nginx/ssl/key.pem
docker-compose restart nginx
```

⚠️ **Note**: If using Cloudflare Tunnel or a reverse proxy, these certificates are only for nginx internal SSL. External SSL is handled by your proxy.

## Access Points

Default ports: `30080` (HTTP) and `30443` (HTTPS)

- **Main PACS Interface**: `https://your-domain/` (requires authentication)
- **OHIF Viewer**: `https://your-domain/ohif/` (primary medical viewer)
- **Stone Web Viewer**: `https://your-domain/stone-webviewer/` (secondary viewer)
- **VolView**: `https://your-domain/volview/` (3D volumetric viewer)
- **Token Management Interface**: `https://your-domain/auth/tokens/manage` (admin only)
- **Orthanc Explorer 2**: `https://your-domain/ui/` (modern interface with integrated viewers)

All routes require Authelia authentication except:
- `/share/?token=xxx` - External sharing links with time-limited tokens

### Token Management Interface (Admin Only)

The token management interface at `http://your-domain/auth/tokens/manage` is **only accessible to users with admin role** authenticated through Authelia. This interface provides:

#### Capabilities:
- **View Active Tokens**: Display all currently valid sharing tokens with details
- **Token Analytics**: Usage statistics, expiration times, and access patterns
- **Revoke Tokens**: Manually disable tokens before their expiration
- **View Expired Tokens**: Review history of previously used tokens
- **Token Statistics**: System-wide metrics on token usage and performance
- **Audit Trail**: Complete logging of all token operations
- **Real-time Monitoring**: Live updates of token usage and status

#### Features:
- **Interactive Dashboard**: Modern web interface with real-time updates
- **Token Details**: View token ID, type, creation date, expiration, usage count
- **Resource Information**: See which studies/series each token grants access to
- **Security Monitoring**: Identify suspicious usage patterns
- **Bulk Operations**: Manage multiple tokens simultaneously

## Custom Auth-Service

Our internally developed Auth-Service provides:

- **Token Lifecycle Management**: Creation, validation, revocation
- **Integration Bridge**: Connects Authelia sessions with Orthanc permissions
- **Web Interface**: User-friendly token management dashboard
- **API Endpoints**: RESTful API for token operations
- **Audit Logging**: Complete activity tracking

The service is called by:
- LibOrthancAuthorization plugin for every protected operation
- Nginx for token-based route validation
- OHIF viewer for external sharing links

## Configuration Files

### Essential Configuration

1. **`.env`** - Environment variables:
   - Domain configuration
   - Authentication credentials (must match orthanc.json)
   - Authelia security secrets
   - SSL mode and settings

2. **`docker-compose.yml`** - Service orchestration:
   - Service definitions and versions
   - Port mappings (default: 30080/30443)
   - Volume mounts
   - Network configuration

3. **`services/authelia/config/users_database.yml`** - User accounts:
   - User emails and hashed passwords
   - Group memberships (admin, doctor, external, user)
   - Display names

4. **`services/orthanc/config/orthanc.json`** - Orthanc PACS configuration:
   - PostgreSQL database credentials
   - Auth-Service API credentials (must match .env)
   - Plugin configuration
   - Viewer integration settings

### Important: Credential Synchronization

These credentials **must match** across files:

- `AUTH_USERNAME` / `AUTH_PASSWORD` in `.env`
- `WebServiceUsername` / `WebServicePassword` in `orthanc.json`

### Optional Configuration

Advanced settings in `.env`:
- `LOG_LEVEL` - Logging verbosity
- `DEFAULT_TOKEN_MAX_USES` - Maximum uses per token
- `DEFAULT_TOKEN_VALIDITY_SECONDS` - Token expiration time
- `CACHE_VALIDITY_*` - Cache durations
- `AUDIT_RETENTION_DAYS` - Audit log retention


## Documentation

- [Authelia User Management Guide](docs/AUTHELIA_USER_MANAGEMENT.md) - Adding/removing users and permissions
- [Token Sharing Guide](docs/TOKEN_SHARING.md) - External sharing workflow
- [Auth-Service Overview](docs/AUTH_SERVICE.md) - Custom authentication service capabilities
- [Nginx Configuration Guide](docs/NGINX_CONFIGURATION.md) - Reverse proxy setup and SSL status
- [Development Guide](docs/DEVELOPMENT.md) - Customization and development

## Docker Registry

This project uses a **private Docker registry** at `registry.yokoinc.ovh` for custom-built images:

- `registry.yokoinc.ovh/orthanc-nginx:1.0.2` - Nginx reverse proxy with SSL auto-generation
- `registry.yokoinc.ovh/orthanc-ohif:3.11.0` - OHIF viewer v3.11.0 with French translation
- `registry.yokoinc.ovh/orthanc-auth-service:1.0.8` - Custom authentication service

### Using a Different Registry

To use your own registry or rebuild images locally:

1. **Build images locally**:
```bash
# Build nginx
cd services/nginx
docker build -t your-registry/orthanc-nginx:1.0.2 .

# Build OHIF
cd services/ohif/docker
docker build -t your-registry/orthanc-ohif:3.11.0 .
```

2. **Update docker-compose.yml** with your registry URLs

3. **Push to your registry** (optional):
```bash
docker push your-registry/orthanc-nginx:1.0.2
docker push your-registry/orthanc-ohif:3.11.0
```

**Note**: The auth-service source code is proprietary. Contact the project maintainer for access or implement your own authentication service compatible with the Orthanc Authorization plugin.

## Troubleshooting

### Common Issues

**1. Services fail to start - "network database not found"**
```bash
# Create the external database network
docker network create database
```

**2. Orthanc fails with "Connection refused" to PostgreSQL**
- Check PostgreSQL container is running: `docker ps | grep postgres`
- Verify PostgreSQL is on the database network: `docker network inspect database`
- Check credentials match in docker-compose.yml and orthanc.json

**3. Nginx generates new certificates on every restart**
- Certificates are stored in Docker volume `orthanc_nginx_ssl`
- Check volume exists: `docker volume ls | grep nginx_ssl`
- Don't delete the volume if you want to keep certificates

**4. Cannot login to Authelia - "Invalid credentials"**
- Verify users_database.yml is correctly configured
- Passwords must be hashed with argon2: `./manage-users.sh`
- Restart Authelia after user changes: `docker-compose restart authelia`

**5. OHIF viewer shows "Failed to load study"**
- Check Orthanc is accessible: `docker-compose logs orthanc`
- Verify DICOMweb is enabled in orthanc.json
- Check browser console for CORS or network errors

**6. "Authorization denied" in Orthanc logs**
- Verify AUTH_USERNAME and AUTH_PASSWORD match in .env and orthanc.json
- Check auth-service is running: `docker-compose ps auth-service`
- Review auth-service logs: `docker-compose logs auth-service`

**7. Port already in use (30080 or 30443)**
```bash
# Check what's using the port
sudo netstat -tulpn | grep 30080

# Either stop the conflicting service or change ports in docker-compose.yml
```

### Viewing Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f orthanc
docker-compose logs -f authelia
docker-compose logs -f nginx

# Last 100 lines
docker-compose logs --tail=100 orthanc
```

### Resetting the Stack

If you need to completely reset:

```bash
# Stop and remove containers
docker-compose down

# Remove volumes (WARNING: deletes SSL certificates)
docker volume rm orthanc_nginx_ssl

# Remove configurations (WARNING: deletes user accounts)
rm services/authelia/config/users_database.yml
rm services/authelia/config/db.sqlite3

# Start fresh
./manage-users.sh
docker-compose up -d
```

## Support

This solution is designed for healthcare environments requiring:
- DICOM storage and viewing
- Secure external sharing
- Role-based access control
- Easy deployment and maintenance

Perfect for medical clinics, radiology centers, and small hospitals needing a professional PACS solution without enterprise complexity.

## Sources and Acknowledgments

This project builds upon the excellent work of:

### Orthanc PACS Server
- **Author**: Sébastien Jodogne
- **Official Website**: [orthanc-server.com](https://orthanc-server.com)
- **Official GitHub**: [github.com/jodogne/OrthancMirror](https://github.com/jodogne/OrthancMirror)
- **Institution**: Université catholique de Louvain (UCLouvain)

### Orthanc Team
- **Official Repository**: [github.com/orthanc-team](https://github.com/orthanc-team)
- **Plugins and Extensions**: Various Orthanc plugins and tools

### Additional Components
- **Authelia**: Modern authentication and authorization server
- **OHIF Viewer**: Open Health Imaging Foundation medical viewer
- **PostgreSQL**: High-performance database backend
- **Redis**: In-memory data structure store

Forked and enhanced by **Dr. Grégory Cuffel** with love for the open-source medical imaging community. 🏥💙