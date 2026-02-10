"""
Migrate user data from SQLite database to JSON format.

This script reads data from users.db (SQLite) and converts it to users.json format.
Run this once during migration from SQLAlchemy to JSON storage.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
SQLITE_DB = PROJECT_ROOT / "data" / "users.db"
JSON_DB = PROJECT_ROOT / "data" / "users.json"


def migrate():
    """Migrate data from SQLite to JSON."""
    if not SQLITE_DB.exists():
        print(f"[Migrate] SQLite database not found: {SQLITE_DB}")
        return

    print(f"[Migrate] Reading from {SQLITE_DB}")

    # Connect to SQLite
    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row  # Access columns by name
    cursor = conn.cursor()

    # Read users
    users_data = []
    cursor.execute("SELECT * FROM users ORDER BY id")
    for row in cursor.fetchall():
        # Get user's wallets
        wallet_cursor = conn.cursor()
        wallet_cursor.execute(
            "SELECT wallet_address FROM user_wallets WHERE user_id = ? ORDER BY added_at",
            (row['id'],)
        )
        wallet_addresses = [w['wallet_address'] for w in wallet_cursor.fetchall()]

        user = {
            'id': row['id'],
            'email': row['email'],
            'created_at': row['created_at'],
            'last_login': row['last_login'],
            'wallet_addresses': wallet_addresses
        }
        users_data.append(user)
        print(f"  OK User {user['id']}: {user['email']} ({len(wallet_addresses)} wallets)")

    # Read verification codes
    codes_data = []
    cursor.execute("SELECT * FROM verification_codes ORDER BY id")
    for row in cursor.fetchall():
        code = {
            'id': row['id'],
            'email': row['email'],
            'code': row['code'],
            'created_at': row['created_at'],
            'used': bool(row['used']),
            'expires_at': row['expires_at']
        }
        codes_data.append(code)

    conn.close()

    # Write to JSON
    output = {
        'users': users_data,
        'verification_codes': codes_data
    }

    JSON_DB.parent.mkdir(exist_ok=True)

    # Backup existing JSON if it exists
    if JSON_DB.exists():
        backup_path = JSON_DB.with_suffix('.json.backup')
        print(f"[Migrate] Backing up existing {JSON_DB} to {backup_path}")
        JSON_DB.rename(backup_path)

    with open(JSON_DB, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[Migrate] OK Migrated {len(users_data)} users and {len(codes_data)} verification codes")
    print(f"[Migrate] OK Saved to {JSON_DB}")
    print()
    print("Migration complete! You can now:")
    print("  1. Delete the old SQLite database: data/users.db")
    print("  2. Remove sqlalchemy from requirements.txt")
    print("  3. Restart your backend server")


if __name__ == "__main__":
    migrate()
