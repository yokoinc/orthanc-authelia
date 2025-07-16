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

### Adding Users

#### Method 1: Using Default Setup Script (Recommended)
```bash
./setup.sh
```
The default setup script will prompt for three users with predefined roles:
- Admin user with `admin` role
- Doctor user with `doctor` role  
- External user with `external` role

The script automatically generates the `users_database.yml` file with hashed passwords.

#### Method 2: Using Management Script
```bash
cd services/authelia/scripts
python3 manage_users.py add user@example.com password123 --name "Dr. Smith" --groups doctor
```


### Removing Users

#### Method 1: Using Management Script
```bash
python3 manage_users.py delete user@example.com
```

#### Method 2: Manual Configuration
1. Remove user entry from `users_database.yml`
2. Restart Authelia container

### Changing Passwords

#### Method 1: Using Management Script
```bash
python3 manage_users.py password user@example.com newpassword123
```

#### Method 2: Manual Configuration
1. Generate new password hash:
```bash
docker run --rm authelia/authelia:latest authelia crypto hash generate argon2 --password "newpassword"
```

2. Update password in `users_database.yml`
3. Restart Authelia container

## Management Commands

### List All Users
```bash
python3 manage_users.py list
```

### Initialize Default Users
```bash
python3 manage_users.py init
```

### User Management Script Help
```bash
python3 manage_users.py --help
```

