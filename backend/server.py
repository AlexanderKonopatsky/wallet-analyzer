import io
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

# Fix Windows encoding for Unicode characters in print() from imported modules
# line_buffering=True ensures logs appear immediately (important for background threads)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from main import fetch_all_transactions, load_existing_data, save_data
from db import init_db, Database
from analyze import (
    load_transactions,
    filter_transactions,
    get_tx_key,
    load_state,
    save_state,
    save_report,
    group_by_days,
    make_chunks,
    format_tx_for_llm,
    call_llm,
    parse_llm_response,
    extract_day_summaries,
    build_context_for_llm,
    SYSTEM_PROMPT,
    merge_chronology_parts,
)
from user_data_store import (
    load_refresh_status,
    save_refresh_status,
    load_hidden_wallets,
    load_analysis_consents,
    load_user_balance,
    save_user_balance,
)
from routers.auth_router import router as auth_router
from routers.system_router import create_system_router
from routers.admin_backup_router import create_admin_backup_router
from routers.payment_router import router as payment_router
from routers.wallets_router import create_wallets_router
from routers.profiles_router import create_profiles_router
from routers.analysis_router import create_analysis_router
CHAIN_EXPLORERS = {
    "ethereum": "https://etherscan.io/tx/",
    "arbitrum": "https://arbiscan.io/tx/",
    "optimism": "https://optimistic.etherscan.io/tx/",
    "polygon": "https://polygonscan.com/tx/",
    "base": "https://basescan.org/tx/",
    "blast": "https://blastscan.io/tx/",
    "bsc": "https://bscscan.com/tx/",
    "avalanche": "https://snowtrace.io/tx/",
    "fantom": "https://ftmscan.com/tx/",
    "linea": "https://lineascan.build/tx/",
    "zksync": "https://explorer.zksync.io/tx/",
    "scroll": "https://scrollscan.com/tx/",
    "mantle": "https://explorer.mantle.xyz/tx/",
    "gnosis": "https://gnosisscan.io/tx/",
    "celo": "https://celoscan.io/tx/",
    "zora": "https://explorer.zora.energy/tx/",
    "mode": "https://explorer.mode.network/tx/",
    "manta": "https://pacific-explorer.manta.network/tx/",
    "solana": "https://solscan.io/tx/",
}

app = FastAPI()

# CORS: support both local development and production
allowed_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
]
# Add Railway production URL if set
railway_url = os.getenv("RAILWAY_PUBLIC_DOMAIN")
if railway_url:
    allowed_origins.append(f"https://{railway_url}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.on_event("startup")
def startup_event():
    """Initialize database and start scheduler on startup."""
    init_db()

    # Start auto-refresh scheduler if enabled
    if AUTO_REFRESH_ENABLED:
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        print(f"[Scheduler] Auto-refresh enabled: will run daily at {AUTO_REFRESH_TIME} UTC")
    else:
        print("[Scheduler] Auto-refresh disabled (AUTO_REFRESH_ENABLED=false)")

    # Ensure public demo report is always available for guest users.
    ensure_public_demo_wallet_report()

# Path relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
# Store reports inside data/ for Railway single volume
REPORTS_DIR = DATA_DIR / "reports"

# Ensure directories exist at startup
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
print(f"[Init] Data directory: {DATA_DIR} (exists: {DATA_DIR.exists()})")
print(f"[Init] Reports directory: {REPORTS_DIR} (exists: {REPORTS_DIR.exists()})")
DATA_BACKUP_ARCHIVE_DIR = DATA_DIR / "backups"
DATA_BACKUP_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
DATA_BACKUP_ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.getenv("DATA_BACKUP_ADMIN_EMAILS", "").split(",")
    if email.strip()
}
DATA_IMPORT_MAX_MB = max(1, int(os.getenv("DATA_IMPORT_MAX_MB", "2048")))
DATA_IMPORT_MAX_BYTES = DATA_IMPORT_MAX_MB * 1024 * 1024
data_backup_lock = threading.Lock()

