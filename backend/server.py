import hashlib
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

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from main import fetch_all_transactions, load_existing_data, save_data
from db import init_db, get_db, User, Database
from auth import get_current_user
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
    fmt_amount,
    fmt_usd,
    fmt_ts,
    ts_to_date,
    call_llm,
    parse_llm_response,
    extract_day_summaries,
    build_context_for_llm,
    SYSTEM_PROMPT,
    FULL_CHRONOLOGY_COUNT,
    merge_chronology_parts,
)
from categories import (
    get_all_categories,
    get_category_by_id,
    create_category,
    update_category,
    delete_category,
    get_wallet_category,
    set_wallet_category,
    get_category_stats,
    get_wallets_by_category,
)
from user_data_store import (
    load_wallet_tags,
    save_wallet_tags,
    load_refresh_status,
    save_refresh_status,
    load_hidden_wallets,
    save_hidden_wallets,
    load_analysis_consents,
    grant_analysis_consent,
    revoke_analysis_consent,
    load_user_balance,
    save_user_balance,
)
from routers.auth_router import router as auth_router
from routers.system_router import create_system_router
from routers.admin_backup_router import create_admin_backup_router
from routers.payment_router import router as payment_router

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

@app.get("/api/wallets")
def list_wallets(
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """List user's tracked wallets (excluding hidden ones)."""
    tags = load_wallet_tags(current_user.id)
    hidden = load_hidden_wallets(current_user.id)

    # Get user's wallets from DB
    wallet_addresses = current_user.wallet_addresses

    wallets = []
    for address in wallet_addresses:
        # Skip hidden wallets
        if address.lower() in hidden:
            continue

        meta = get_wallet_meta(address)
        if meta:
            report_path = REPORTS_DIR / f"{address}.md"
            meta["has_report"] = report_path.exists()
            meta["tag"] = tags.get(address, "")

            # Add category info
            category_id = get_wallet_category(current_user.id, address)
            if category_id:
                category = get_category_by_id(current_user.id, category_id)
                meta["category"] = category
            else:
                meta["category"] = None

            wallets.append(meta)

    return wallets


@app.get("/api/tags")
def get_tags(current_user: User = Depends(get_current_user)):
    """Get all wallet tags for current user."""
    return load_wallet_tags(current_user.id)


@app.put("/api/tags/{wallet}")
async def set_tag(
    wallet: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Set tag/name for a wallet (user must own it)."""
    wallet_lower = wallet.lower()

    # Check ownership
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


# ── Categories API ────────────────────────────────────────────────────────────


@app.get("/api/categories")
def list_categories(current_user: User = Depends(get_current_user)):
    """Get all categories with wallet counts for current user."""
    categories = get_all_categories(current_user.id)
    stats = get_category_stats(current_user.id)

    # Add wallet count to each category
    for category in categories:
        category["wallet_count"] = stats.get(category["id"], 0)

    return {
        "categories": categories,
        "uncategorized_count": stats.get("uncategorized", 0)
    }


@app.post("/api/categories")
async def create_new_category(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """Create a new category for current user."""
    body = await request.json()
    name = body.get("name", "").strip()
    color = body.get("color", "#3b82f6")

    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")

    category = create_category(current_user.id, name, color)
    return category


@app.put("/api/categories/{category_id}")
async def update_existing_category(
    category_id: str,
    request: Request,
    current_user: User = Depends(get_current_user)
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


@app.delete("/api/categories/{category_id}")
def remove_category(
    category_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a category for current user. Wallets in this category will become uncategorized."""
    success = delete_category(current_user.id, category_id)

    if not success:
        raise HTTPException(status_code=404, detail="Category not found")

    return {"status": "deleted", "category_id": category_id}


@app.put("/api/wallets/{wallet}/category")
async def assign_wallet_category(
    wallet: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Assign wallet to a category or remove from category (set to null)."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    body = await request.json()
    category_id = body.get("category_id")  # Can be null to uncategorize

    success = set_wallet_category(current_user.id, wallet_lower, category_id)

    if not success:
        raise HTTPException(status_code=404, detail="Category not found")

    return {"wallet": wallet_lower, "category_id": category_id}


@app.get("/api/wallets/{wallet}/category")
def get_wallet_category_info(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get category info for a specific wallet (user must own it)."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    category_id = get_wallet_category(current_user.id, wallet_lower)

    if category_id:
        category = get_category_by_id(current_user.id, category_id)
        return {"wallet": wallet_lower, "category": category}

    return {"wallet": wallet_lower, "category": None}


# ── Wallet Hiding/Unhiding ────────────────────────────────────────────────────


@app.post("/api/wallets/{wallet}/hide")
def hide_wallet(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Hide wallet from list (user must own it). Wallet data remains in database."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    hidden = load_hidden_wallets(current_user.id)
    hidden.add(wallet_lower)
    save_hidden_wallets(current_user.id, hidden)

    # Clear refresh status for this wallet to stop any polling
    user_refresh_tasks = load_refresh_status(current_user.id)
    if wallet_lower in user_refresh_tasks:
        user_refresh_tasks.pop(wallet_lower)
        save_refresh_status(current_user.id, user_refresh_tasks)

    return {"wallet": wallet_lower, "status": "hidden"}


@app.post("/api/wallets/{wallet}/unhide")
def unhide_wallet(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Restore hidden wallet to list (user must own it)."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    hidden = load_hidden_wallets(current_user.id)
    if wallet_lower in hidden:
        hidden.remove(wallet_lower)
        save_hidden_wallets(current_user.id, hidden)

    return {"wallet": wallet_lower, "status": "visible"}


@app.get("/api/report/{wallet}")
def get_report(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get markdown report for a wallet. Reports are shared globally (cached data)."""
    wallet = wallet.lower()

    report_path = REPORTS_DIR / f"{wallet}.md"

    # If report doesn't exist, return 404 (new wallet, no analysis yet)
    # This allows frontend to auto-start refresh for new wallets
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="No report found for this wallet")

    # Security: only allow viewing if user owns wallet
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


@app.get("/api/profile/{wallet}")
def get_profile(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get cached profile for a wallet. Profiles are shared globally (cached data)."""
    wallet = wallet.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    profile_path = REPORTS_DIR / f"{wallet}_profile.json"

    # If profile doesn't exist, return 404
    if not profile_path.exists():
        raise HTTPException(status_code=404, detail="No profile found")

    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/profile/{wallet}/estimate-cost")
def estimate_profile_cost(
    wallet: str,
    force: bool = False,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Estimate profile generation cost from report size/word count."""
    wallet = wallet.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    report_path = REPORTS_DIR / f"{wallet}.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found. Refresh data first.")

    markdown = report_path.read_text(encoding="utf-8")
    report_hash = hashlib.md5(markdown.encode("utf-8")).hexdigest()
    estimate = estimate_profile_generation_cost(markdown)

    is_cached = False
    profile_path = REPORTS_DIR / f"{wallet}_profile.json"
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


@app.post("/api/profile/{wallet}/generate")
def generate_profile(
    wallet: str,
    force: bool = False,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Generate wallet profile from report using LLM. Returns cached if report unchanged."""
    wallet = wallet.lower()

    report_path = REPORTS_DIR / f"{wallet}.md"

    # If report doesn't exist, return 404
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found. Refresh data first.")

    # If report exists but user doesn't own wallet, return 403
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    markdown = report_path.read_text(encoding="utf-8")
    report_hash = hashlib.md5(markdown.encode("utf-8")).hexdigest()

    # Check cache
    profile_path = REPORTS_DIR / f"{wallet}_profile.json"
    if profile_path.exists() and not force:
        with open(profile_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("report_hash") == report_hash:
            return cached

    estimate = estimate_profile_generation_cost(markdown)
    profile_cost = estimate["cost_usd"]

    # Check balance before LLM call
    balance_data = load_user_balance(current_user.id)
    current_balance = float(balance_data.get("balance", 0.0) or 0.0)
    if current_balance < profile_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient balance: need ${profile_cost:.4f}, have ${current_balance:.4f}"
        )

    # Generate profile via LLM
    user_prompt = f"Вот хронология активности кошелька:\n\n{markdown}\n\nСоставь профиль этого кошелька."
    profile_text = call_llm(PROFILE_SYSTEM_PROMPT, user_prompt, model=PROFILE_MODEL, max_tokens=PROFILE_MAX_TOKENS)

    # Deduct generation cost from user's balance
    balance_data["balance"] = round(current_balance - profile_cost, 4)
    balance_data.setdefault("transactions", []).append({
        "type": "profile",
        "amount": profile_cost,
        "wallet": wallet,
        "model": PROFILE_MODEL,
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

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile_data, f, indent=2, ensure_ascii=False)

    return profile_data


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


def format_tx_for_frontend(tx: dict) -> dict:
    """Format a transaction for display in the frontend."""
    tx_type = tx.get("tx_type", "?")
    chain = tx.get("chain", "?")
    tx_hash = tx.get("tx_hash", "")
    timestamp = tx.get("timestamp", 0)

    explorer_base = CHAIN_EXPLORERS.get(chain, "")
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
    elif tx_type == "lp":
        lp_type = tx.get("type", "?")
        result["description"] = (
            f"LP {lp_type} {fmt_amount(tx.get('token0_amount', 0))} {tx.get('token0_symbol', '?')} "
            f"+ {fmt_amount(tx.get('token1_amount', 0))} {tx.get('token1_symbol', '?')}"
        )
        result["usd"] = fmt_usd(
            (tx.get("token0_amount_usd", 0) or 0) + (tx.get("token1_amount_usd", 0) or 0)
        )
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


@app.get("/api/tx-counts/{wallet}")
def get_tx_counts(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get transaction counts per day. Data is shared globally (cached)."""
    wallet = wallet.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    raw_txs = load_transactions(wallet)

    # If no transaction data, return 404
    if not raw_txs:
        raise HTTPException(status_code=404, detail="No transaction data found")

    txs = filter_transactions(raw_txs)
    day_groups = group_by_days(txs)

    return {day: len(day_txs) for day, day_txs in day_groups.items()}


@app.get("/api/transactions/{wallet}")
def get_transactions(
    wallet: str,
    date_from: str = None,
    date_to: str = None,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get wallet transactions, optionally filtered by date range. Data is shared globally (cached)."""
    wallet = wallet.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    raw_txs = load_transactions(wallet)

    # If no transaction data, return 404
    if not raw_txs:
        raise HTTPException(status_code=404, detail="No transaction data found")

    txs = filter_transactions(raw_txs)
    day_groups = group_by_days(txs)

    # Filter by date range if provided
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


def background_estimate_cost(wallet: str, user_id: int) -> None:
    """Background task: fetch transactions and calculate cost estimate."""
    wallet_lower = wallet.lower()
    try:
        print(f"[Cost Estimate] Starting for {wallet_lower} (user {user_id})", flush=True)

        # Check if transactions already exist
        data_file = DATA_DIR / f"{wallet_lower}.json"
        is_cached = data_file.exists()

        user_refresh_tasks = load_refresh_status(user_id)

        if not is_cached:
            # Fetch transactions with progress updates
            print(f"[Cost Estimate] Fetching transactions for new wallet: {wallet_lower}")
            user_refresh_tasks[wallet_lower] = {
                "status": "fetching",
                "detail": "Fetching transactions...",
                "new_count": 0,
                "total_count": 0
            }
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
                    "detail": f"Received {new_count} new transactions",
                    "new_count": new_count,
                    "total_count": total_count
                }
                save_refresh_status(user_id, user_refresh_tasks)
                refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

            all_transactions = fetch_all_transactions(wallet, existing_txs, progress_callback=fetch_progress)
            if all_transactions:
                all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                save_data(wallet, all_transactions)

            # Reload data file after saving
            existing_data = load_existing_data(wallet)
            tx_count = len(existing_data["transactions"])
        else:
            # Load existing transaction count
            print(f"[Cost Estimate] Using cached transactions for: {wallet_lower}")
            existing_data = load_existing_data(wallet)
            tx_count = len(existing_data["transactions"])

        # Calculate cost
        cost_per_1000 = float(os.getenv("COST_PER_1000_TX", "0.20"))
        cost_multiplier = float(os.getenv("COST_MULTIPLIER", "1.0"))
        base_cost = (tx_count / 1000) * cost_per_1000
        final_cost = base_cost * cost_multiplier

        # Update status with cost estimate
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {
            "status": "cost_estimate",
            "tx_count": tx_count,
            "cost_usd": round(final_cost, 2),
            "is_cached": is_cached
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
        # Clean up thread reference
        active_threads.pop(wallet_lower, None)


@app.post("/api/estimate-cost/{wallet}")
def estimate_cost(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Start background task to fetch transactions and estimate cost.

    Returns immediately and updates status via /api/refresh-status/{wallet}
    """
    wallet_lower = wallet.lower()

    # Check ownership or add if new wallet
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        # New wallet - add to user's list
        add_user_wallet(db, current_user.id, wallet_lower)

    # Check if already running
    existing_thread = active_threads.get(wallet_lower)
    if existing_thread and existing_thread.is_alive():
        return {"status": "already_running"}

    # Start background task
    user_refresh_tasks = load_refresh_status(current_user.id)
    user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
    save_refresh_status(current_user.id, user_refresh_tasks)
    refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

    thread = threading.Thread(target=background_estimate_cost, args=(wallet, current_user.id), daemon=False)
    thread.start()
    active_threads[wallet_lower] = thread

    return {"status": "started"}


@app.post("/api/start-analysis/{wallet}")
def start_analysis(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Start AI analysis for a wallet (transactions must be fetched first via /estimate-cost)."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    # Explicit consent is granted when user manually starts analysis.
    grant_analysis_consent(current_user.id, wallet_lower)

    # Load user's refresh status
    user_refresh_tasks = load_refresh_status(current_user.id)

    # Check if thread is already running
    existing_thread = active_threads.get(wallet_lower)
    if existing_thread and existing_thread.is_alive():
        current = user_refresh_tasks.get(wallet_lower, {})
        return {"status": "already_running", "detail": current.get("detail", "")}

    # Check status from disk
    current = user_refresh_tasks.get(wallet_lower, {})
    if current.get("status") in ("fetching", "analyzing"):
        # Status says running but no thread - might be stale, start new one
        pass

    user_refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
    save_refresh_status(current_user.id, user_refresh_tasks)

    # Update global in-memory tracker
    refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]

    thread = threading.Thread(target=background_refresh, args=(wallet, current_user.id), daemon=False)
    thread.start()
    active_threads[wallet_lower] = thread

    return {"status": "started"}


@app.post("/api/cancel-analysis/{wallet}")
def cancel_analysis(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Cancel pending analysis task and remove it from user's persisted task list."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    user_refresh_tasks = load_refresh_status(current_user.id)
    removed = wallet_lower in user_refresh_tasks
    if removed:
        user_refresh_tasks.pop(wallet_lower, None)
        save_refresh_status(current_user.id, user_refresh_tasks)

    # Remove in-memory task mirror so /api/active-tasks won't re-surface it.
    refresh_tasks.pop(wallet_lower, None)

    # If user explicitly cancels, do not keep auto-analysis consent for this wallet.
    revoke_analysis_consent(current_user.id, wallet_lower)

    return {"status": "cancelled", "wallet": wallet_lower, "removed": removed}


@app.post("/api/refresh-bulk")
async def start_bulk_refresh(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Start refresh for multiple wallets (all or by category) for current user."""
    body = await request.json()
    category_id = body.get("category_id")  # None for all, string for specific category

    # Get candidate wallets for this request
    if category_id == "all" or category_id is None:
        wallets = current_user.wallet_addresses
    else:
        wallets = get_wallets_by_category(current_user.id, category_id)

    if not wallets:
        return {"status": "no_wallets", "started": []}

    hidden_wallets = load_hidden_wallets(current_user.id)
    consented_wallets = load_analysis_consents(current_user.id)

    # Start refresh for each wallet (if not already running)
    started = []
    already_running = []
    skipped_unauthorized = []  # Track wallets user doesn't actually own
    skipped_hidden = []
    skipped_no_consent = []
    user_refresh_tasks = load_refresh_status(current_user.id)
    seen_wallets = set()

    for wallet in wallets:
        wallet_lower = wallet.lower()

        if wallet_lower in seen_wallets:
            continue
        seen_wallets.add(wallet_lower)

        # Never refresh hidden wallets (not present in UI list).
        if wallet_lower in hidden_wallets:
            skipped_hidden.append(wallet_lower)
            continue

        # Security check: verify user actually owns this wallet before refreshing
        if not check_wallet_ownership(db, current_user.id, wallet_lower):
            skipped_unauthorized.append(wallet_lower)
            continue

        # Only refresh wallets with explicit consent for paid analysis.
        if wallet_lower not in consented_wallets:
            skipped_no_consent.append(wallet_lower)
            continue

        # Check if thread is already running
        existing_thread = active_threads.get(wallet_lower)
        if existing_thread and existing_thread.is_alive():
            already_running.append(wallet_lower)
            continue

        # Check status from disk
        current = user_refresh_tasks.get(wallet_lower, {})
        if current.get("status") in ("fetching", "analyzing"):
            # Status says running but no thread - might be stale, start new one
            pass

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
        "total": len(seen_wallets)
    }


@app.get("/api/refresh-status/{wallet}")
def get_refresh_status(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Check refresh progress for a wallet (user must own it)."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    user_refresh_tasks = load_refresh_status(current_user.id)
    status = user_refresh_tasks.get(wallet_lower, {"status": "idle", "detail": ""})
    return status


@app.get("/api/active-tasks")
def get_active_tasks(current_user: User = Depends(get_current_user), db: Database = Depends(get_db)):
    """Get active refresh tasks for current user (only for wallets user still owns)."""
    # Load with cleanup to remove statuses for deleted wallets
    user_refresh_tasks = load_refresh_status(current_user.id, cleanup=True, db=db)
    active = {
        wallet: status
        for wallet, status in user_refresh_tasks.items()
        if status.get("status") in ("cost_estimate", "fetching", "analyzing")
    }
    return active


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



