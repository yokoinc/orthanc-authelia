# Authelia User Management Guide

This guide explains how to manage users in the ORTHANC-AUTHELIA system and the permission architecture.

## Permission Architecture

ORTHANC-AUTHELIA implements a **two-level permission system**:

### Level 1: Authelia Session Authentication & Role Attribution
- **Purpose**: Authenticates users and assigns the predefined roles required by LibOrthancAuthorization
- **Mechanism**: Session-based authentication with role mapping
- **Storage**: Users stored in `services/authelia/config/users_database.yml`
- **Role Assignment**: When a user is authenticated, Authelia tells the Authorization Plugin:
  - `admin` user → `admin-role`
  - `doctor` user → `doctor-role`
  - `external` user → `external-role`
- **Session Duration**: Sessions are persistent with configurable timeout (default: 1 hour inactivity, 12 hours maximum)

### Level 2: LibOrthancAuthorization Plugin with Custom Auth-Service
- **Purpose**: Controls what operations users can perform on DICOM resources
- **Total Access Control**: When LibOrthancAuthorization is activated, **no access to Orthanc is possible without authentication**
- **Mechanism**: The LibOrthancAuthorization plugin intercepts **every single DICOM API request**
- **Custom Integration**: Our Auth-Service is called for **each request** to validate permissions
- **Predefined Roles**: The plugin expects specific role names hardcoded in its source:
  - `admin-role`
  - `doctor-role`
  - `external-role`
- **Real-time Validation**: Unlike static configurations, our system validates permissions dynamically on each operation (redis retention)

## User Roles and Permissions

The LibOrthancAuthorization plugin requires these exact role names to function:

### Admin Role (`admin` → `admin-role`)
**Authelia Session**: Full system access
**Plugin Role Mapping**: `admin-role` 
**Orthanc Operations**: 
- All DICOM operations (view, upload, delete, modify)
- Token creation and management

### Doctor Role (`doctor` → `doctor-role`) 
**Authelia Session**: Medical professional access
**Plugin Role Mapping**: `doctor-role`
**Orthanc Operations**:
- View all patient data
- Upload new studies
- Modify study metadata
- Generate reports
- **Restrictions**: Cannot delete system data or modify system settings

### External Role (`external` → `external-role`)
**Authelia Session**: Limited guest access
**Plugin Role Mapping**: `external-role`
**Orthanc Operations**:
- Read-only access to assigned studies
- Basic viewing operations
- **Restrictions**: Cannot upload, delete, or modify any data

## How the Permission System Works

### 1. Initial Access (Authelia)
```
User Login → Authelia Authentication → Session Cookie → Nginx Auth Request
```

### 2. Per-Request Validation (LibOrthancAuthorization)
```
DICOM API Call → LibOrthancAuthorization Plugin → Auth-Service Query → Role Check → Allow/Deny
```

### 3. Auth-Service Role Mapping
Our custom Auth-Service serves as the bridge between Authelia sessions and the plugin's hardcoded roles:

- **Role Translation**: Maps Authelia groups (`admin`, `doctor`, `external`) to plugin roles (`admin-role`, `doctor-role`, `external-role`)
- **Per-Request Validation**: Called by the plugin for **every single DICOM API operation**
- **Token Validation**: Also validates sharing tokens for external access
- **Permission Logic**: Implements the business rules for each predefined role
- **Audit Logging**: Records all permission checks and access attempts

### 4. Plugin Integration
The LibOrthancAuthorization plugin:
- **Intercepts**: Every DICOM API call before processing
- **Expects**: Specific role names hardcoded in its source code
- **Queries**: Our Auth-Service for each request validation
- **Enforces**: Permissions based on the returned role

## User Management

Users are managed **from the administration panel**, under
`https://<your-domain>/console/`, Users tab. The panel is the only path that
enforces the invariants: an argon2id hash, at least one active administrator,
and an audit trail entry for every change.

### Adding, changing, removing

Everything happens in the Users tab: create an account, change its display
name, its email or its groups, disable it, reset its password, delete it.

Two refusals are deliberate and cannot be worked around:

- the last active administrator can be neither deleted, nor disabled, nor
  removed from the `admin` group -- the stack would be left with nobody able
  to administer it;
- the first-run wizard closes for good once finalised, so it cannot be used
  to create a second administrator.

### If the panel is unreachable

`./manage-authelia-users.sh`, at the root of the repository, does the same
work from a console on the host. It edits `users_database.yml` directly and
enforces none of the invariants above: keep it for the case where you are
locked out, not for day-to-day management.

Editing `users_database.yml` by hand is the last resort. Authelia refuses to
start on a password that is not a valid argon2id hash ("argon2 decode
error"), and on a file without users ("users: non zero value required").
