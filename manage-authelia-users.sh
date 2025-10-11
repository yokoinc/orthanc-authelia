#!/bin/bash
# =============================================================================
# AUTHELIA USER MANAGEMENT - INTERACTIVE TOOL
# =============================================================================
# Complete interactive tool for managing Authelia users in PACS environment
# Features: Add, Modify, Delete, List users, Active sessions, Statistics
# =============================================================================

set -e

# Colors for better UX
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Configuration
USERS_FILE="services/authelia/config/users_database.yml"
CONTAINER_NAME="orthanc-authelia"
REDIS_CONTAINER="orthanc-redis"
AUTHELIA_VERSION="4.39.5"

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

print_header() {
    clear
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}       ${BLUE}AUTHELIA USER MANAGEMENT - ORTHANC PACS${NC}           ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

prompt_input() {
    local prompt="$1"
    local default="$2"
    local value

    if [[ -n "$default" ]]; then
        read -p "$(echo -e ${CYAN}${prompt}${NC}) [${default}]: " value
        echo "${value:-$default}"
    else
        read -p "$(echo -e ${CYAN}${prompt}${NC}): " value
        echo "$value"
    fi
}

prompt_password() {
    local prompt="$1"
    local password
    local confirm

    while true; do
        echo -e "${CYAN}${prompt}${NC} (hidden):"
        read -s password
        echo

        if [[ -z "$password" ]]; then
            print_error "Password cannot be empty"
            continue
        fi

        if [[ ${#password} -lt 8 ]]; then
            print_error "Password must be at least 8 characters"
            continue
        fi

        echo -e "${CYAN}Confirm password${NC} (hidden):"
        read -s confirm
        echo

        if [[ "$password" == "$confirm" ]]; then
            echo "$password"
            return
        else
            print_error "Passwords do not match. Try again."
        fi
    done
}

generate_random_password() {
    # Generate a secure 16-character password
    LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*' < /dev/urandom | head -c 16
}

hash_password() {
    local password=$1
    local hash

    print_info "Hashing password (this may take a few seconds)..."
    hash=$(docker run --rm authelia/authelia:${AUTHELIA_VERSION} \
        authelia crypto hash generate argon2 --password "$password" 2>/dev/null \
        | grep "Digest: " | cut -d' ' -f2)

    if [[ -z "$hash" ]]; then
        print_error "Failed to generate password hash"
        return 1
    fi

    echo "$hash"
}

restart_authelia() {
    print_info "Restarting Authelia container..."
    if docker restart ${CONTAINER_NAME} &>/dev/null; then
        print_success "Authelia restarted successfully"
        sleep 2
    else
        print_warning "Could not restart Authelia (container might not be running)"
    fi
}

validate_email() {
    local email=$1
    if [[ "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        return 0
    else
        return 1
    fi
}

# =============================================================================
# USER DATABASE OPERATIONS
# =============================================================================

check_users_file() {
    if [[ ! -f "$USERS_FILE" ]]; then
        print_warning "Users database not found. Creating new database..."
        mkdir -p "$(dirname "$USERS_FILE")"
        cat > "$USERS_FILE" << 'EOF'
---
users:
EOF
        print_success "Created empty users database"
    fi
}

user_exists() {
    local email=$1
    check_users_file
    grep -q "^  ${email}:" "$USERS_FILE"
}

get_user_info() {
    local email=$1

    # Use awk to extract only the user block
    awk -v email="$email" '
        BEGIN { in_user = 0 }
        $0 ~ "^  " email ":" { in_user = 1 }
        in_user && /^  [a-zA-Z0-9]/ && $0 !~ email { exit }
        in_user { print }
        in_user && /^$/ { exit }
    ' "$USERS_FILE"
}

count_users() {
    check_users_file
    grep -c "^  [a-zA-Z0-9].*@.*:" "$USERS_FILE" || echo "0"
}

# =============================================================================
# REDIS SESSION FUNCTIONS
# =============================================================================

get_active_sessions() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
        return 1
    fi

    # Get all session keys from Redis
    docker exec ${REDIS_CONTAINER} redis-cli KEYS "authelia:session:*" 2>/dev/null || echo ""
}

get_session_info() {
    local session_key=$1
    docker exec ${REDIS_CONTAINER} redis-cli GET "$session_key" 2>/dev/null || echo ""
}

count_active_sessions() {
    local sessions=$(get_active_sessions)
    if [[ -z "$sessions" ]]; then
        echo "0"
    else
        echo "$sessions" | wc -l
    fi
}

# =============================================================================
# LOG ANALYSIS FUNCTIONS
# =============================================================================

get_last_login_time() {
    local email=$1

    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "N/A"
        return
    fi

    # Extract last successful login from logs
    local last_login=$(docker logs ${CONTAINER_NAME} 2>&1 | \
        grep -i "successful" | \
        grep -i "$email" | \
        tail -1 | \
        awk '{print $1, $2}')

    if [[ -n "$last_login" ]]; then
        echo "$last_login"
    else
        echo "Never"
    fi
}

get_failed_login_count() {
    local email=$1

    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "0"
        return
    fi

    docker logs ${CONTAINER_NAME} 2>&1 | \
        grep -i "authentication.*failed" | \
        grep -i "$email" | \
        wc -l
}

# =============================================================================
# MENU FUNCTIONS
# =============================================================================

show_statistics() {
    print_header
    echo -e "${BLUE}═══ SYSTEM STATISTICS ═══${NC}"
    echo

    # User statistics
    local total_users=$(count_users)
    local admin_count=$(grep -c "groups:" "$USERS_FILE" 2>/dev/null | xargs -I{} grep -c "- admin" "$USERS_FILE" 2>/dev/null || echo "0")
    local doctor_count=$(grep -c "- doctor" "$USERS_FILE" 2>/dev/null || echo "0")
    local external_count=$(grep -c "- external" "$USERS_FILE" 2>/dev/null || echo "0")

    echo -e "${CYAN}📊 User Statistics:${NC}"
    echo -e "   Total users:    ${MAGENTA}${total_users}${NC}"
    echo -e "   Administrators: ${RED}${admin_count}${NC}"
    echo -e "   Doctors:        ${GREEN}${doctor_count}${NC}"
    echo -e "   External:       ${YELLOW}${external_count}${NC}"
    echo

    # Session statistics
    local active_sessions=$(count_active_sessions)
    echo -e "${CYAN}🔐 Session Statistics:${NC}"
    echo -e "   Active sessions: ${GREEN}${active_sessions}${NC}"
    echo

    # Container status
    echo -e "${CYAN}🐳 Container Status:${NC}"
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        local uptime=$(docker inspect -f '{{.State.StartedAt}}' ${CONTAINER_NAME} 2>/dev/null || echo "Unknown")
        echo -e "   Authelia:  ${GREEN}Running${NC} (since ${uptime})"
    else
        echo -e "   Authelia:  ${RED}Stopped${NC}"
    fi

    if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
        echo -e "   Redis:     ${GREEN}Running${NC}"
    else
        echo -e "   Redis:     ${RED}Stopped${NC}"
    fi

    echo
    read -p "Press Enter to continue..."
}

show_active_sessions() {
    print_header
    echo -e "${BLUE}═══ ACTIVE SESSIONS ═══${NC}"
    echo

    local sessions=$(get_active_sessions)

    if [[ -z "$sessions" ]]; then
        print_warning "No active sessions found or Redis not available"
        echo
        read -p "Press Enter to continue..."
        return
    fi

    local count=0
    while IFS= read -r session_key; do
        count=$((count + 1))

        # Extract session ID from key
        local session_id=$(echo "$session_key" | sed 's/authelia:session://')

        # Get session data
        local session_data=$(get_session_info "$session_key")

        # Try to extract username from session data (JSON format)
        local username=$(echo "$session_data" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)

        if [[ -z "$username" ]]; then
            username="Unknown"
        fi

        echo -e "${CYAN}${count}.${NC} Session: ${session_id:0:16}..."
        echo -e "   User: ${GREEN}${username}${NC}"
        echo
    done <<< "$sessions"

    if [[ $count -eq 0 ]]; then
        print_warning "No active sessions"
    else
        print_success "Total active sessions: ${count}"
    fi

    echo
    read -p "Press Enter to continue..."
}

list_users() {
    print_header
    echo -e "${BLUE}═══ USER LIST ═══${NC}"
    echo

    check_users_file

    if ! grep -q "^  [a-zA-Z0-9].*@" "$USERS_FILE"; then
        print_warning "No users found in database"
        echo
        read -p "Press Enter to continue..."
        return
    fi

    local count=0
    while IFS= read -r line; do
        # Only match lines with email addresses (containing @)
        if [[ "$line" =~ ^[[:space:]]{2}([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}): ]]; then
            count=$((count + 1))
            local email="${BASH_REMATCH[1]}"

            # Extract user details
            local info=$(get_user_info "$email")
            local displayname=$(echo "$info" | grep "displayname:" | sed 's/.*displayname: "\(.*\)"/\1/')
            local groups=$(echo "$info" | grep -A1 "groups:" | tail -n1 | sed 's/.*- //')

            # Color code by group
            local group_color="${NC}"
            local group_icon=""
            case "$groups" in
                admin)
                    group_color="${RED}"
                    group_icon="👑"
                    ;;
                doctor)
                    group_color="${GREEN}"
                    group_icon="👨‍⚕️"
                    ;;
                external)
                    group_color="${YELLOW}"
                    group_icon="👤"
                    ;;
            esac

            echo -e "${CYAN}${count}.${NC} ${email}"
            echo -e "   Name:       ${displayname}"
            echo -e "   Group:      ${group_icon} ${group_color}${groups}${NC}"

            # Show access level
            case "$groups" in
                admin) echo -e "   Access:     ${RED}Full PACS administration${NC}" ;;
                doctor) echo -e "   Access:     ${GREEN}Medical imaging and patient data${NC}" ;;
                external) echo -e "   Access:     ${YELLOW}Limited read-only access${NC}" ;;
            esac

            # Show last login (if available)
            local last_login=$(get_last_login_time "$email")
            if [[ "$last_login" != "N/A" ]]; then
                if [[ "$last_login" == "Never" ]]; then
                    echo -e "   Last login: ${YELLOW}Never${NC}"
                else
                    echo -e "   Last login: ${GREEN}${last_login}${NC}"
                fi
            fi

            echo
        fi
    done < "$USERS_FILE"

    if [[ $count -eq 0 ]]; then
        print_warning "No users found"
    else
        print_info "Total users: ${count}"
    fi

    echo
    read -p "Press Enter to continue..."
}

