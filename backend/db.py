"""
Database models and initialization using JSON storage.

Uses JSON files for local storage of users, verification codes, and user-wallet relationships.
"""

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Path relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "users.json"

# Thread lock for concurrent access
_db_lock = threading.Lock()


@dataclass
class User:
    """User account (identified by email)."""
    id: int
    email: str
    created_at: str
    last_login: Optional[str] = None
    wallet_addresses: List[str] = None

    def __post_init__(self):
        if self.wallet_addresses is None:
            self.wallet_addresses = []


@dataclass
class VerificationCode:
    """Email verification codes for passwordless authentication."""
    id: int
    email: str
    code: str
    created_at: str
    used: bool
    expires_at: str


@dataclass
class UserWallet:
    """User-wallet ownership relationship (for compatibility)."""
    user_id: int
    wallet_address: str
    added_at: str


class Database:
    """JSON-based database for users and verification codes."""

    def __init__(self):
        self.users: List[User] = []
        self.verification_codes: List[VerificationCode] = []
        self._next_user_id = 1
        self._next_code_id = 1

    def load(self):
        """Load database from JSON file."""
        if not DB_PATH.exists():
            return

        with _db_lock:
            try:
                with open(DB_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Load users
                self.users = [User(**u) for u in data.get('users', [])]
                if self.users:
                    self._next_user_id = max(u.id for u in self.users) + 1

                # Load verification codes
                self.verification_codes = [
                    VerificationCode(**vc) for vc in data.get('verification_codes', [])
                ]
                if self.verification_codes:
                    self._next_code_id = max(vc.id for vc in self.verification_codes) + 1

            except Exception as e:
                print(f"[DB] Error loading database: {e}")

    def save(self):
        """Save database to JSON file."""
        with _db_lock:
            try:
                DATA_DIR = PROJECT_ROOT / "data"
                DATA_DIR.mkdir(exist_ok=True)

                data = {
                    'users': [asdict(u) for u in self.users],
                    'verification_codes': [asdict(vc) for vc in self.verification_codes]
                }

                with open(DB_PATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

            except Exception as e:
                print(f"[DB] Error saving database: {e}")

    # User operations
    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        for user in self.users:
            if user.email.lower() == email.lower():
                return user
        return None

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        for user in self.users:
            if user.id == user_id:
                return user
        return None

    def create_user(self, email: str) -> User:
        """Create new user."""
        user = User(
            id=self._next_user_id,
            email=email.lower().strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
            wallet_addresses=[]
        )
        self._next_user_id += 1
        self.users.append(user)
        self.save()
        return user

    def update_user_login(self, user: User):
        """Update user's last login time."""
        user.last_login = datetime.now(timezone.utc).isoformat()
        self.save()

    def add_wallet_to_user(self, user: User, wallet_address: str) -> bool:
        """
        Add wallet to user's wallet list.

        Returns:
            True if wallet was added, False if already exists
        """
        wallet_lower = wallet_address.lower()
        if wallet_lower not in [w.lower() for w in user.wallet_addresses]:
            user.wallet_addresses.append(wallet_address)
            self.save()
            return True
        return False

    def remove_wallet_from_user(self, user: User, wallet_address: str) -> bool:
        """
        Remove wallet from user's wallet list.

        Returns:
            True if wallet was removed, False if not found
        """
        wallet_lower = wallet_address.lower()
        # Find and remove wallet (case-insensitive)
        for i, w in enumerate(user.wallet_addresses):
            if w.lower() == wallet_lower:
                user.wallet_addresses.pop(i)
                self.save()
                return True
        return False

    def get_wallet_owner(self, wallet_address: str) -> Optional[User]:
        """Get user who owns this wallet."""
        wallet_lower = wallet_address.lower()
        for user in self.users:
            if wallet_lower in [w.lower() for w in user.wallet_addresses]:
                return user
        return None

    # Verification code operations
    def create_verification_code(self, email: str, code: str, expires_at: datetime) -> VerificationCode:
        """Create new verification code."""
        vcode = VerificationCode(
            id=self._next_code_id,
            email=email.lower().strip(),
            code=code,
            created_at=datetime.now(timezone.utc).isoformat(),
            used=False,
            expires_at=expires_at.isoformat()
        )
        self._next_code_id += 1
        self.verification_codes.append(vcode)
        self.save()
        return vcode

    def invalidate_codes_for_email(self, email: str):
        """Mark all unused codes for email as used."""
        changed = False
        for vcode in self.verification_codes:
            if vcode.email.lower() == email.lower() and not vcode.used:
                vcode.used = True
                changed = True
        if changed:
            self.save()

    def get_valid_code(self, email: str, code: str) -> Optional[VerificationCode]:
        """Get valid (unused, not expired) verification code."""
        now = datetime.now(timezone.utc)
        for vcode in self.verification_codes:
            if (vcode.email.lower() == email.lower() and
                vcode.code == code and
                not vcode.used and
                datetime.fromisoformat(vcode.expires_at) > now):
                return vcode
        return None

    def mark_code_used(self, vcode: VerificationCode):
        """Mark verification code as used."""
        vcode.used = True
        self.save()

    # Cleanup old codes (optional utility)
    def cleanup_old_codes(self, days: int = 7):
        """Remove verification codes older than N days."""
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        original_count = len(self.verification_codes)

        self.verification_codes = [
            vc for vc in self.verification_codes
            if datetime.fromisoformat(vc.created_at).timestamp() > cutoff
        ]

        if len(self.verification_codes) < original_count:
            self.save()
            print(f"[DB] Cleaned up {original_count - len(self.verification_codes)} old verification codes")


# Global database instance
_db_instance: Optional[Database] = None


def get_database() -> Database:
    """Get global database instance (singleton)."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        _db_instance.load()
    return _db_instance


def init_db():
    """Initialize database (create file if doesn't exist)."""
    DATA_DIR = PROJECT_ROOT / "data"
    DATA_DIR.mkdir(exist_ok=True)

    db = get_database()
    db.save()
    print(f"[DB] Database initialized at {DB_PATH}")


# FastAPI dependency (compatibility with old code)
def get_db():
    """
    Get database session (for FastAPI Depends).

    Yields database instance for compatibility with old SQLAlchemy pattern.
    """
    db = get_database()
    try:
        yield db
    finally:
        pass  # No cleanup needed for JSON database
