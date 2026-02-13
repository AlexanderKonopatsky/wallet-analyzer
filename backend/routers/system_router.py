from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from db import User
from user_data_store import load_user_balance, save_user_balance


def create_system_router(
    *,
    auto_refresh_enabled: bool,
    auto_refresh_time: str,
    data_backup_restricted: bool,
    data_import_max_mb: int,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings")
    def get_settings():
        """Get application settings."""
        return {
            "auto_refresh_enabled": auto_refresh_enabled,
            "auto_refresh_time": auto_refresh_time,
            "data_backup_restricted": data_backup_restricted,
            "data_import_max_mb": data_import_max_mb,
        }

    @router.get("/api/user/balance")
    def get_user_balance(current_user: User = Depends(get_current_user)):
        """Get current user's balance."""
        balance_data = load_user_balance(current_user.id)
        return {
            "balance": balance_data.get("balance", 0.0),
            "currency": "USD",
        }

    @router.post("/api/user/balance/deduct")
    def deduct_balance(
        amount: float,
        current_user: User = Depends(get_current_user),
    ):
        """Deduct amount from user's balance (for analysis cost)."""
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")

        balance_data = load_user_balance(current_user.id)
        current_balance = balance_data.get("balance", 0.0)

        if current_balance < amount:
            raise HTTPException(status_code=402, detail="Insufficient balance")

        balance_data["balance"] = round(current_balance - amount, 2)
        if "transactions" not in balance_data:
            balance_data["transactions"] = []

        balance_data["transactions"].append({
            "type": "deduction",
            "amount": amount,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        save_user_balance(current_user.id, balance_data)
        return {"balance": balance_data["balance"]}

    return router
