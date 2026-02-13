import os
import threading
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from analyze import (
    filter_transactions,
    fmt_amount,
    fmt_ts,
    fmt_usd,
    group_by_days,
    load_transactions,
)
from auth import get_current_user
from categories import get_wallets_by_category
from db import Database, User, get_db
from main import fetch_all_transactions, load_existing_data, save_data
from user_data_store import (
    grant_analysis_consent,
    load_analysis_consents,
    load_hidden_wallets,
    load_refresh_status,
    revoke_analysis_consent,
    save_refresh_status,
)


def create_analysis_router(
    *,
    data_dir: Path,
    refresh_tasks: dict[str, dict],
    active_threads: dict[str, threading.Thread],
    chain_explorers: dict[str, str],
    check_wallet_ownership: Callable[[Database, int, str], bool],
    add_user_wallet: Callable[[Database, int, str], None],
    background_refresh: Callable[[str, int], None],
) -> APIRouter:
    router = APIRouter()

    def format_tx_for_frontend(tx: dict) -> dict:
        """Format a transaction for display in the frontend."""
        tx_type = tx.get("tx_type", "?")
        chain = tx.get("chain", "?")
        tx_hash = tx.get("tx_hash", "")
        timestamp = tx.get("timestamp", 0)

        explorer_base = chain_explorers.get(chain, "")
        explorer_url = f"{explorer_base}{tx_hash}" if explorer_base and tx_hash else ""

        result = {
            "tx_hash": tx_hash,
            "tx_type": tx_type,
            "chain": chain,
            "timestamp": timestamp,
            "time": fmt_ts(timestamp),
            "explorer_url": explorer_url,
        }

        if tx_type == "swap":
            result["description"] = (
                f"Swap {fmt_amount(tx.get('token0_amount', 0))} {tx.get('token0_symbol', '?')} "
                f"→ {fmt_amount(tx.get('token1_amount', 0))} {tx.get('token1_symbol', '?')}"
            )
            result["usd"] = fmt_usd(max(
                tx.get("token0_amount_usd", 0) or 0,
                tx.get("token1_amount_usd", 0) or 0,
            ))
            result["platform"] = tx.get("dex", "") or ""
        elif tx_type == "lending":
            action = tx.get("action", "?")
            result["description"] = f"{action} {fmt_amount(tx.get('amount', 0))} {tx.get('symbol', '?')}"
            result["usd"] = fmt_usd(tx.get("amount_usd", 0) or 0)
            result["platform"] = tx.get("platform", "") or ""
        elif tx_type == "transfer":
            sym = tx.get("symbol", tx.get("token_symbol", "?"))
            amt = tx.get("amount", tx.get("token_amount", 0))
            usd = tx.get("amount_usd", tx.get("token_amount_usd", 0)) or 0
            from_label = tx.get("from_label", "") or ""
            to_label = tx.get("to_label", "") or ""
            frm = tx.get("from", "")
            to = tx.get("to", "")
            if not from_label and frm:
                from_label = f"{frm[:6]}…{frm[-4:]}"
            if not to_label and to:
                to_label = f"{to[:6]}…{to[-4:]}"
            result["description"] = f"Transfer {fmt_amount(amt)} {sym}: {from_label} → {to_label}"
            result["usd"] = fmt_usd(usd)
            result["platform"] = ""
        elif tx_type == "approval":
            spender = tx.get("spender_label") or tx.get("spender", "")
            result["description"] = f"Approve {tx.get('symbol', '?')} for {spender}"
            result["usd"] = ""
            result["platform"] = ""
        elif tx_type == "staking":
            action = tx.get("action", "?")
            result["description"] = f"{action} {fmt_amount(tx.get('amount', 0))} {tx.get('symbol', '?')}"
            result["usd"] = fmt_usd(tx.get("amount_usd", 0) or 0)
            result["platform"] = tx.get("platform", "") or ""
        elif tx_type == "lp":
            action = tx.get("action", "?")
            result["description"] = (
                f"{action} {fmt_amount(tx.get('token0_amount', 0))} {tx.get('token0_symbol', '?')} + "
                f"{fmt_amount(tx.get('token1_amount', 0))} {tx.get('token1_symbol', '?')}"
            )
            result["usd"] = fmt_usd(tx.get("total_usd", 0) or 0)
            result["platform"] = tx.get("dex", "") or ""
        elif tx_type == "bridge":
            from_chain = tx.get("from_chain", "?") or "?"
            to_chain = tx.get("to_chain", "?") or "?"
            result["description"] = (
                f"Bridge {fmt_amount(tx.get('amount', 0))} {tx.get('token_symbol', '?')} "
                f"{from_chain} → {to_chain}"
            )
            result["usd"] = fmt_usd(tx.get("amount_usd", 0) or 0)
            result["platform"] = tx.get("platform", "") or ""
        elif tx_type == "wrap":
            action = tx.get("action", "?")
            result["description"] = f"{action} {fmt_amount(tx.get('amount', 0))} {tx.get('symbol', '?')}"
            result["usd"] = fmt_usd(tx.get("amount_usd", 0) or 0)
            result["platform"] = ""
        elif tx_type == "nft_transfer":
            name = tx.get("nft_name", "?")
            token_id = tx.get("nft_token_id", "?")
            result["description"] = f"NFT {name} #{token_id}"
            result["usd"] = ""
            result["platform"] = ""
        else:
            result["description"] = tx_type.upper()
            result["usd"] = ""
            result["platform"] = ""

        return result

    def background_estimate_cost(wallet: str, user_id: int) -> None:
        """Background task: fetch transactions and calculate cost estimate."""
        wallet_lower = wallet.lower()
        try:
            print(f"[Cost Estimate] Starting for {wallet_lower} (user {user_id})", flush=True)

            data_file = data_dir / f"{wallet_lower}.json"
            is_cached = data_file.exists()
            user_refresh_tasks = load_refresh_status(user_id)

            if not is_cached:
                print(f"[Cost Estimate] Fetching transactions for new wallet: {wallet_lower}")
                user_refresh_tasks[wallet_lower] = {
                    "status": "fetching",
                    "detail": "Fetching transactions...",
                    "new_count": 0,
                    "total_count": 0,
                }
                save_refresh_status(user_id, user_refresh_tasks)
                refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

                existing_data = load_existing_data(wallet)
                existing_txs = {tx["tx_hash"]: tx for tx in existing_data["transactions"]}

                def fetch_progress(new_count, total_count):
                    current_tasks = load_refresh_status(user_id)
                    current_tasks[wallet_lower] = {
                        "status": "fetching",
                        "detail": f"Received {new_count} new transactions",
                        "new_count": new_count,
                        "total_count": total_count,
                    }
                    save_refresh_status(user_id, current_tasks)
                    refresh_tasks[wallet_lower] = current_tasks[wallet_lower]

                all_transactions = fetch_all_transactions(wallet, existing_txs, progress_callback=fetch_progress)
                if all_transactions:
                    all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                    save_data(wallet, all_transactions)

                existing_data = load_existing_data(wallet)
                tx_count = len(existing_data["transactions"])
            else:
                print(f"[Cost Estimate] Using cached transactions for: {wallet_lower}")
                existing_data = load_existing_data(wallet)
                tx_count = len(existing_data["transactions"])

            cost_per_1000 = float(os.getenv("COST_PER_1000_TX", "0.20"))
            cost_multiplier = float(os.getenv("COST_MULTIPLIER", "1.0"))
            base_cost = (tx_count / 1000) * cost_per_1000
            final_cost = base_cost * cost_multiplier

            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {
                "status": "cost_estimate",
                "tx_count": tx_count,
                "cost_usd": round(final_cost, 2),
                "is_cached": is_cached,
            }
            save_refresh_status(user_id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

            print(f"[Cost Estimate] Done for {wallet_lower}: {tx_count} txs, ${final_cost:.2f}", flush=True)

        except Exception as e:
            print(f"[Cost Estimate] Error for {wallet_lower}: {e}", flush=True)
            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {"status": "error", "detail": str(e)}
            save_refresh_status(user_id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]
        finally:
            active_threads.pop(wallet_lower, None)

    @router.get("/api/tx-counts/{wallet}")
    def get_tx_counts(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Get transaction counts per day."""
        wallet = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        raw_txs = load_transactions(wallet)
        if not raw_txs:
            raise HTTPException(status_code=404, detail="No transaction data found")

        txs = filter_transactions(raw_txs)
        day_groups = group_by_days(txs)
        return {day: len(day_txs) for day, day_txs in day_groups.items()}

    @router.get("/api/transactions/{wallet}")
    def get_transactions(
        wallet: str,
        date_from: str = None,
        date_to: str = None,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Get wallet transactions, optionally filtered by date range."""
        wallet = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        raw_txs = load_transactions(wallet)
        if not raw_txs:
            raise HTTPException(status_code=404, detail="No transaction data found")

        txs = filter_transactions(raw_txs)
        day_groups = group_by_days(txs)

        if date_from or date_to:
            filtered = {}
            for day, day_txs in day_groups.items():
                if date_from and day < date_from:
                    continue
                if date_to and day > date_to:
                    continue
                filtered[day] = day_txs
            day_groups = filtered

        result = {}
        for day, day_txs in day_groups.items():
            result[day] = [format_tx_for_frontend(tx) for tx in day_txs]

        return result

    @router.post("/api/estimate-cost/{wallet}")
    def estimate_cost(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Start background task to fetch transactions and estimate cost."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            add_user_wallet(db, current_user.id, wallet_lower)

        existing_thread = active_threads.get(wallet_lower)
        if existing_thread and existing_thread.is_alive():
            return {"status": "already_running"}

        user_refresh_tasks = load_refresh_status(current_user.id)
        user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
        save_refresh_status(current_user.id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        thread = threading.Thread(target=background_estimate_cost, args=(wallet, current_user.id), daemon=False)
        thread.start()
        active_threads[wallet_lower] = thread

        return {"status": "started"}

    @router.post("/api/start-analysis/{wallet}")
    def start_analysis(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Start AI analysis for a wallet."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        grant_analysis_consent(current_user.id, wallet_lower)

        user_refresh_tasks = load_refresh_status(current_user.id)
        existing_thread = active_threads.get(wallet_lower)
        if existing_thread and existing_thread.is_alive():
            current = user_refresh_tasks.get(wallet_lower, {})
            return {"status": "already_running", "detail": current.get("detail", "")}

        user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
        save_refresh_status(current_user.id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        thread = threading.Thread(target=background_refresh, args=(wallet, current_user.id), daemon=False)
        thread.start()
        active_threads[wallet_lower] = thread

        return {"status": "started"}

    @router.post("/api/cancel-analysis/{wallet}")
    def cancel_analysis(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Cancel pending analysis task and remove it from user's persisted task list."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        user_refresh_tasks = load_refresh_status(current_user.id)
        removed = wallet_lower in user_refresh_tasks
        if removed:
            user_refresh_tasks.pop(wallet_lower, None)
            save_refresh_status(current_user.id, user_refresh_tasks)

        refresh_tasks.pop(wallet_lower, None)
        revoke_analysis_consent(current_user.id, wallet_lower)
        return {"status": "cancelled", "wallet": wallet_lower, "removed": removed}

    @router.post("/api/refresh-bulk")
    async def start_bulk_refresh(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Start refresh for multiple wallets (all or by category) for current user."""
        body = await request.json()
        category_id = body.get("category_id")

        if category_id == "all" or category_id is None:
            wallets = current_user.wallet_addresses
        else:
            wallets = get_wallets_by_category(current_user.id, category_id)

        if not wallets:
            return {"status": "no_wallets", "started": []}

        hidden_wallets = load_hidden_wallets(current_user.id)
        consented_wallets = load_analysis_consents(current_user.id)

        started = []
        already_running = []
        skipped_unauthorized = []
        skipped_hidden = []
        skipped_no_consent = []
        user_refresh_tasks = load_refresh_status(current_user.id)
        seen_wallets = set()

        for wallet in wallets:
            wallet_lower = wallet.lower()

            if wallet_lower in seen_wallets:
                continue
            seen_wallets.add(wallet_lower)

            if wallet_lower in hidden_wallets:
                skipped_hidden.append(wallet_lower)
                continue

            if not check_wallet_ownership(db, current_user.id, wallet_lower):
                skipped_unauthorized.append(wallet_lower)
                continue

            if wallet_lower not in consented_wallets:
                skipped_no_consent.append(wallet_lower)
                continue

            existing_thread = active_threads.get(wallet_lower)
            if existing_thread and existing_thread.is_alive():
                already_running.append(wallet_lower)
                continue

            user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
            save_refresh_status(current_user.id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

            thread = threading.Thread(target=background_refresh, args=(wallet, current_user.id), daemon=False)
            thread.start()
            active_threads[wallet_lower] = thread
            started.append(wallet_lower)

        return {
            "status": "started",
            "started": started,
            "already_running": already_running,
            "skipped_unauthorized": skipped_unauthorized,
            "skipped_hidden": skipped_hidden,
            "skipped_no_consent": skipped_no_consent,
            "total": len(seen_wallets),
        }

    @router.get("/api/refresh-status/{wallet}")
    def get_refresh_status(
        wallet: str,
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Check refresh progress for a wallet (user must own it)."""
        wallet_lower = wallet.lower()

        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            raise HTTPException(status_code=403, detail="Wallet not found in your list")

        user_refresh_tasks = load_refresh_status(current_user.id)
        status = user_refresh_tasks.get(wallet_lower, {"status": "idle", "detail": ""})
        return status

    @router.get("/api/active-tasks")
    def get_active_tasks(
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Get active refresh tasks for current user."""
        user_refresh_tasks = load_refresh_status(current_user.id, cleanup=True, db=db)
        active = {
            wallet: status
            for wallet, status in user_refresh_tasks.items()
            if status.get("status") in ("cost_estimate", "fetching", "analyzing")
        }
        return active

    return router
