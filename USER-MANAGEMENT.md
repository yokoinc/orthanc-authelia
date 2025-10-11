# Authelia User Management Guide

## Interactive User Management Tool

This project includes a comprehensive interactive tool for managing Authelia users.

### Quick Start

From the project root directory, run:

```bash
./manage-authelia-users.sh
```

### Features

The interactive tool provides a user-friendly menu with the following options:

#### 1. **List All Users**
- View all registered users
- See their display names, groups, and access levels
- Color-coded by role (admin, doctor, external)

#### 2. **Add New User**
- Create a new user account
- Set email, display name, and password
- Assign to appropriate group (admin/doctor/external)
- Automatic password hashing with Argon2ID
- Email validation
- Password strength requirements (minimum 8 characters)
- Password confirmation

#### 3. **Modify User**
- Update user display name
- Change password (with hashing)
- Change user group/role
- View current user information before modification

#### 4. **Delete User**
- Remove user from the system
- Confirmation prompt to prevent accidental deletion
- Shows user information before deletion

### User Groups and Access Levels

The system supports three user groups for PACS environment:

| Group | Access Level | Description |
|-------|--------------|-------------|
| **admin** | Full administration | Complete access to all PACS functions and administration |
| **doctor** | Medical access | Access to medical imaging and patient data |
| **external** | Limited read-only | Restricted access for external users |

### Security Features

- **Argon2ID Password Hashing**: Industry-standard secure password hashing
- **Password Strength**: Minimum 8 characters required
- **Password Confirmation**: Double-entry to prevent typos
- **Email Validation**: Ensures valid email format
- **Automatic Container Restart**: Changes take effect immediately

### File Location

User database is stored at:
```
services/authelia/config/users_database.yml
```

### Requirements

- Docker (for password hashing)
- Bash shell
- Running from project root directory

### Manual Password Generation

If you need to manually hash a password:

```bash
docker run --rm authelia/authelia:4.39.5 \
  authelia crypto hash generate argon2 \
  --password "your-password-here"
```

### Troubleshooting

**Script won't start:**
- Ensure you're in the project root directory
- Check that Docker is installed and running
- Verify the script is executable: `chmod +x manage-authelia-users.sh`

**Changes not taking effect:**
- The script automatically restarts Authelia
- If manual restart needed: `docker restart orthanc-authelia`

**User can't log in:**
- Verify user exists: Run script and choose "List all users"
- Check password was set correctly
- Ensure Authelia container is running: `docker ps | grep authelia`

### Legacy Scripts

Old scripts have been moved to `services/authelia/scripts/archive/` for reference:
- `manage_users.py` - Python CLI version
- `generate_passwords.py` - Password generation utility

These are kept for backward compatibility but the new interactive tool is recommended.

---

**Need help?** Run the script and follow the interactive prompts!
