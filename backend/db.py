"""
Database models and initialization for multi-user authentication.

Uses SQLite for local storage of users, verification codes, and user-wallet relationships.
"""

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone
from pathlib import Path

# Path relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "users.db"

Base = declarative_base()
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


class User(Base):
    """User account (identified by email)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime)

    # Relationships
    wallets = relationship("UserWallet", back_populates="user", cascade="all, delete-orphan")


class VerificationCode(Base):
    """Email verification codes for passwordless authentication."""
    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, index=True)
    code = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    used = Column(Boolean, default=False, index=True)
    expires_at = Column(DateTime, nullable=False)


class UserWallet(Base):
    """User-wallet ownership relationship."""
    __tablename__ = "user_wallets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wallet_address = Column(String, nullable=False)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="wallets")

    # Unique constraint: user can't add same wallet twice
    __table_args__ = (
        __import__('sqlalchemy').UniqueConstraint('user_id', 'wallet_address', name='uix_user_wallet'),
    )


def init_db():
    """Initialize database tables if they don't exist."""
    DATA_DIR = PROJECT_ROOT / "data"
    DATA_DIR.mkdir(exist_ok=True)

    Base.metadata.create_all(engine)
    print(f"[DB] Database initialized at {DB_PATH}")


def get_db():
    """Get database session (for FastAPI Depends)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
