import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from analyze import call_llm
from auth import get_current_user
from db import Database, User, get_db
from user_data_store import load_user_balance, save_user_balance


def create_profiles_router(
    *,
    reports_dir: Path,
    check_wallet_ownership: Callable[[Database, int, str], bool],
    get_wallet_meta: Callable[[str], dict | None],
    estimate_profile_generation_cost: Callable[[str], dict],
    profile_model: str,
    profile_max_tokens: int,
    profile_system_prompt: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/report/{wallet}")
    def get_report(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Get markdown report for a wallet."""
        wallet = wallet.lower()
        report_path = reports_dir / f"{wallet}.md"

        if not report_path.exists():
            raise HTTPException(status_code=404, detail="No report found for this wallet")

        if not check_wallet_ownership(db, current_user.id, wallet):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        markdown = report_path.read_text(encoding="utf-8")
        meta = get_wallet_meta(wallet)

        return {
            "markdown": markdown,
            "last_updated": meta["last_updated"] if meta else None,
            "tx_count": meta["tx_count"] if meta else 0,
            "address": meta["address"] if meta else wallet,
        }

    @router.get("/api/profile/{wallet}")
    def get_profile(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Get cached profile for a wallet."""
        wallet = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        profile_path = reports_dir / f"{wallet}_profile.json"
        if not profile_path.exists():
            raise HTTPException(status_code=404, detail="No profile found")

        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @router.get("/api/profile/{wallet}/estimate-cost")
    def estimate_profile_cost(
        wallet: str,
        force: bool = False,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Estimate profile generation cost from report size/word count."""
        wallet = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        report_path = reports_dir / f"{wallet}.md"
        if not report_path.exists():
            raise HTTPException(status_code=404, detail="Report not found. Refresh data first.")

        markdown = report_path.read_text(encoding="utf-8")
        report_hash = hashlib.md5(markdown.encode("utf-8")).hexdigest()
        estimate = estimate_profile_generation_cost(markdown)

        is_cached = False
        profile_path = reports_dir / f"{wallet}_profile.json"
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            is_cached = cached.get("report_hash") == report_hash

        charge_required = force or not is_cached
        return {
            "wallet": wallet,
            "model": estimate["model"],
            "report_words": estimate["report_words"],
            "report_chars": estimate["report_chars"],
            "cost_multiplier": estimate["cost_multiplier"],
            "estimated_cost_usd": estimate["cost_usd"] if charge_required else 0.0,
            "charge_required": charge_required,
            "cached": is_cached,
        }

    @router.post("/api/profile/{wallet}/generate")
    def generate_profile(
        wallet: str,
        force: bool = False,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Generate wallet profile from report using LLM."""
        wallet = wallet.lower()
        report_path = reports_dir / f"{wallet}.md"

        if not report_path.exists():
            raise HTTPException(status_code=404, detail="Report not found. Refresh data first.")

        if not check_wallet_ownership(db, current_user.id, wallet):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        markdown = report_path.read_text(encoding="utf-8")
        report_hash = hashlib.md5(markdown.encode("utf-8")).hexdigest()

        profile_path = reports_dir / f"{wallet}_profile.json"
        if profile_path.exists() and not force:
            with open(profile_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("report_hash") == report_hash:
                return cached

        estimate = estimate_profile_generation_cost(markdown)
        profile_cost = estimate["cost_usd"]

        balance_data = load_user_balance(current_user.id)
        current_balance = float(balance_data.get("balance", 0.0) or 0.0)
        if current_balance < profile_cost:
            raise HTTPException(
                status_code=402,
                detail=f"Insufficient balance: need ${profile_cost:.4f}, have ${current_balance:.4f}",
            )

        user_prompt = f"Вот хронология активности кошелька:\n\n{markdown}\n\nСоставь профиль этого кошелька."
        profile_text = call_llm(
            profile_system_prompt,
            user_prompt,
            model=profile_model,
            max_tokens=profile_max_tokens,
        )

        balance_data["balance"] = round(current_balance - profile_cost, 4)
        balance_data.setdefault("transactions", []).append({
            "type": "profile",
            "amount": profile_cost,
            "wallet": wallet,
            "model": profile_model,
            "report_words": estimate["report_words"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        save_user_balance(current_user.id, balance_data)

        profile_data = {
            "wallet": wallet,
            "profile_text": profile_text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_hash": report_hash,
            "generation_cost_usd": profile_cost,
        }

        reports_dir.mkdir(parents=True, exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=2, ensure_ascii=False)

        return profile_data

    return router