add_user() {
    print_header
    echo -e "${BLUE}═══ ADD NEW USER ═══${NC}"
    echo

    # Get user email
    local email
    while true; do
        email=$(prompt_input "Email address")
        if ! validate_email "$email"; then
            print_error "Invalid email format"
            continue
        fi
        if user_exists "$email"; then
            print_error "User already exists: $email"
            read -p "Try another email? (y/N): " retry
            [[ "$retry" =~ ^[Yy]$ ]] || return
            continue
        fi
        break
    done

    # Get display name
    local displayname=$(prompt_input "Display name" "$(echo $email | cut -d@ -f1)")

    # Get group
    echo
    echo -e "${BLUE}Available groups:${NC}"
    echo -e "  ${RED}1${NC}. admin    👑 Full PACS administration"
    echo -e "  ${GREEN}2${NC}. doctor   👨‍⚕️ Medical imaging and patient data"
    echo -e "  ${YELLOW}3${NC}. external 👤 Limited read-only access"
    echo

    local group_choice
    while true; do
        group_choice=$(prompt_input "Select group" "2")
        case "$group_choice" in
            1|admin) local group="admin"; break ;;
            2|doctor) local group="doctor"; break ;;
            3|external) local group="external"; break ;;
            *) print_error "Invalid choice" ;;
        esac
    done

    # Get password
    echo
    echo -e "${BLUE}Password options:${NC}"
    echo "  1. Enter password manually"
    echo "  2. Generate secure random password"
    echo

    local pwd_choice=$(prompt_input "Select option" "1")
    local password
    local show_password=false

    case "$pwd_choice" in
        2|random|auto)
            password=$(generate_random_password)
            show_password=true
            print_success "Generated secure password: ${MAGENTA}${password}${NC}"
            print_warning "Save this password - it won't be shown again!"
            echo
            ;;
        *)
            password=$(prompt_password "Enter password")
            ;;
    esac

    # Hash password
    echo
    local hash=$(hash_password "$password")
    if [[ -z "$hash" ]]; then
        print_error "Failed to create user"
        read -p "Press Enter to continue..."
        return 1
    fi

    # Add user to database
    cat >> "$USERS_FILE" << EOF
  ${email}:
    displayname: "${displayname}"
    password: "${hash}"
    email: ${email}
    groups:
      - ${group}

