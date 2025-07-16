# ORTHANC-AUTHELIA

Medical PACS solution based on Orthanc with Authelia authentication (SSO, F2A, RBAC ...), OHIF viewer, and custom token management system.

## Overview

ORTHANC-AUTHELIA is a complete Picture Archiving and Communication System (PACS) designed for small to medium healthcare structures. It combines the power of Orthanc PACS with modern authentication (Authelia) and a professional DICOM viewer (OHIF v3.10.2).

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

1. **OHIF Viewer v3.10.2** (Primary)
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

## Database

- **PostgreSQL 15**: Clean, empty database ready for DICOM data
- **Scalable Storage**: Optimized for medical imaging workloads
- **Configurable**: Both internal and external database options

## Quick Start

### Option 1: Automated Setup (Recommended)

```bash
git clone <repository>
cd pacs-orthanc-authelia
chmod +x setup.sh
./setup.sh
```

The setup script will:
1. Collect configuration interactively
2. Generate secure secrets automatically
3. Create all configuration files from templates
4. Set up user accounts with hashed passwords
5. Choose appropriate docker-compose file

### Option 2: Manual Configuration

1. Copy template files:
```bash
cp .env.example .env
cp services/authelia/config/configuration.yml.example services/authelia/config/configuration.yml
cp services/orthanc/config/orthanc.json.example services/orthanc/config/orthanc.json
```

2. Edit configuration files manually
3. Generate Authelia secrets:
```bash
openssl rand -base64 48  # Generate 3 secrets for Authelia
```

### Deployment

```bash
# For internal PostgreSQL (standalone)
docker-compose -f docker-compose.standalone.yml up -d

# For external PostgreSQL
docker-compose up -d
```

## Access Points

- **Main PACS Interface**: `http://your-domain/` (requires authentication)
- **OHIF Viewer**: `http://your-domain/ohif/` (primary medical viewer)
- **Stone Web Viewer**: `http://your-domain/stone-web-viewer/` (secondary viewer)
- **VolView**: `http://your-domain/volview/` (3D volumetric viewer)
- **Token Management Interface**: `http://your-domain/auth/tokens/manage` (admin only)
- **Orthanc Explorer**: `http://your-domain/ui/` (modern interface with integrated viewers)

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

## Configuration

All passwords and authentication credentials are configurable:

- **Orthanc ↔ Auth-Service**: Configurable API credentials
- **Authelia Users**: Managed via users_database.yml
- **Database Credentials**: Fully customizable
- **Token Settings**: Lifetime, usage limits, permissions


## Documentation

- [Authelia User Management Guide](docs/AUTHELIA_USER_MANAGEMENT.md) - Adding/removing users and permissions
- [Token Sharing Guide](docs/TOKEN_SHARING.md) - External sharing workflow
- [Auth-Service Overview](docs/AUTH_SERVICE.md) - Custom authentication service capabilities
- [Nginx Configuration Guide](docs/NGINX_CONFIGURATION.md) - Reverse proxy setup and SSL status
- [Development Guide](docs/DEVELOPMENT.md) - Customization and development

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