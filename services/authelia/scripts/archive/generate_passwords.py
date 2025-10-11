#!/usr/bin/env python3
# =============================================================================
# AUTHELIA PASSWORD HASH GENERATOR
# =============================================================================
# Generates Argon2ID password hashes for Authelia user database
# Usage: python3 generate_passwords.py
# 
# This script creates secure password hashes that can be used in the
# users_database.yml file for Authelia authentication.

import argon2

# =============================================================================
# ARGON2ID HASHER CONFIGURATION
# =============================================================================
# Configure Argon2ID hasher with same parameters as Authelia configuration
# These parameters must match the settings in configuration.yml
hasher = argon2.PasswordHasher(
    time_cost=1,        # Number of iterations (low for PACS performance)
    memory_cost=128,    # Memory usage in MB
    parallelism=8,      # Number of parallel threads
    hash_len=32,        # Hash output length in bytes
    salt_len=16         # Salt length in bytes
)

# =============================================================================
# DEFAULT PASSWORDS FOR PACS USERS
# =============================================================================
# WARNING: Change these passwords in production!
# These are default credentials for initial setup
passwords = {
    'admin123': 'admin',        # Administrator account
    'doctor123': 'doctor',      # Medical doctor account
    'external123': 'external'   # External user account
}

# =============================================================================
# GENERATE PASSWORD HASHES
# =============================================================================
print("# =============================================================================")
print("# AUTHELIA PASSWORD HASHES")
print("# =============================================================================")
print("# Generated Argon2ID hashes for users_database.yml")
print("# Copy these hashes to the password field for each user")
print()

for password, user in passwords.items():
    hash_result = hasher.hash(password)
    print(f"# User: {user} (password: {password})")
    print(f"{user}: {hash_result}")
    print()

print("# =============================================================================")
print("# SECURITY WARNING")
print("# =============================================================================")
print("# Change default passwords in production environment!")
print("# These hashes should be placed in services/authelia/config/users_database.yml")