EOF

    echo
    print_success "User created successfully!"
    echo
    echo -e "Email:  ${CYAN}${email}${NC}"
    echo -e "Name:   ${displayname}"
    echo -e "Group:  ${group}"
    if $show_password; then
        echo -e "Password: ${MAGENTA}${password}${NC}"
    fi
    echo

    restart_authelia

    read -p "Press Enter to continue..."
}

modify_user() {
    print_header
    echo -e "${BLUE}═══ MODIFY USER ═══${NC}"
    echo

    local email=$(prompt_input "User email to modify")

    if ! user_exists "$email"; then
        print_error "User not found: $email"
        read -p "Press Enter to continue..."
        return 1
    fi

    # Show current user info
    echo
    print_info "Current user information:"
    get_user_info "$email" | grep -v "password:"
    echo

    echo -e "${BLUE}What would you like to modify?${NC}"
    echo "  1. Display name"
    echo "  2. Reset password"
    echo "  3. Generate new random password"
    echo "  4. Change group"
    echo "  5. Cancel"
    echo

    local choice=$(prompt_input "Select option" "2")

    case "$choice" in
        1)
            local new_name=$(prompt_input "New display name")
            sed -i "s/^\(  ${email}:.*\n.*displayname:\).*/\1 \"${new_name}\"/" "$USERS_FILE"
            print_success "Display name updated"
            ;;
        2)
            local new_password=$(prompt_password "New password")
            local hash=$(hash_password "$new_password")
            if [[ -n "$hash" ]]; then
                # Find and replace password line
                awk -v email="$email" -v hash="$hash" '
                    BEGIN { in_user = 0 }
                    /^  [a-zA-Z0-9]/ { in_user = 0 }
                    $0 ~ "^  " email ":" { in_user = 1 }
                    in_user && /password:/ { print "    password: \"" hash "\""; next }
                    { print }
                ' "$USERS_FILE" > "$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
                print_success "Password updated"
            else
                print_error "Failed to update password"
            fi
            ;;
        3)
            local new_password=$(generate_random_password)
            echo
            print_success "Generated password: ${MAGENTA}${new_password}${NC}"
            print_warning "Save this password - it won't be shown again!"
            echo

            local hash=$(hash_password "$new_password")
            if [[ -n "$hash" ]]; then
                awk -v email="$email" -v hash="$hash" '
                    BEGIN { in_user = 0 }
                    /^  [a-zA-Z0-9]/ { in_user = 0 }
                    $0 ~ "^  " email ":" { in_user = 1 }
                    in_user && /password:/ { print "    password: \"" hash "\""; next }
                    { print }
                ' "$USERS_FILE" > "$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
                print_success "Password updated with generated password"
            else
                print_error "Failed to update password"
            fi
            ;;
        4)
            echo
            echo "  1. admin    👑"
            echo "  2. doctor   👨‍⚕️"
            echo "  3. external 👤"
            local group_choice=$(prompt_input "Select new group" "2")
            case "$group_choice" in
                1|admin) local new_group="admin" ;;
                2|doctor) local new_group="doctor" ;;
                3|external) local new_group="external" ;;
                *) print_error "Invalid choice"; read -p "Press Enter to continue..."; return ;;
            esac

            # Replace group
            awk -v email="$email" -v group="$new_group" '
                BEGIN { in_user = 0; in_groups = 0 }
                /^  [a-zA-Z0-9]/ { in_user = 0; in_groups = 0 }
                $0 ~ "^  " email ":" { in_user = 1 }
                in_user && /groups:/ { in_groups = 1; print; next }
                in_groups && /^      - / { print "      - " group; in_groups = 0; next }
                { print }
            ' "$USERS_FILE" > "$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
            print_success "Group updated to: $new_group"
            ;;
        5|*)
            print_info "Cancelled"
            read -p "Press Enter to continue..."
            return
            ;;
    esac

    echo
    restart_authelia
    read -p "Press Enter to continue..."
}

