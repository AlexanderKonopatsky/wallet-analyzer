import os
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from analyze import call_llm
from auth import get_current_user, get_optional_user
from db import Database, User, get_db
from user_data_store import load_user_balance, save_user_balance

PUBLIC_DEMO_WALLET = os.getenv(
    "PUBLIC_DEMO_WALLET",
    "0xfeb016d0d14ac0fa6d69199608b0776d007203b2",
).lower()


def _parse_report_sections(markdown: str) -> tuple[list[dict], list[str]]:
    """Split report markdown into day sections and lightweight fingerprints."""
    header_pattern = re.compile(r"^###\s+(.+)$", re.MULTILINE)
    matches = list(header_pattern.finditer(markdown))
    if not matches:
        return [], []

    sections: list[dict] = []
    fingerprints: list[str] = []

    for idx, match in enumerate(matches):
        section_start = match.start()
        section_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        raw_section = markdown[section_start:section_end]
        date = match.group(1).strip()

        first_newline = raw_section.find("\n")
        if first_newline == -1:
            content = ""
        else:
            content = raw_section[first_newline + 1 :].strip()

        sort_date_match = re.search(r"\d{4}-\d{2}-\d{2}", date)
        sort_date = sort_date_match.group(0) if sort_date_match else ""

        significance_match = re.search(
            r"\*\*(?:Важность|Importance)\s*:\s*([1-5])\s*\*\*",
            content,
        )
        significance = int(significance_match.group(1)) if significance_match else 3

        fingerprint_date_match = re.search(
            r"(\d{4}-\d{2}-\d{2}(?:\s*—\s*\d{4}-\d{2}-\d{2})?)",
            date,
        )
        fingerprint_date = fingerprint_date_match.group(1) if fingerprint_date_match else ""
        fingerprints.append(f"{fingerprint_date}:{len(raw_section)}")

        sections.append({
            "date": date,
            "content": content,
            "sort_date": sort_date,
            "original_index": idx,
            "significance": significance,
        })

    sections.sort(key=lambda item: item.get("sort_date", ""), reverse=True)
    return sections, fingerprints


def _build_calendar_sections(sections: list[dict]) -> list[dict]:
    """Build lightweight section list for calendar without day content."""
    return [
        {
            "date": section.get("date", ""),
            "sort_date": section.get("sort_date", ""),
            "original_index": section.get("original_index", idx),
            "significance": section.get("significance", 3),
        }
        for idx, section in enumerate(sections)
    ]


def create_profiles_router(
    *,
    reports_dir: Path,
    check_wallet_ownership: Callable[[Database, int, str], bool],
    add_user_wallet: Callable[[Database, int, str], None],
    get_wallet_meta: Callable[[str], dict | None],
    estimate_profile_generation_cost: Callable[[str], dict],
    profile_model: str,
    profile_max_tokens: int,
    profile_system_prompt: str,
) -> APIRouter:
    router = APIRouter()

    def ensure_wallet_access(
        *,
        db: Database,
        user_id: int,
        wallet: str,
        can_auto_attach: bool,
    ) -> None:
        """Ensure user can access wallet; auto-attach when shared artifacts already exist."""
        if check_wallet_ownership(db, user_id, wallet):
            return

        if can_auto_attach:
            add_user_wallet(db, user_id, wallet)
            if check_wallet_ownership(db, user_id, wallet):
                return

        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    @router.get("/api/report/{wallet}")
    def get_report(
        wallet: str,
        days_limit: int | None = Query(default=None, ge=1, le=200),
        days_offset: int = Query(default=0, ge=0),
        current_user: User | None = Depends(get_optional_user),
        db: Database = Depends(get_db),
    ):
        """Get wallet report (full markdown or paginated by day sections)."""
        wallet = wallet.lower()
        report_path = reports_dir / f"{wallet}.md"

        if not report_path.exists():
            raise HTTPException(status_code=404, detail="No report found for this wallet")

        if current_user is None:
            if wallet != PUBLIC_DEMO_WALLET:
                raise HTTPException(status_code=401, detail="Not authenticated")
        else:
            ensure_wallet_access(
                db=db,
                user_id=current_user.id,
                wallet=wallet,
                can_auto_attach=True,
            )

        markdown = report_path.read_text(encoding="utf-8")
        sections, fingerprints = _parse_report_sections(markdown)
        calendar_sections = _build_calendar_sections(sections)
        total_sections = len(sections)
        meta = get_wallet_meta(wallet)

        if days_limit is not None:
            paginated_sections = sections[days_offset : days_offset + days_limit]
            has_more = (days_offset + len(paginated_sections)) < total_sections

            response = {
                "sections": paginated_sections,
                "days_offset": days_offset,
                "days_limit": days_limit,
                "returned_sections": len(paginated_sections),
                "total_sections": total_sections,
                "has_more": has_more,
                "last_updated": meta["last_updated"] if meta else None,
                "tx_count": meta["tx_count"] if meta else 0,
                "address": meta["address"] if meta else wallet,
            }
            if days_offset == 0:
                response["section_fingerprints"] = fingerprints
                response["calendar_sections"] = calendar_sections
            return response

        return {
            "markdown": markdown,
            "sections": sections,
            "calendar_sections": calendar_sections,
            "total_sections": total_sections,
            "has_more": False,
            "section_fingerprints": fingerprints,
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

        profile_path = reports_dir / f"{wallet}_profile.json"
        if not profile_path.exists():
            raise HTTPException(status_code=404, detail="No profile found")

        ensure_wallet_access(
            db=db,
            user_id=current_user.id,
            wallet=wallet,
            can_auto_attach=True,
        )

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

        report_path = reports_dir / f"{wallet}.md"
        if not report_path.exists():
            raise HTTPException(status_code=404, detail="Report not found. Refresh data first.")

        ensure_wallet_access(
            db=db,
            user_id=current_user.id,
            wallet=wallet,
            can_auto_attach=True,
        )

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

        ensure_wallet_access(
            db=db,
            user_id=current_user.id,
            wallet=wallet,
            can_auto_attach=True,
        )

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
