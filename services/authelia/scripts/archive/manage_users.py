#!/usr/bin/env python3
# =============================================================================
# AUTHELIA USER MANAGEMENT SCRIPT FOR ORTHANC-AUTHELIA
# =============================================================================
# Comprehensive user management for Authelia authentication in PACS environment
# Usage: python3 manage_users.py [command] [options]
#
# Commands:
#   init                    - Initialize database with default users
#   add <email> <password>  - Add new user
#   delete <email>          - Delete user
#   password <email> <new>  - Change user password
#   list                    - List all users

import argparse
import yaml
import subprocess
import sys
import os
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================
USERS_FILE = "services/authelia/config/users_database.yml"
CONTAINER_NAME = "orthanc-authelia"

# =============================================================================
# PASSWORD HASH GENERATION
# =============================================================================
def generate_password_hash(password):
    """
    Generate Argon2ID hash for a password using Authelia's crypto utility
    
    Args:
        password (str): Plain text password to hash
        
    Returns:
        str: Argon2ID hash or None if error
    """
    try:
        # Use Authelia's built-in crypto command for hash generation
        result = subprocess.run([
            "docker", "run", "--rm", "authelia/authelia:4.39.5",
            "authelia", "crypto", "hash", "generate", "argon2",
            "--password", password
        ], capture_output=True, text=True, check=True)
        
        # Extract hash from command output
        for line in result.stdout.split('\n'):
            if line.startswith('Digest: '):
                return line.replace('Digest: ', '').strip()
        
        raise Exception("Hash not found in output")
        
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Error generating password hash: {e}")
        return None

# =============================================================================
# USER DATABASE OPERATIONS
# =============================================================================
def load_users():
    """
    Load users from users_database.yml file
    
    Returns:
        dict: User database structure
    """
    if not os.path.exists(USERS_FILE):
        return {"users": {}}
    
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {"users": {}}

def save_users(data):
    """
    Save users to users_database.yml file
    
    Args:
        data (dict): User database structure to save
    """
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

def restart_authelia():
    """
    Restart Authelia container to reload user database
    """
    try:
        subprocess.run(["docker", "restart", CONTAINER_NAME], check=True)
        print("Authelia restarted successfully")
    except subprocess.CalledProcessError:
        print("ERROR: Error restarting Authelia container")

# =============================================================================
# USER MANAGEMENT COMMANDS
# =============================================================================
def init_database():
    """
    Initialize user database with default PACS users
    Creates admin, doctor, and external user accounts
    """
    print("Initializing user database...")
    
    # Default users for PACS environment with different access levels
    default_users = {
        "users": {
            "admin@example.com": {
                "displayname": "PACS Administrator",         # Full system access
                "password": generate_password_hash("admin123"),
                "email": "admin@example.com",
                "groups": ["admin"]
            },
            "doctor@example.com": {
                "displayname": "Medical Doctor",              # Medical access
                "password": generate_password_hash("doctor123"), 
                "email": "doctor@example.com",
                "groups": ["doctor"]
            },
            "external@example.com": {
                "displayname": "External User",               # Limited access
                "password": generate_password_hash("external123"),
                "email": "external@example.com", 
                "groups": ["external"]
            }
        }
    }
    
    save_users(default_users)
    print("Database initialized with default users")
    print("Created users:")
    print("   - admin@example.com / admin123 (group: admin)")
    print("   - doctor@example.com / doctor123 (group: doctor)")
    print("   - external@example.com / external123 (group: external)")
    print("WARNING: Change default passwords in production!")
    
    restart_authelia()

def add_user(email, password, displayname, groups):
    """
    Add a new user to the database
    
    Args:
        email (str): User email address
        password (str): User password
        displayname (str): Display name for the user
        groups (str): Comma-separated list of groups
    """
    print(f"Adding user {email}...")
    
    # Validate email format
    if not email or '@' not in email:
        print("ERROR: Invalid email address")
        return
    
    # Validate password strength
    if not password or len(password) < 6:
        print("ERROR: Password too short (minimum 6 characters)")
        return
    
    # Validate groups against PACS roles
    valid_groups = ["admin", "doctor", "external"]
    group_list = [g.strip() for g in groups.split(',')]
    
    for group in group_list:
        if group not in valid_groups:
            print(f"ERROR: Invalid group: {group}. Valid groups: {valid_groups}")
            return
    
    data = load_users()
    
    # Check if user already exists
    if email in data["users"]:
        print(f"ERROR: User {email} already exists")
        return
    
    # Generate secure password hash
    password_hash = generate_password_hash(password)
    if not password_hash:
        print("ERROR: Error generating password hash")
        return
    
    # Add user to database
    data["users"][email] = {
        "displayname": displayname or email,
        "password": password_hash,
        "email": email,
        "groups": group_list
    }
    
    save_users(data)
    print(f"User {email} added successfully")
    print(f"   - Name: {displayname or email}")
    print(f"   - Groups: {', '.join(group_list)}")
    
    restart_authelia()