# Profile generation settings
PROFILE_MODEL = os.getenv("PROFILE_MODEL", "google/gemini-3-pro-preview")
PROFILE_MAX_TOKENS = int(os.getenv("PROFILE_MAX_TOKENS", 15192))
PROFILE_COST_BASE_USD = float(os.getenv("PROFILE_COST_BASE_USD", "0.03686"))
PROFILE_COST_PER_WORD_USD = float(os.getenv("PROFILE_COST_PER_WORD_USD", "0.000005846"))
PROFILE_SYSTEM_PROMPT = """Ты — опытный ончейн-аналитик. Тебе дана подробная хронология активности крипто-кошелька.

Прочитай отчёт целиком и составь глубокий профиль владельца. Не следуй шаблону — каждый кошелёк уникален, и профиль должен отражать именно то, что делает этого владельца особенным. Пиши о том, что действительно бросается в глаза и заслуживает внимания.

Не пересказывай транзакции. Анализируй поведение, читай между строк, делай выводы. Ссылайся на конкретные события из отчёта как доказательства. Пиши на русском, используй markdown."""

# Auto-refresh settings
AUTO_REFRESH_ENABLED = os.getenv("AUTO_REFRESH_ENABLED", "false").lower() == "true"
AUTO_REFRESH_TIME = os.getenv("AUTO_REFRESH_TIME", "23:00")
PUBLIC_DEMO_WALLET = os.getenv(
    "PUBLIC_DEMO_WALLET",
    "0xfeb016d0d14ac0fa6d69199608b0776d007203b2",
).lower()
PUBLIC_DEMO_WALLET_NAME = os.getenv(
    "PUBLIC_DEMO_WALLET_NAME",
    "Vitalik",
).strip() or "Vitalik"

# Background task status tracking: {wallet: {status, detail, thread_id}}
refresh_tasks: dict[str, dict] = {}
# Active threads: {wallet: Thread object}
active_threads: dict[str, threading.Thread] = {}


def check_wallet_ownership(db: Database, user_id: int, wallet_address: str) -> bool:
    """Check if user owns this wallet."""
    user = db.get_user_by_id(user_id)
    if not user:
        return False
    wallet_lower = wallet_address.lower()
    return wallet_lower in [w.lower() for w in user.wallet_addresses]


def add_user_wallet(db: Database, user_id: int, wallet_address: str):
    """Add wallet to user's tracked wallets."""
    wallet_address = wallet_address.lower()
    user = db.get_user_by_id(user_id)
    if not user:
        print(f"[DB] Error: User {user_id} not found")
        return

    if db.add_wallet_to_user(user, wallet_address):
        print(f"[DB] Added wallet {wallet_address} for user {user_id}")


def has_running_background_tasks() -> bool:
    """Check if any refresh/analysis thread is currently running."""
    return any(thread and thread.is_alive() for thread in active_threads.values())


def get_wallet_meta(wallet: str) -> dict:
    """Read wallet metadata from data file."""
    filepath = DATA_DIR / f"{wallet.lower()}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "address": data.get("wallet", wallet),
        "last_updated": data.get("last_updated"),
        "tx_count": len(data.get("transactions", [])),
    }


def estimate_profile_generation_cost(markdown: str) -> dict:
    """Estimate profile generation cost from report text."""
    report_words = len(re.findall(r"\S+", markdown))
    report_chars = len(markdown)
    cost_multiplier = float(os.getenv("COST_MULTIPLIER", "1.0"))

    base_cost = PROFILE_COST_BASE_USD + (report_words * PROFILE_COST_PER_WORD_USD)
    final_cost = round(base_cost * cost_multiplier, 4)

    return {
        "model": PROFILE_MODEL,
        "report_words": report_words,
        "report_chars": report_chars,
        "cost_multiplier": cost_multiplier,
        "base_cost_usd": round(base_cost, 4),
        "cost_usd": final_cost,
    }


