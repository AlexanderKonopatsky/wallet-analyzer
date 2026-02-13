"""
Authentication logic for Google OAuth.

Features:
- Google OAuth token verification
- JWT token creation and validation
- FastAPI dependency for protected endpoints
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import jwt
from fastapi import HTTPException, Depends, Header
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from db import get_db, User, Database
from email_service import send_verification_code

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 30

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")


def generate_verification_code() -> str:
    """Generate a random 6-digit verification code."""
    return str(secrets.randbelow(1000000)).zfill(6)


def create_verification_code(db: Database, email: str) -> str:
    """
    Create and send verification code for email.

    Args:
        db: Database instance
        email: User email address

    Returns:
        Generated verification code (6 digits)
    """
    code = generate_verification_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    # Invalidate all previous unused codes for this email
    db.invalidate_codes_for_email(email)

    # Create new code
    db.create_verification_code(email, code, expires_at)

    # Send email
    send_verification_code(email, code)

    return code


def verify_code(db: Database, email: str, code: str) -> Optional[User]:
    """
    Verify code and return/create user.

    Args:
        db: Database instance
        email: User email address
        code: Verification code to verify

    Returns:
        User object if code is valid, None otherwise
    """
    # Find valid code (not used, not expired)
    vcode = db.get_valid_code(email, code)

    if not vcode:
        return None

    # Mark code as used
    db.mark_code_used(vcode)

    # Get or create user
    user = db.get_user_by_email(email)
    if not user:
        print(f"[Auth] Creating new user: {email}")
        user = db.create_user(email)

    # Update last login
    db.update_user_login(user)

    return user


def create_jwt_token(user_id: int) -> str:
    """
    Create JWT token for user.

    Args:
        user_id: User ID

    Returns:
        JWT token string
    """
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRATION_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt_token(token: str) -> Optional[dict]:
    """
    Decode and validate JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        print("[Auth] Token expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"[Auth] Invalid token: {e}")
        return None


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Database = Depends(get_db)
) -> User:
    """
    FastAPI dependency: extract and validate current user from JWT.

    Raises:
        HTTPException: If token is missing, invalid, or expired

    Returns:
        Current authenticated User object
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.replace("Bearer ", "")
    payload = decode_jwt_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.get_user_by_id(payload["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def verify_google_token(token: str) -> Optional[Dict]:
    """
    Verify Google OAuth ID token.

    Args:
        token: Google ID token from frontend

    Returns:
        User info dict with 'email', 'name', 'picture' if valid, None otherwise
    """
    if not GOOGLE_CLIENT_ID:
        print("[Auth] Google OAuth not configured (missing GOOGLE_CLIENT_ID)")
        return None

    try:
        # Verify token with Google
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # Check issuer
        if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
            print("[Auth] Invalid Google token issuer")
            return None

        # Extract user info
        return {
            'email': idinfo['email'],
            'name': idinfo.get('name', ''),
            'picture': idinfo.get('picture', '')
        }

    except ValueError as e:
        print(f"[Auth] Invalid Google token: {e}")
        return None
    except Exception as e:
        print(f"[Auth] Error verifying Google token: {e}")
        return None


def get_or_create_user_from_google(db: Database, google_info: Dict) -> User:
    """
    Get or create user from Google OAuth info.

    Args:
        db: Database instance
        google_info: Dict with 'email', 'name', 'picture' from Google

    Returns:
        User object
    """
    email = google_info['email'].lower().strip()

    # Find existing user
    user = db.get_user_by_email(email)

    if not user:
        # Create new user
        print(f"[Auth] Creating new user from Google: {email}")
        user = db.create_user(email)

    # Update last login
    db.update_user_login(user)

    return user
