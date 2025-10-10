#!/bin/bash
# =============================================================================
# AUTHELIA USER MANAGEMENT SCRIPT
# =============================================================================
# Simple script to create and manage Authelia users
# =============================================================================

set -e

echo "AUTHELIA User Management"
echo "========================"
echo

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Function to prompt for input with default value
prompt_input() {
    local prompt="$1"
    local default="$2"
    local value

    if [[ -n "$default" ]]; then
        read -p "$prompt (default: $default): " value
        echo "${value:-$default}"
    else
        read -p "$prompt: " value
        echo "$value"
    fi
}

# Function to prompt for password
prompt_password() {
    local prompt="$1"
    local password

    while true; do
        >&2 echo "$prompt (input hidden):"
        read -s password
        >&2 echo

        if [[ -n "$password" ]]; then
            break
        fi
        >&2 echo "ERROR: Password cannot be empty. Please try again."
    done

    echo "$password"
}

# Function to hash password using Docker
hash_password() {
    local password=$1
    docker run --rm authelia/authelia:latest authelia crypto hash generate argon2 --password "$password" 2>/dev/null | grep "Digest: " | cut -d' ' -f2
}

# =============================================================================
# MAIN PROCESS
# =============================================================================

# Check if users_database.yml already exists
if [[ -f "services/authelia/config/users_database.yml" ]]; then
    echo "WARNING: users_database.yml already exists!"
    read -p "Overwrite existing file? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Operation cancelled."
        exit 0
    fi
fi

echo "How many users do you want to create?"
NUM_USERS=$(prompt_input "Number of users" "3")

# Create users_database.yml header
cat > services/authelia/config/users_database.yml << 'EOF'
---
users:
EOF

# Collect user information
for i in $(seq 1 $NUM_USERS); do
    echo
    echo "User $i/$NUM_USERS"
    echo "----------"

    USER_EMAIL=$(prompt_input "Email")
    USER_NAME=$(prompt_input "Display name" "User $i")
    USER_PASSWORD=$(prompt_password "Password")

    echo "Available groups: admin, doctor, external, user"
    USER_GROUP=$(prompt_input "Group" "user")

    echo "Hashing password..."
    HASHED_PASSWORD=$(hash_password "$USER_PASSWORD")

    # Append user to file
    cat >> services/authelia/config/users_database.yml << EOF
  ${USER_EMAIL}:
    displayname: "${USER_NAME}"
    password: "${HASHED_PASSWORD}"
    email: ${USER_EMAIL}
    groups:
      - ${USER_GROUP}

EOF
done

echo
echo "Users created successfully!"
echo "=========================="
echo
echo "Users database saved to: services/authelia/config/users_database.yml"
echo
echo "Next steps:"
echo "1. Restart Authelia: docker-compose restart authelia"
echo "2. Users can now login at: https://pacs.yokoinc.ovh/auth"
echo