def run_analysis_pipeline(wallet: str, user_id: int = None, progress_callback=None) -> None:
    """Run the analyze.py pipeline without interactive prompts.

    Args:
        wallet: Wallet address
        user_id: User ID for status updates
        progress_callback: Optional callback(current_chunk, total_chunks, percent) to report progress
    """
    raw_txs = load_transactions(wallet)
    if not raw_txs:
        return

    txs = filter_transactions(raw_txs)
    all_day_groups = group_by_days(txs)
    day_tx_counts = {day: len(day_txs) for day, day_txs in all_day_groups.items()}

    state = load_state(wallet)
    chronology_parts = state["chronology_parts"]
    processed_keys = set(state["processed_tx_keys"])
    pending_keys = set(state.get("pending_tx_keys", []))
    start_chunk = state["chunk_index"]
    compression_cache = state.get("compression_cache", {"groups": {}, "super_groups": {}})

    resuming = bool(pending_keys and start_chunk > 0)

    if resuming:
        new_txs = [tx for tx in txs if get_tx_key(tx) in pending_keys]
    else:
        new_txs = [tx for tx in txs if get_tx_key(tx) not in processed_keys]
        start_chunk = 0

        if not new_txs:
            if not processed_keys and chronology_parts:
                all_keys = [get_tx_key(tx) for tx in txs]
                save_state(wallet, {
                    "chunk_index": 0,
                    "chronology_parts": chronology_parts,
                    "processed_tx_keys": all_keys,
                    "pending_tx_keys": [],
                    "compression_cache": compression_cache,
                })
            return

    batch_keys = [get_tx_key(tx) for tx in new_txs]
    day_groups = group_by_days(new_txs)
    chunks = make_chunks(day_groups)
    total_chunks = len(chunks)

    print(f"🚀 Starting analysis for wallet {wallet[:10]}...")
    print(f"📦 Found {len(new_txs)} new transactions, split into {total_chunks} chunks")
    if resuming:
        print(f"♻️ Resuming from chunk {start_chunk + 1}/{total_chunks}")

    for i in range(start_chunk, total_chunks):
        chunk = chunks[i]

        formatted_lines = []
        for day, day_txs in chunk.items():
            for tx in day_txs:
                formatted_lines.append(format_tx_for_llm(tx))

        tx_text = "\n".join(formatted_lines)

        # Build context: compressed summaries + last N full chronologies
        context = build_context_for_llm(
            chronology_parts,
            compression_cache,
            day_tx_counts=day_tx_counts,
        )

        # Save context for inspection
        from analyze import REPORTS_DIR
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        context_path = REPORTS_DIR / f"{wallet.lower()}_context.md"
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(f"# LLM Context for chunk {i + 1}/{total_chunks}\n\n{context}")

        user_prompt = f"""{context}

## Транзакции для анализа:
{tx_text}

Опиши хронологию действий пользователя по дням."""

        # Report progress before LLM call
        current_percent = int((i / total_chunks) * 100)
        print(f"📊 Analyzing chunk {i + 1}/{total_chunks} for wallet {wallet[:10]}... ({len(formatted_lines)} transactions, {current_percent}%)")
        if progress_callback:
            progress_callback(i + 1, total_chunks, current_percent)

        response = call_llm(SYSTEM_PROMPT, user_prompt)
        chronology = parse_llm_response(response)

        # Report progress after LLM call
        completed_percent = int(((i + 1) / total_chunks) * 100)
        print(f"✅ Chunk {i + 1}/{total_chunks} processed ({completed_percent}%)")
        if progress_callback:
            progress_callback(i + 1, total_chunks, completed_percent)

        if chronology:
            chronology_parts = merge_chronology_parts(chronology_parts, chronology)

        save_state(wallet, {
            "chunk_index": i + 1,
            "chronology_parts": chronology_parts,
            "processed_tx_keys": list(processed_keys),
            "pending_tx_keys": batch_keys,
            "compression_cache": compression_cache,
        })

    processed_keys.update(batch_keys)
    save_state(wallet, {
        "chunk_index": 0,
        "chronology_parts": chronology_parts,
        "processed_tx_keys": list(processed_keys),
        "pending_tx_keys": [],
        "compression_cache": compression_cache,
    })

    save_report(wallet, chronology_parts)
    print(f"🎉 Analysis for wallet {wallet[:10]}... completed! Report saved.")


