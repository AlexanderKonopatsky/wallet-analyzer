import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import (
    create_jwt_token,
    create_verification_code,
    get_current_user,
    get_or_create_user_from_google,
    verify_code,
    verify_google_token,
)
from db import Database, User, get_db
from user_data_store import ensure_user_balance_initialized

router = APIRouter()

DATA_BACKUP_ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.getenv("DATA_BACKUP_ADMIN_EMAILS", "").split(",")
    if email.strip()
}


def can_manage_data_backup(email: str) -> bool:
    """Check whether user can access backup/import management."""
    if not DATA_BACKUP_ADMIN_EMAILS:
        return True
    return email.lower() in DATA_BACKUP_ADMIN_EMAILS


def build_auth_user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "can_manage_data_backup": can_manage_data_backup(user.email),
    }


class RequestCodeRequest(BaseModel):
    """Request body for /api/auth/request-code."""

    email: str


class VerifyCodeRequest(BaseModel):
    """Request body for /api/auth/verify-code."""

    email: str
    code: str


class GoogleAuthRequest(BaseModel):
    """Request body for /api/auth/google."""

    token: str


@router.get("/api/auth/config")
async def auth_config():
    """Return public auth config (Google Client ID) for frontend."""
    from auth import GOOGLE_CLIENT_ID

    return {"google_client_id": GOOGLE_CLIENT_ID or ""}


@router.post("/api/auth/request-code")
async def request_code(body: RequestCodeRequest, db: Database = Depends(get_db)):
    """Send verification code to email."""
    try:
        email = body.email.lower().strip()
        create_verification_code(db, email)
        return {"status": "sent", "email": email}
    except Exception as e:
        print(f"[Auth] Error sending code: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/auth/verify-code")
async def verify_code_endpoint(body: VerifyCodeRequest, db: Database = Depends(get_db)):
    """Verify code and return JWT token."""
    email = body.email.lower().strip()
    user = verify_code(db, email, body.code)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    ensure_user_balance_initialized(user.id)
    token = create_jwt_token(user.id)

    return {
        "token": token,
        "user": build_auth_user_payload(user),
    }


@router.post("/api/auth/google")
async def google_auth(body: GoogleAuthRequest, db: Database = Depends(get_db)):
    """Authenticate with Google OAuth token."""
    google_info = verify_google_token(body.token)

    if not google_info:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user = get_or_create_user_from_google(db, google_info)
    ensure_user_balance_initialized(user.id)

    token = create_jwt_token(user.id)
    return {
        "token": token,
        "user": build_auth_user_payload(user),
    }


@router.get("/api/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user info."""
    return build_auth_user_payload(current_user)