delete_user() {
    print_header
    echo -e "${BLUE}═══ DELETE USER ═══${NC}"
    echo

    local email=$(prompt_input "User email to delete")

    if ! user_exists "$email"; then
        print_error "User not found: $email"
        read -p "Press Enter to continue..."
        return 1
    fi

    # Show user info
    echo
    print_warning "User to be deleted:"
    get_user_info "$email" | grep -v "password:"
    echo

    read -p "$(echo -e ${RED}Are you sure you want to delete this user? \(y/N\):${NC}) " confirm

    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        print_info "Deletion cancelled"
        read -p "Press Enter to continue..."
        return
    fi

    # Delete user (remove user block from file)
    awk -v email="$email" '
        BEGIN { in_user = 0; skip = 0 }
        /^  [a-zA-Z0-9]/ { in_user = 0; skip = 0 }
        $0 ~ "^  " email ":" { in_user = 1; skip = 1; next }
        in_user && /^    / { skip = 1; next }
        in_user && /^$/ { skip = 1; next }
        !skip { print }
    ' "$USERS_FILE" > "$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"

    echo
    print_success "User deleted: $email"
    echo

    restart_authelia
    read -p "Press Enter to continue..."
}

# =============================================================================
# MAIN MENU
# =============================================================================

main_menu() {
    while true; do
        print_header

        check_users_file

        # Show quick stats
        local total_users=$(count_users)
        local active_sessions=$(count_active_sessions)
        echo -e "${BLUE}Quick Stats:${NC} ${MAGENTA}${total_users}${NC} users | ${GREEN}${active_sessions}${NC} active sessions"
        echo

        echo -e "${BLUE}MAIN MENU${NC}"
        echo
        echo "  1. List all users"
        echo "  2. Add new user"
        echo "  3. Modify user"
        echo "  4. Delete user"
        echo "  5. View active sessions"
        echo "  6. System statistics"
        echo "  7. Exit"
        echo

        choice=$(prompt_input "Select option" "1")

        case "$choice" in
            1) list_users ;;
            2) add_user ;;
            3) modify_user ;;
            4) delete_user ;;
            5) show_active_sessions ;;
            6) show_statistics ;;
            7|q|Q|exit)
                echo
                print_success "Goodbye!"
                exit 0
                ;;
            *)
                print_error "Invalid option"
                sleep 1
                ;;
        esac
    done
}

# =============================================================================
# ENTRY POINT
# =============================================================================

# Check if running from correct directory
if [[ ! -d "services/authelia" ]]; then
    print_error "Please run this script from the orthanc-authelia root directory"
    exit 1
fi

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed or not in PATH"
    exit 1
fi

# Start main menu
main_menu