def ensure_public_demo_wallet_report() -> None:
    """Ensure a public demo report exists. Build it in background if missing."""
    wallet_lower = PUBLIC_DEMO_WALLET
    report_path = REPORTS_DIR / f"{wallet_lower}.md"

    if report_path.exists():
        print(f"[Demo] Public demo report already exists for {wallet_lower}")
        return

    existing_thread = active_threads.get(wallet_lower)
    if existing_thread and existing_thread.is_alive():
        print(f"[Demo] Demo report generation already running for {wallet_lower}")
        return

    def _worker() -> None:
        try:
            print(f"[Demo] Ensuring public demo report for {wallet_lower}")

            state = load_state(wallet_lower)
            chronology_parts = state.get("chronology_parts", [])
            if chronology_parts:
                save_report(wallet_lower, chronology_parts)
                print(f"[Demo] Rebuilt report from existing state for {wallet_lower}")
                return

            data_file = DATA_DIR / f"{wallet_lower}.json"
            if not data_file.exists():
                print(f"[Demo] Fetching transactions for {wallet_lower}")
                existing_data = load_existing_data(wallet_lower)
                existing_txs = {
                    tx["tx_hash"]: tx
                    for tx in existing_data.get("transactions", [])
                    if tx.get("tx_hash")
                }
                all_transactions = fetch_all_transactions(wallet_lower, existing_txs)
                if all_transactions:
                    all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                    save_data(wallet_lower, all_transactions)

            reloaded_data = load_existing_data(wallet_lower)
            if not reloaded_data.get("transactions"):
                print(f"[Demo] Cannot generate report for {wallet_lower}: no transactions")
                return

            run_analysis_pipeline(wallet_lower)
        except Exception as exc:
            print(f"[Demo] Failed to ensure report for {wallet_lower}: {exc}")
        finally:
            active_threads.pop(wallet_lower, None)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    active_threads[wallet_lower] = thread
    print(f"[Demo] Started background demo report generation for {wallet_lower}")


