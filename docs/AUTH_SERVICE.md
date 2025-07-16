# Auth-Service Overview

## What is Auth-Service?

Auth-Service is a **custom FastAPI service** that bridges authentication between Authelia and the LibOrthancAuthorization plugin. It acts as the **authorization engine** for all DICOM operations.

## Core Function

**Real-time Permission Validation**: For every single DICOM API request, LibOrthancAuthorization queries Auth-Service to validate if the user/token has permission to perform the operation.

## Key Capabilities

### 1. User Role Validation
- **Role Mapping**: Converts Authelia groups (`admin`, `doctor`, `external`) to plugin roles (`admin-role`, `doctor-role`, `external-role`)
- **Permission Logic**: Implements business rules for each role
- **Session Integration**: Validates user sessions from Authelia via Redis

### 2. Token Management
- **Token Creation**: Generates secure sharing tokens for external access
- **Token Validation**: Validates tokens for resource access
- **Usage Tracking**: Monitors token usage and enforces limits
- **Expiration Control**: Handles automatic token expiration

### 3. Admin Interface
- **Web Dashboard**: `/auth/tokens/manage` - Admin-only token management interface
- **Token Statistics**: Real-time usage analytics
- **Token Revocation**: Manual token disabling
- **Audit Trail**: Complete activity logging

### 4. API Endpoints

#### Authentication Validation
```bash
POST /tokens/validate
# Called by LibOrthancAuthorization for every DICOM operation
```

#### User Profile
```bash
POST /user/get-profile
# Maps Authelia groups to plugin roles
```

#### Token Operations
```bash
POST /tokens/ohif-viewer-publication  # Create sharing token
GET /tokens                           # List active tokens
GET /tokens/expired                   # List expired tokens
DELETE /tokens/{token_id}             # Revoke token
GET /tokens/stats                     # Usage statistics
```

#### External Sharing
```bash
GET /share/?token=xyz                 # Token-based study access
```

## Technical Architecture

### Storage
- **Redis**: Sessions, tokens, and audit logs
- **TTL Management**: Automatic expiration handling
- **Cache Optimization**: Fast permission lookups

### Security
- **Role-Based Access**: Granular permission control
- **Resource Isolation**: Tokens only access specified studies
- **Audit Logging**: Complete activity tracking
- **Usage Limits**: Configurable token constraints

### Integration
- **LibOrthancAuthorization**: Per-request validation
- **Authelia**: Session and user management
- **Nginx**: Header forwarding and routing
- **OHIF**: Token injection for external access

## Permission Model

### Admin Role
- All DICOM operations
- Token creation and management
- System access

### Doctor Role
- Medical data access
- Study upload and modification
- No token creation

### External Role
- Read-only access to assigned studies
- Limited to specific resources

### Token Access
- Read-only access only
- Resource-specific permissions
- Time and usage limited

## Configuration

### Environment Variables
```bash
LANGUAGE=en                           # Interface language
AUTH_USERNAME=share-user              # API username
AUTH_PASSWORD=secure-password         # API password
REDIS_HOST=redis                      # Redis connection
DEFAULT_TOKEN_MAX_USES=50             # Token usage limit
DEFAULT_TOKEN_VALIDITY_SECONDS=604800 # 7 days
```

### Translation Support
- **English/French**: UI translations via JSON files
- **Dynamic Loading**: Language switching without restart
- **Fallback**: English as default

## Monitoring

### Health Check
```bash
GET /health
# Service status and Redis connectivity
```

### Logging
- **Structured Logging**: JSON format with request IDs
- **Audit Trail**: All permission checks logged
- **Error Tracking**: Detailed error information

## Current Status

**Production Ready**: Fully functional for all authentication and token operations

**Missing Features**: 
- User management interface (users managed via Authelia configuration)
- Advanced rate limiting
- Metrics collection endpoint