def delete_user(email):
    """
    Delete a user from the database
    
    Args:
        email (str): Email of user to delete
    """
    print(f"Deleting user {email}...")
    
    data = load_users()
    
    if email not in data["users"]:
        print(f"ERROR: User {email} does not exist")
        return
    
    del data["users"][email]
    save_users(data)
    print(f"User {email} deleted successfully")
    
    restart_authelia()

def change_password(email, new_password):
    """
    Change password for an existing user
    
    Args:
        email (str): User email
        new_password (str): New password
    """
    print(f"Changing password for {email}...")
    
    # Validate password strength
    if len(new_password) < 6:
        print("ERROR: Password too short (minimum 6 characters)")
        return
    
    data = load_users()
    
    if email not in data["users"]:
        print(f"ERROR: User {email} does not exist")
        return
    
    # Generate new password hash
    password_hash = generate_password_hash(new_password)
    if not password_hash:
        print("ERROR: Error generating password hash")
        return
    
    data["users"][email]["password"] = password_hash
    save_users(data)
    print(f"Password changed for {email}")
    
    restart_authelia()

def list_users():
    """
    List all users in the database with their details
    """
    print("User list:")
    
    data = load_users()
    
    if not data["users"]:
        print("   No users found")
        return
    
    # Display users with their roles and access levels
    for email, user_data in data["users"].items():
        groups = ", ".join(user_data.get("groups", []))
        displayname = user_data.get("displayname", "")
        print(f"   {email}")
        print(f"      Name: {displayname}")
        print(f"      Groups: {groups}")
        
        # Show access level description
        if "admin" in user_data.get("groups", []):
            print(f"      Access: Full PACS administration")
        elif "doctor" in user_data.get("groups", []):
            print(f"      Access: Medical imaging and patient data")
        elif "external" in user_data.get("groups", []):
            print(f"      Access: Limited read-only access")
        print()

# =============================================================================
# COMMAND LINE INTERFACE
# =============================================================================
def main():
    """
    Main function - parse command line arguments and execute commands
    """
    parser = argparse.ArgumentParser(
        description="Authelia User Manager for Orthanc PACS",
        epilog="Examples:\n"
               "  python3 manage_users.py init\n"
               "  python3 manage_users.py add user@hospital.com password123 --name 'Dr. Smith' --groups doctor\n"
               "  python3 manage_users.py list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Initialize database command
    subparsers.add_parser('init', 
                         help='Initialize database with default PACS users')
    
    # Add user command
    add_parser = subparsers.add_parser('add', 
                                      help='Add new user to the system')
    add_parser.add_argument('email', help='User email address')
    add_parser.add_argument('password', help='User password')
    add_parser.add_argument('--name', help='Display name', default='')
    add_parser.add_argument('--groups', help='Comma-separated groups (admin,doctor,external)', 
                           default='external')
    
    # Delete user command
    del_parser = subparsers.add_parser('delete', 
                                      help='Delete user from the system')
    del_parser.add_argument('email', help='Email of user to delete')
    
    # Change password command
    pwd_parser = subparsers.add_parser('password', 
                                      help='Change user password')
    pwd_parser.add_argument('email', help='User email address')
    pwd_parser.add_argument('new_password', help='New password')
    
    # List users command
    subparsers.add_parser('list', 
                         help='List all users and their access levels')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    print(f"Authelia User Manager - {args.command.upper()}")
    print("=" * 50)
    
    # Execute requested command
    if args.command == 'init':
        init_database()
    elif args.command == 'add':
        add_user(args.email, args.password, args.name, args.groups)
    elif args.command == 'delete':
        confirmation = input(f"Are you sure you want to delete {args.email}? (y/N): ")
        if confirmation.lower() == 'y':
            delete_user(args.email)
        else:
            print("Deletion cancelled")
    elif args.command == 'password':
        change_password(args.email, args.new_password)
    elif args.command == 'list':
        list_users()

if __name__ == "__main__":
    main()