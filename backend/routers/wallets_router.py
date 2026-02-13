from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from categories import (
    create_category,
    delete_category,
    get_all_categories,
    get_category_by_id,
    get_category_stats,
    get_wallet_category,
    set_wallet_category,
    update_category,
)
from db import Database, User, get_db
from user_data_store import (
    load_hidden_wallets,
    load_refresh_status,
    load_wallet_tags,
    save_hidden_wallets,
    save_refresh_status,
    save_wallet_tags,
)


def create_wallets_router(
    *,
    reports_dir: Path,
    get_wallet_meta: Callable[[str], dict | None],
    check_wallet_ownership: Callable[[Database, int, str], bool],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/wallets")
    def list_wallets(
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """List user's tracked wallets (excluding hidden ones)."""
        tags = load_wallet_tags(current_user.id)
        hidden = load_hidden_wallets(current_user.id)

        wallet_addresses = current_user.wallet_addresses
        wallets = []

        for address in wallet_addresses:
            if address.lower() in hidden:
                continue

            meta = get_wallet_meta(address)
            if not meta:
                continue

            report_path = reports_dir / f"{address}.md"
            meta["has_report"] = report_path.exists()
            meta["tag"] = tags.get(address, "")

            category_id = get_wallet_category(current_user.id, address)
            if category_id:
                category = get_category_by_id(current_user.id, category_id)
                meta["category"] = category
            else:
                meta["category"] = None

            wallets.append(meta)

        return wallets

    @router.get("/api/tags")
    def get_tags(current_user: User = Depends(get_current_user)):
        """Get all wallet tags for current user."""
        return load_wallet_tags(current_user.id)

    @router.put("/api/tags/{wallet}")
    async def set_tag(
        wallet: str,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Set tag/name for a wallet (user must own it)."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        body = await request.json()
        tag = body.get("tag", "").strip()

        tags = load_wallet_tags(current_user.id)
        if tag:
            tags[wallet_lower] = tag
        else:
            tags.pop(wallet_lower, None)
        save_wallet_tags(current_user.id, tags)

        return {"address": wallet_lower, "tag": tag}

    @router.get("/api/categories")
    def list_categories(current_user: User = Depends(get_current_user)):
        """Get all categories with wallet counts for current user."""
        categories = get_all_categories(current_user.id)
        stats = get_category_stats(current_user.id)

        for category in categories:
            category["wallet_count"] = stats.get(category["id"], 0)

        return {
            "categories": categories,
            "uncategorized_count": stats.get("uncategorized", 0),
        }

    @router.post("/api/categories")
    async def create_new_category(
        request: Request,
        current_user: User = Depends(get_current_user),
    ):
        """Create a new category for current user."""
        body = await request.json()
        name = body.get("name", "").strip()
        color = body.get("color", "#3b82f6")

        if not name:
            raise HTTPException(status_code=400, detail="Category name is required")

        category = create_category(current_user.id, name, color)
        return category

    @router.put("/api/categories/{category_id}")
    async def update_existing_category(
        category_id: str,
        request: Request,
        current_user: User = Depends(get_current_user),
    ):
        """Update category (name, color, or expanded state) for current user."""
        body = await request.json()
        name = body.get("name")
        color = body.get("color")
        expanded = body.get("expanded")

        category = update_category(current_user.id, category_id, name=name, color=color, expanded=expanded)
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

        return category

    @router.delete("/api/categories/{category_id}")
    def remove_category(
        category_id: str,
        current_user: User = Depends(get_current_user),
    ):
        """Delete a category for current user. Wallets in this category become uncategorized."""
        success = delete_category(current_user.id, category_id)
        if not success:
            raise HTTPException(status_code=404, detail="Category not found")

        return {"status": "deleted", "category_id": category_id}

    @router.put("/api/wallets/{wallet}/category")
    async def assign_wallet_category(
        wallet: str,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Assign wallet to a category or remove from category (set to null)."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        body = await request.json()
        category_id = body.get("category_id")

        success = set_wallet_category(current_user.id, wallet_lower, category_id)
        if not success:
            raise HTTPException(status_code=404, detail="Category not found")

        return {"wallet": wallet_lower, "category_id": category_id}

    @router.get("/api/wallets/{wallet}/category")
    def get_wallet_category_info(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Get category info for a specific wallet (user must own it)."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        category_id = get_wallet_category(current_user.id, wallet_lower)
        if category_id:
            category = get_category_by_id(current_user.id, category_id)
            return {"wallet": wallet_lower, "category": category}

        return {"wallet": wallet_lower, "category": None}

    @router.post("/api/wallets/{wallet}/hide")
    def hide_wallet(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Hide wallet from list (user must own it)."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        hidden = load_hidden_wallets(current_user.id)
        hidden.add(wallet_lower)
        save_hidden_wallets(current_user.id, hidden)

        # Clear refresh status for this wallet to stop polling.
        user_refresh_tasks = load_refresh_status(current_user.id)
        if wallet_lower in user_refresh_tasks:
            user_refresh_tasks.pop(wallet_lower)
            save_refresh_status(current_user.id, user_refresh_tasks)

        return {"wallet": wallet_lower, "status": "hidden"}

    @router.post("/api/wallets/{wallet}/unhide")
    def unhide_wallet(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Restore hidden wallet to list (user must own it)."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        hidden = load_hidden_wallets(current_user.id)
        if wallet_lower in hidden:
            hidden.remove(wallet_lower)
            save_hidden_wallets(current_user.id, hidden)

        return {"wallet": wallet_lower, "status": "visible"}

    return router