def background_refresh(wallet: str, user_id: int) -> None:
    """Background task: fetch transactions then run analysis for specific user."""
    wallet_lower = wallet.lower()
    try:
        # Step 1: Fetch transactions
        print(f"[Refresh] Step 1: Fetching transactions for {wallet_lower} (user {user_id})", flush=True)
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Fetching transactions from API...", "new_count": 0, "total_count": 0}
        save_refresh_status(user_id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        existing_data = load_existing_data(wallet)
        existing_txs = {tx["tx_hash"]: tx for tx in existing_data["transactions"]}
        initial_count = len(existing_txs)

        # Progress callback to update status
        def fetch_progress(new_count, total_count):
            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {
                "status": "fetching",
                "detail": f"Received {new_count} new transactions (total: {total_count})",
                "new_count": new_count,
                "total_count": total_count
            }
            save_refresh_status(user_id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        all_transactions = fetch_all_transactions(wallet, existing_txs, progress_callback=fetch_progress)
        if all_transactions:
            all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            save_data(wallet, all_transactions)

        # Final fetch status
        final_new_count = len(all_transactions) - initial_count
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {
            "status": "fetching",
            "detail": f"Fetched {final_new_count} new transactions (total: {len(all_transactions)})",
            "new_count": final_new_count,
            "total_count": len(all_transactions)
        }
        save_refresh_status(user_id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        # Check if we have any data at all (either newly fetched or existing)
        data_file = DATA_DIR / f"{wallet_lower}.json"
        if not data_file.exists():
            # API returned pending/empty and no existing data - cannot proceed
            raise Exception("No transaction data available yet. Data is still loading from API. Please try again in a few minutes.")

        # Step 2: Analyze
        # Check if wallet has any transactions
        reloaded_data = load_existing_data(wallet)
        if not reloaded_data["transactions"]:
            print(f"[Refresh] Wallet {wallet_lower} has 0 transactions. Skipping analysis.", flush=True)
            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {"status": "done", "detail": "Wallet has no transactions"}
            save_refresh_status(user_id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]
            return

        # Calculate cost and deduct from balance
        tx_count = len(reloaded_data["transactions"])
        cost_per_1000 = float(os.getenv("COST_PER_1000_TX", "0.20"))
        cost_multiplier = float(os.getenv("COST_MULTIPLIER", "1.0"))
        analysis_cost = round((tx_count / 1000) * cost_per_1000 * cost_multiplier, 2)

        # Deduct from balance
        balance_data = load_user_balance(user_id)
        current_balance = float(balance_data.get("balance", 0.0) or 0.0)
        if current_balance < analysis_cost:
            print(f"[Refresh] Insufficient balance for {wallet_lower}: need ${analysis_cost}, have ${current_balance}", flush=True)
            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {
                "status": "cost_estimate",
                "detail": f"Insufficient balance: need ${analysis_cost:.2f}, have ${current_balance:.2f}",
                "tx_count": tx_count,
                "cost_usd": analysis_cost,
                "required_cost_usd": analysis_cost,
                "balance_usd": round(current_balance, 2),
                "insufficient_balance": True,
            }
            save_refresh_status(user_id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]
            return

        # Deduct cost from balance
        balance_data["balance"] = current_balance - analysis_cost
        if "transactions" not in balance_data:
            balance_data["transactions"] = []
        balance_data["transactions"].append({
            "type": "analysis",
            "amount": analysis_cost,
            "wallet": wallet_lower,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        save_user_balance(user_id, balance_data)
        print(f"[Refresh] Deducted ${analysis_cost} for analysis. Balance: ${balance_data['balance']}", flush=True)

        print(f"[Refresh] Step 2: Analyzing transactions for {wallet_lower}", flush=True)
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {"status": "analyzing", "detail": "Analyzing transactions with AI...", "percent": 0}
        save_refresh_status(user_id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        # Progress callback for analysis
        def analysis_progress(current_chunk, total_chunks, percent):
            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {
                "status": "analyzing",
                "detail": f"Analyzing chunk {current_chunk}/{total_chunks}",
                "current_chunk": current_chunk,
                "total_chunks": total_chunks,
                "percent": percent
            }
            save_refresh_status(user_id, user_refresh_tasks)
            refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

        run_analysis_pipeline(wallet, user_id=user_id, progress_callback=analysis_progress)

        print(f"[Refresh] Step 2 done: Analysis complete for {wallet_lower}", flush=True)
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {"status": "done", "detail": "Refresh complete!"}
        save_refresh_status(user_id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

    except Exception as e:
        print(f"[Refresh] ERROR for {wallet_lower}: {e}", flush=True)
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {"status": "error", "detail": str(e)}
        save_refresh_status(user_id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]
    finally:
        # Clean up thread reference
        active_threads.pop(wallet_lower, None)


# ── Startup: Initialize refresh tasks tracking ──────────────────────────────

# Note: refresh_tasks will now be loaded per-user when needed
# Global dict still used for in-memory tracking of active background tasks
refresh_tasks: dict[str, dict] = {}


def on_data_import_success(db: Database) -> None:
    """Reload DB and clear cached refresh status after data import."""
    db.load()
    refresh_tasks.clear()


app.include_router(auth_router)
app.include_router(
    create_system_router(
        auto_refresh_enabled=AUTO_REFRESH_ENABLED,
        auto_refresh_time=AUTO_REFRESH_TIME,
        data_backup_restricted=bool(DATA_BACKUP_ADMIN_EMAILS),
        data_import_max_mb=DATA_IMPORT_MAX_MB,
        public_demo_wallet=PUBLIC_DEMO_WALLET,
        public_demo_wallet_name=PUBLIC_DEMO_WALLET_NAME,
    )
)
app.include_router(
    create_admin_backup_router(
        data_backup_admin_emails=DATA_BACKUP_ADMIN_EMAILS,
        data_backup_lock=data_backup_lock,
        project_root=PROJECT_ROOT,
        data_dir=DATA_DIR,
        reports_dir=REPORTS_DIR,
        data_backup_archive_dir=DATA_BACKUP_ARCHIVE_DIR,
        data_import_max_mb=DATA_IMPORT_MAX_MB,
        data_import_max_bytes=DATA_IMPORT_MAX_BYTES,
        has_running_background_tasks=has_running_background_tasks,
        on_import_success=on_data_import_success,
    )
)
app.include_router(payment_router)
app.include_router(
    create_wallets_router(
        reports_dir=REPORTS_DIR,
        get_wallet_meta=get_wallet_meta,
        check_wallet_ownership=check_wallet_ownership,
    )
)
app.include_router(
    create_profiles_router(
        reports_dir=REPORTS_DIR,
        check_wallet_ownership=check_wallet_ownership,
        add_user_wallet=add_user_wallet,
        get_wallet_meta=get_wallet_meta,
        estimate_profile_generation_cost=estimate_profile_generation_cost,
        profile_model=PROFILE_MODEL,
        profile_max_tokens=PROFILE_MAX_TOKENS,
        profile_system_prompt=PROFILE_SYSTEM_PROMPT,
    )
)
app.include_router(
    create_analysis_router(
        data_dir=DATA_DIR,
        refresh_tasks=refresh_tasks,
        active_threads=active_threads,
        chain_explorers=CHAIN_EXPLORERS,
        check_wallet_ownership=check_wallet_ownership,
        add_user_wallet=add_user_wallet,
        background_refresh=background_refresh,
    )
)

def auto_refresh_all_wallets() -> None:
    """Auto-refresh all wallets for all users (scheduled task)."""
    try:
        print(f"[Auto-Refresh] Starting scheduled refresh at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

        # Get database connection (singleton with loaded data)
        from db import get_database
        db = get_database()

        # Get all users
        all_users = db.get_all_users()

        if not all_users:
            print("[Auto-Refresh] No users found, skipping")
            return

        total_wallets = 0
        total_started = 0

        for user in all_users:
            user_id = user.id
            all_wallets = user.wallet_addresses

            if not all_wallets:
                continue

            hidden_wallets = load_hidden_wallets(user_id)
            consented_wallets = load_analysis_consents(user_id)
            wallets = [
                wallet for wallet in all_wallets
                if wallet.lower() not in hidden_wallets and wallet.lower() in consented_wallets
            ]

            if not wallets:
                print(f"[Auto-Refresh] Skipping user {user_id}: no visible consented wallets")
                continue

            skipped_hidden_count = sum(1 for wallet in all_wallets if wallet.lower() in hidden_wallets)
            skipped_no_consent_count = sum(
                1 for wallet in all_wallets
                if wallet.lower() not in hidden_wallets and wallet.lower() not in consented_wallets
            )
            print(
                f"[Auto-Refresh] Processing {len(wallets)} wallets for user {user_id} "
                f"(hidden: {skipped_hidden_count}, no consent: {skipped_no_consent_count})"
            )

            # Load user's refresh status
            user_refresh_tasks = load_refresh_status(user_id)

            for wallet in wallets:
                wallet_lower = wallet.lower()
                total_wallets += 1

                # Check if already running
                existing_thread = active_threads.get(wallet_lower)
                if existing_thread and existing_thread.is_alive():
                    print(f"[Auto-Refresh] Skipping {wallet_lower} (already running)")
                    continue

                # Check status from disk
                current = user_refresh_tasks.get(wallet_lower, {})
                if current.get("status") in ("fetching", "analyzing"):
                    print(f"[Auto-Refresh] Skipping {wallet_lower} (status: {current.get('status')})")
                    continue

                # Start refresh
                print(f"[Auto-Refresh] Starting refresh for {wallet_lower} (user {user_id})")
                user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Auto-refresh: Starting..."}
                save_refresh_status(user_id, user_refresh_tasks)
                refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

                thread = threading.Thread(target=background_refresh, args=(wallet, user_id), daemon=False)
                thread.start()
                active_threads[wallet_lower] = thread
                total_started += 1

        print(f"[Auto-Refresh] Completed: {total_started}/{total_wallets} wallets started, {total_wallets - total_started} skipped")

    except Exception as e:
        print(f"[Auto-Refresh] Error during scheduled refresh: {e}")


def run_scheduler() -> None:
    """Background thread to run scheduled tasks (using UTC time)."""
    print(f"[Scheduler] Starting scheduler thread (auto-refresh at {AUTO_REFRESH_TIME} UTC)")

    # Parse target time (HH:MM format)
    target_hour, target_minute = map(int, AUTO_REFRESH_TIME.split(":"))
    last_run_date = None

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            current_date = now_utc.date()
            current_time = now_utc.time()

            # Check if it's time to run (target time reached and not run today yet)
            if (current_time.hour == target_hour and
                current_time.minute == target_minute and
                last_run_date != current_date):

                print(f"[Scheduler] Triggering auto-refresh at {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                auto_refresh_all_wallets()
                last_run_date = current_date

            time.sleep(60)  # Check every minute
        except Exception as e:
            print(f"[Scheduler] Error in scheduler loop: {e}")
            time.sleep(60)


# Serve frontend static files (for production)
# IMPORTANT: This must be defined AFTER all API routes!
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.exists():
    # Mount static files with html=True for SPA routing
    # This automatically serves index.html for non-existent paths (SPA routing)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
    print("✅ Frontend static files mounted from:", FRONTEND_DIST)
else:
    print("⚠️  Frontend dist folder not found. Running in API-only mode.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)






