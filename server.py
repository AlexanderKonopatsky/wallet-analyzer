import hashlib
import io
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows encoding for Unicode characters in print() from imported modules
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from main import fetch_all_transactions, load_existing_data, save_data
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
    SYSTEM_PROMPT,
    FULL_CHRONOLOGY_COUNT,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")
TAGS_FILE = DATA_DIR / "wallet_tags.json"
REFRESH_STATUS_FILE = DATA_DIR / "refresh_status.json"
EXCLUDED_WALLETS_FILE = DATA_DIR / "excluded_wallets.json"

# Profile generation settings
PROFILE_MODEL = "google/gemini-3-flash-preview"
PROFILE_MAX_TOKENS = 8192
PROFILE_SYSTEM_PROMPT = """Ты — опытный ончейн-аналитик. Тебе дана подробная хронология активности крипто-кошелька.

Прочитай отчёт целиком и составь глубокий профиль владельца. Не следуй шаблону — каждый кошелёк уникален, и профиль должен отражать именно то, что делает этого владельца особенным. Пиши о том, что действительно бросается в глаза и заслуживает внимания.

Не пересказывай транзакции. Анализируй поведение, читай между строк, делай выводы. Ссылайся на конкретные события из отчёта как доказательства. Пиши на русском, используй markdown."""

# Wallet classification settings
CLASSIFY_MODEL = "google/gemini-3-flash-preview"
AUTO_CLASSIFY_BATCH_SIZE = int(os.getenv("AUTO_CLASSIFY_BATCH_SIZE", 3))
CLASSIFY_SYSTEM_PROMPT = """You are a blockchain address classifier. Given an Ethereum-compatible wallet address and some context about its transaction behavior, determine if this address belongs to a known protocol, bridge, exchange, contract, DEX router, MEV bot, or dust spammer — i.e., NOT a personal wallet.

Use the web search results to identify the address. Check if it matches any known protocol, bridge, exchange, or smart contract.

Respond ONLY with a JSON object (no markdown fencing, no extra text):
{
  "is_excluded": true or false,
  "label": "bridge" | "exchange" | "contract" | "dex_router" | "mev_bot" | "dust_spammer" | "personal" | "unknown",
  "name": "Human-readable name if known (e.g. 'Across Protocol', 'Binance Hot Wallet'), otherwise empty string",
  "confidence": "high" | "medium" | "low",
  "reasoning": "Brief explanation of why you classified it this way"
}

Rules:
- Only set is_excluded to true if confidence is medium or high
- If unsure, set is_excluded to false and label to "unknown"
- "personal" means it appears to be a regular user wallet
- Consider transaction patterns: bridges often handle many different tokens, exchanges have very high volume, contracts have programmatic behavior"""

# Background task status tracking: {wallet: {status, detail, thread_id}}
refresh_tasks: dict[str, dict] = {}
# Active threads: {wallet: Thread object}
active_threads: dict[str, threading.Thread] = {}


def load_wallet_tags() -> dict:
    """Load wallet tags/names from file."""
    if TAGS_FILE.exists():
        with open(TAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_wallet_tags(tags: dict) -> None:
    """Save wallet tags/names to file."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, indent=2, ensure_ascii=False)


def load_refresh_status() -> dict:
    """Load refresh task statuses from file."""
    if REFRESH_STATUS_FILE.exists():
        try:
            with open(REFRESH_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_refresh_status(status_dict: dict) -> None:
    """Save refresh task statuses to file."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(REFRESH_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_dict, f, indent=2, ensure_ascii=False)


def load_excluded_wallets() -> dict:
    """Load excluded wallet addresses from file."""
    if EXCLUDED_WALLETS_FILE.exists():
        try:
            with open(EXCLUDED_WALLETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_excluded_wallets(excluded: dict) -> None:
    """Save excluded wallet addresses to file."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(EXCLUDED_WALLETS_FILE, "w", encoding="utf-8") as f:
        json.dump(excluded, f, indent=2, ensure_ascii=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def run_analysis_pipeline(wallet: str) -> None:
    """Run the analyze.py pipeline without interactive prompts."""
    raw_txs = load_transactions(wallet)
    if not raw_txs:
        return

    txs = filter_transactions(raw_txs)

    state = load_state(wallet)
    chronology_parts = state["chronology_parts"]
    processed_keys = set(state["processed_tx_keys"])
    pending_keys = set(state.get("pending_tx_keys", []))
    start_chunk = state["chunk_index"]

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
                })
            return

    batch_keys = [get_tx_key(tx) for tx in new_txs]
    day_groups = group_by_days(new_txs)
    chunks = make_chunks(day_groups)
    total_chunks = len(chunks)

    for i in range(start_chunk, total_chunks):
        chunk = chunks[i]

        formatted_lines = []
        for day, day_txs in chunk.items():
            for tx in day_txs:
                formatted_lines.append(format_tx_for_llm(tx))

        tx_text = "\n".join(formatted_lines)

        # Build context
        if chronology_parts:
            context_sections = []

            if len(chronology_parts) > FULL_CHRONOLOGY_COUNT:
                old_parts = chronology_parts[:-FULL_CHRONOLOGY_COUNT]
                recent_parts = chronology_parts[-FULL_CHRONOLOGY_COUNT:]
            else:
                old_parts = []
                recent_parts = chronology_parts

            if old_parts:
                all_summaries = []
                for part in old_parts:
                    all_summaries.extend(extract_day_summaries(part))
                if all_summaries:
                    context_sections.append(
                        "## Краткий контекст предыдущей активности:\n"
                        + "\n".join(f"- {s}" for s in all_summaries)
                    )

            if recent_parts:
                context_sections.append(
                    "## Подробная хронология последних дней:\n\n"
                    + "\n\n".join(recent_parts)
                )

            context = "\n\n".join(context_sections)
        else:
            context = "## Контекст предыдущей активности:\nЭто начало анализа, предыдущих данных нет."

        user_prompt = f"""{context}

## Транзакции для анализа:
{tx_text}

Опиши хронологию действий пользователя по дням."""

        response = call_llm(SYSTEM_PROMPT, user_prompt)
        chronology = parse_llm_response(response)

        if chronology:
            chronology_parts.append(chronology)

        save_state(wallet, {
            "chunk_index": i + 1,
            "chronology_parts": chronology_parts,
            "processed_tx_keys": list(processed_keys),
            "pending_tx_keys": batch_keys,
        })

    processed_keys.update(batch_keys)
    save_state(wallet, {
        "chunk_index": 0,
        "chronology_parts": chronology_parts,
        "processed_tx_keys": list(processed_keys),
        "pending_tx_keys": [],
    })

    save_report(wallet, chronology_parts)


def background_refresh(wallet: str) -> None:
    """Background task: fetch transactions then run analysis."""
    wallet_lower = wallet.lower()
    try:
        # Step 1: Fetch transactions
        refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Fetching transactions from API..."}
        save_refresh_status(refresh_tasks)

        existing_data = load_existing_data(wallet)
        existing_txs = {tx["tx_hash"]: tx for tx in existing_data["transactions"]}

        all_transactions = fetch_all_transactions(wallet, existing_txs)
        if all_transactions:
            all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            save_data(wallet, all_transactions)

        # Step 2: Analyze
        refresh_tasks[wallet_lower] = {"status": "analyzing", "detail": "Analyzing transactions with AI..."}
        save_refresh_status(refresh_tasks)
        run_analysis_pipeline(wallet)

        refresh_tasks[wallet_lower] = {"status": "done", "detail": "Refresh complete!"}
        save_refresh_status(refresh_tasks)
    except Exception as e:
        refresh_tasks[wallet_lower] = {"status": "error", "detail": str(e)}
        save_refresh_status(refresh_tasks)
    finally:
        # Clean up thread reference
        active_threads.pop(wallet_lower, None)


# ── Startup: Load persisted refresh statuses ─────────────────────────────────

# Load refresh statuses from disk on startup
refresh_tasks = load_refresh_status()

# Clean up any stale "fetching" or "analyzing" statuses
# (these would be from interrupted previous runs)
for wallet, status in list(refresh_tasks.items()):
    if status.get("status") in ("fetching", "analyzing"):
        refresh_tasks[wallet] = {
            "status": "error",
            "detail": "Task interrupted (server restarted)"
        }
save_refresh_status(refresh_tasks)


# ── API Endpoints ─────────────────────────────────────────────────────────────


@app.get("/api/settings")
def get_settings():
    """Get application settings."""
    return {
        "auto_classify_batch_size": AUTO_CLASSIFY_BATCH_SIZE,
    }


@app.get("/api/wallets")
def list_wallets():
    """List all tracked wallets."""
    tags = load_wallet_tags()
    wallets = []
    if DATA_DIR.exists():
        for filepath in sorted(DATA_DIR.glob("*.json")):
            if filepath.name in ("wallet_tags.json", "categories.json", "refresh_status.json", "excluded_wallets.json"):
                continue
            address = filepath.stem
            meta = get_wallet_meta(address)
            if meta:
                report_path = REPORTS_DIR / f"{address}.md"
                meta["has_report"] = report_path.exists()
                meta["tag"] = tags.get(address.lower(), "")

                # Add category info
                category_id = get_wallet_category(address.lower())
                if category_id:
                    category = get_category_by_id(category_id)
                    meta["category"] = category
                else:
                    meta["category"] = None

                wallets.append(meta)
    return wallets


@app.get("/api/tags")
def get_tags():
    """Get all wallet tags."""
    return load_wallet_tags()


@app.put("/api/tags/{wallet}")
async def set_tag(wallet: str, request: Request):
    """Set tag/name for a wallet."""
    wallet_lower = wallet.lower()
    body = await request.json()
    tag = body.get("tag", "").strip()
    tags = load_wallet_tags()
    if tag:
        tags[wallet_lower] = tag
    else:
        tags.pop(wallet_lower, None)
    save_wallet_tags(tags)
    return {"address": wallet_lower, "tag": tag}


# ── Categories API ────────────────────────────────────────────────────────────


@app.get("/api/categories")
def list_categories():
    """Get all categories with wallet counts."""
    categories = get_all_categories()
    stats = get_category_stats()

    # Add wallet count to each category
    for category in categories:
        category["wallet_count"] = stats.get(category["id"], 0)

    return {
        "categories": categories,
        "uncategorized_count": stats.get("uncategorized", 0)
    }


@app.post("/api/categories")
async def create_new_category(request: Request):
    """Create a new category."""
    body = await request.json()
    name = body.get("name", "").strip()
    color = body.get("color", "#3b82f6")

    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")

    category = create_category(name, color)
    return category


@app.put("/api/categories/{category_id}")
async def update_existing_category(category_id: str, request: Request):
    """Update category (name, color, or expanded state)."""
    body = await request.json()
    name = body.get("name")
    color = body.get("color")
    expanded = body.get("expanded")

    category = update_category(category_id, name=name, color=color, expanded=expanded)

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    return category


@app.delete("/api/categories/{category_id}")
def remove_category(category_id: str):
    """Delete a category. Wallets in this category will become uncategorized."""
    success = delete_category(category_id)

    if not success:
        raise HTTPException(status_code=404, detail="Category not found")

    return {"status": "deleted", "category_id": category_id}


@app.put("/api/wallets/{wallet}/category")
async def assign_wallet_category(wallet: str, request: Request):
    """Assign wallet to a category or remove from category (set to null)."""
    wallet_lower = wallet.lower()
    body = await request.json()
    category_id = body.get("category_id")  # Can be null to uncategorize

    success = set_wallet_category(wallet_lower, category_id)

    if not success:
        raise HTTPException(status_code=404, detail="Category not found")

    return {"wallet": wallet_lower, "category_id": category_id}


@app.get("/api/wallets/{wallet}/category")
def get_wallet_category_info(wallet: str):
    """Get category info for a specific wallet."""
    wallet_lower = wallet.lower()
    category_id = get_wallet_category(wallet_lower)

    if category_id:
        category = get_category_by_id(category_id)
        return {"wallet": wallet_lower, "category": category}

    return {"wallet": wallet_lower, "category": None}


@app.get("/api/report/{wallet}")
def get_report(wallet: str):
    """Get markdown report for a wallet."""
    wallet = wallet.lower()
    report_path = REPORTS_DIR / f"{wallet}.md"

    if not report_path.exists():
        raise HTTPException(status_code=404, detail="No report found for this wallet")

    markdown = report_path.read_text(encoding="utf-8")
    meta = get_wallet_meta(wallet)

    return {
        "markdown": markdown,
        "last_updated": meta["last_updated"] if meta else None,
        "tx_count": meta["tx_count"] if meta else 0,
        "address": meta["address"] if meta else wallet,
    }


@app.get("/api/profile/{wallet}")
def get_profile(wallet: str):
    """Get cached profile for a wallet."""
    wallet = wallet.lower()
    profile_path = REPORTS_DIR / f"{wallet}_profile.json"

    if not profile_path.exists():
        raise HTTPException(status_code=404, detail="No profile found")

    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/profile/{wallet}/generate")
def generate_profile(wallet: str):
    """Generate wallet profile from report using LLM. Returns cached if report unchanged."""
    wallet = wallet.lower()
    report_path = REPORTS_DIR / f"{wallet}.md"

    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found. Refresh data first.")

    markdown = report_path.read_text(encoding="utf-8")
    report_hash = hashlib.md5(markdown.encode("utf-8")).hexdigest()

    # Check cache
    profile_path = REPORTS_DIR / f"{wallet}_profile.json"
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("report_hash") == report_hash:
            return cached

    # Generate profile via LLM
    user_prompt = f"Вот хронология активности кошелька:\n\n{markdown}\n\nСоставь профиль этого кошелька."
    profile_text = call_llm(PROFILE_SYSTEM_PROMPT, user_prompt, model=PROFILE_MODEL, max_tokens=PROFILE_MAX_TOKENS)

    profile_data = {
        "wallet": wallet,
        "profile_text": profile_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_hash": report_hash,
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile_data, f, indent=2, ensure_ascii=False)

    return profile_data


# ── Wallet Exclusion (classification) ─────────────────────────────────────────


def classify_wallet_address(address: str, context: str = "") -> dict:
    """Use LLM with web search to classify whether a wallet is a known contract/bridge/exchange/etc."""
    user_prompt = f"Classify this blockchain address: {address}"
    if context:
        user_prompt += f"\n\nTransaction context:\n{context}"

    try:
        response = call_llm(
            CLASSIFY_SYSTEM_PROMPT,
            user_prompt,
            model=CLASSIFY_MODEL,
            max_tokens=512,
            plugins=[{"id": "web", "max_results": 3}],
        )
        # Parse JSON from response (handle possible markdown fencing)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip())
        json_match = re.search(r"\{[\s\S]*\}", cleaned)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"Classification failed for {address}: {e}")

    return {
        "is_excluded": False,
        "label": "unknown",
        "name": "",
        "confidence": "low",
        "reasoning": "Classification failed",
    }


def build_classification_context(address: str) -> str:
    """Build transaction context for a wallet address from existing data."""
    context_parts = []
    if not DATA_DIR.exists():
        return ""

    for filepath in DATA_DIR.glob("*.json"):
        if filepath.name in ("wallet_tags.json", "categories.json", "refresh_status.json", "excluded_wallets.json"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            txs = data.get("transactions", [])
            relevant = [
                tx for tx in txs
                if (tx.get("from", "").lower() == address or tx.get("to", "").lower() == address)
                and tx.get("tx_type") == "transfer"
            ]
            if relevant:
                symbols = set()
                chains = set()
                for tx in relevant[:20]:
                    sym = tx.get("symbol", tx.get("token_symbol", ""))
                    if sym:
                        symbols.add(sym)
                    chain = tx.get("chain", "")
                    if chain:
                        chains.add(chain)
                context_parts.append(
                    f"Found in {len(relevant)} transfers, tokens: {', '.join(symbols)}, chains: {', '.join(chains)}"
                )
        except Exception:
            continue

    return "; ".join(context_parts[:5])


@app.get("/api/excluded-wallets")
def get_excluded_wallets():
    """Get all excluded wallet addresses."""
    return load_excluded_wallets()


@app.post("/api/excluded-wallets")
async def add_excluded_wallet(request: Request):
    """Manually add a wallet to the exclusion list."""
    body = await request.json()
    address = (body.get("address", "")).lower().strip()
    if not address:
        raise HTTPException(status_code=400, detail="Address is required")

    excluded = load_excluded_wallets()
    excluded[address] = {
        "is_excluded": True,
        "label": body.get("label", "other"),
        "name": body.get("name", ""),
        "reason": body.get("reason", "Manually excluded by user"),
        "source": "manual",
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }
    save_excluded_wallets(excluded)
    return {"address": address, **excluded[address]}


@app.delete("/api/excluded-wallets/{address:path}")
def remove_excluded_wallet(address: str):
    """Remove a wallet from the exclusion list."""
    address = address.lower()
    excluded = load_excluded_wallets()
    if address not in excluded:
        raise HTTPException(status_code=404, detail="Address not in exclusion list")
    removed = excluded.pop(address)
    save_excluded_wallets(excluded)
    return {"address": address, "status": "removed", **removed}


@app.post("/api/classify-wallet/{address:path}")
def classify_wallet(address: str):
    """Classify a wallet address using LLM with web search. Auto-excludes if confident."""
    address = address.lower()

    # Check if already classified (cache hit)
    classified = load_excluded_wallets()
    if address in classified:
        return {"address": address, "cached": True, **classified[address]}

    # Build context and classify
    context = build_classification_context(address)
    classification = classify_wallet_address(address, context)

    # Save ALL classification results as cache (both excluded and personal)
    is_excluded = bool(
        classification.get("is_excluded")
        and classification.get("confidence") in ("medium", "high")
    )
    classified[address] = {
        "is_excluded": is_excluded,
        "label": classification.get("label", "unknown"),
        "name": classification.get("name", ""),
        "reason": classification.get("reasoning", ""),
        "source": "llm",
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }
    save_excluded_wallets(classified)

    return {"address": address, "cached": False, **classified[address]}


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
def get_tx_counts(wallet: str):
    """Get transaction counts per day (lightweight, no formatting)."""
    wallet = wallet.lower()
    raw_txs = load_transactions(wallet)
    if not raw_txs:
        raise HTTPException(status_code=404, detail="No transaction data found")

    txs = filter_transactions(raw_txs)
    day_groups = group_by_days(txs)

    return {day: len(day_txs) for day, day_txs in day_groups.items()}


@app.get("/api/transactions/{wallet}")
def get_transactions(wallet: str, date_from: str = None, date_to: str = None):
    """Get wallet transactions, optionally filtered by date range."""
    wallet = wallet.lower()
    raw_txs = load_transactions(wallet)
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


@app.post("/api/refresh/{wallet}")
def start_refresh(wallet: str):
    """Start background fetch + analyze for a wallet."""
    wallet_lower = wallet.lower()

    # Check if thread is already running
    existing_thread = active_threads.get(wallet_lower)
    if existing_thread and existing_thread.is_alive():
        current = refresh_tasks.get(wallet_lower, {})
        return {"status": "already_running", "detail": current.get("detail", "")}

    # Check status from disk
    current = refresh_tasks.get(wallet_lower, {})
    if current.get("status") in ("fetching", "analyzing"):
        # Status says running but no thread - might be stale, start new one
        pass

    refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
    save_refresh_status(refresh_tasks)

    thread = threading.Thread(target=background_refresh, args=(wallet,), daemon=False)
    thread.start()
    active_threads[wallet_lower] = thread

    return {"status": "started"}


@app.post("/api/refresh-bulk")
async def start_bulk_refresh(request: Request):
    """Start refresh for multiple wallets (all or by category)."""
    body = await request.json()
    category_id = body.get("category_id")  # None for all, string for specific category

    # Get list of wallets to refresh
    if category_id == "all" or category_id is None:
        # Get all wallets
        tags = load_wallet_tags()
        wallets = []
        if DATA_DIR.exists():
            for filepath in sorted(DATA_DIR.glob("*.json")):
                if filepath.name in ("wallet_tags.json", "categories.json", "refresh_status.json", "excluded_wallets.json"):
                    continue
                wallets.append(filepath.stem)
    else:
        # Get wallets in specific category
        wallets = get_wallets_by_category(category_id)

    if not wallets:
        return {"status": "no_wallets", "started": []}

    # Start refresh for each wallet (if not already running)
    started = []
    already_running = []

    for wallet in wallets:
        wallet_lower = wallet.lower()

        # Check if thread is already running
        existing_thread = active_threads.get(wallet_lower)
        if existing_thread and existing_thread.is_alive():
            already_running.append(wallet_lower)
            continue

        # Check status from disk
        current = refresh_tasks.get(wallet_lower, {})
        if current.get("status") in ("fetching", "analyzing"):
            # Status says running but no thread - might be stale, start new one
            pass

        refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
        save_refresh_status(refresh_tasks)

        thread = threading.Thread(target=background_refresh, args=(wallet,), daemon=False)
        thread.start()
        active_threads[wallet_lower] = thread
        started.append(wallet_lower)

    return {
        "status": "started",
        "started": started,
        "already_running": already_running,
        "total": len(wallets)
    }


@app.get("/api/refresh-status/{wallet}")
def get_refresh_status(wallet: str):
    """Check refresh progress for a wallet."""
    wallet_lower = wallet.lower()
    status = refresh_tasks.get(wallet_lower, {"status": "idle", "detail": ""})
    return status


@app.get("/api/active-tasks")
def get_active_tasks():
    """Get all active refresh tasks."""
    active = {
        wallet: status
        for wallet, status in refresh_tasks.items()
        if status.get("status") in ("fetching", "analyzing")
    }
    return active


@app.get("/api/related-wallets/{wallet}")
def get_related_wallets(wallet: str):
    """Find wallets that have bidirectional transfers with this wallet."""
    wallet = wallet.lower()
    raw_txs = load_transactions(wallet)
    if not raw_txs:
        raise HTTPException(status_code=404, detail="No transaction data found")

    txs = filter_transactions(raw_txs)

    # Track outgoing and incoming transfers
    sent_to: dict[str, list] = {}
    received_from: dict[str, list] = {}

    for tx in txs:
        if tx.get("tx_type") != "transfer":
            continue

        frm = (tx.get("from") or "").lower()
        to = (tx.get("to") or "").lower()
        sym = tx.get("symbol", tx.get("token_symbol", "?"))
        amt = tx.get("amount", tx.get("token_amount", 0))
        usd = tx.get("amount_usd", tx.get("token_amount_usd", 0)) or 0
        ts = tx.get("timestamp", 0)

        transfer_info = {"timestamp": ts, "amount_usd": usd, "symbol": sym, "amount": amt}

        if frm == wallet and to and to != wallet:
            sent_to.setdefault(to, []).append(transfer_info)
        elif to == wallet and frm and frm != wallet:
            received_from.setdefault(frm, []).append(transfer_info)

    # Find bidirectional wallets (sent TO and received FROM)
    related = []
    bidirectional_addrs = set(sent_to.keys()) & set(received_from.keys())

    for addr in bidirectional_addrs:
        s_txs = sent_to[addr]
        r_txs = received_from[addr]

        all_timestamps = [t["timestamp"] for t in s_txs + r_txs]
        total_usd_sent = sum(t["amount_usd"] for t in s_txs)
        total_usd_received = sum(t["amount_usd"] for t in r_txs)

        # Collect unique tokens
        tokens_sent = list({t["symbol"] for t in s_txs})
        tokens_received = list({t["symbol"] for t in r_txs})

        related.append({
            "address": addr,
            "sent_count": len(s_txs),
            "received_count": len(r_txs),
            "total_transfers": len(s_txs) + len(r_txs),
            "total_usd_sent": round(total_usd_sent, 2),
            "total_usd_received": round(total_usd_received, 2),
            "tokens_sent": tokens_sent,
            "tokens_received": tokens_received,
            "first_interaction": min(all_timestamps) if all_timestamps else 0,
            "last_interaction": max(all_timestamps) if all_timestamps else 0,
        })

    related.sort(key=lambda x: x["total_transfers"], reverse=True)

    # Filter out excluded wallets and attach classification data
    classified = load_excluded_wallets()
    filtered_related = []
    excluded_in_results = []
    for rw in related:
        entry = classified.get(rw["address"])
        if entry and entry.get("is_excluded", False):
            excluded_in_results.append({**rw, "exclusion": entry})
        else:
            # Attach classification if cached (even for non-excluded)
            if entry:
                rw["classification"] = entry
            filtered_related.append(rw)

    return {
        "wallet": wallet,
        "related_count": len(filtered_related),
        "related_wallets": filtered_related,
        "excluded_count": len(excluded_in_results),
        "excluded_wallets": excluded_in_results,
    }


@app.get("/api/related-transactions/{wallet}")
def get_related_transactions(wallet: str, counterparty: str, direction: str = "all"):
    """Get transfer transactions between wallet and a specific counterparty."""
    wallet = wallet.lower()
    counterparty = counterparty.lower()

    raw_txs = load_transactions(wallet)
    if not raw_txs:
        raise HTTPException(status_code=404, detail="No transaction data found")

    txs = filter_transactions(raw_txs)
    result = []

    for tx in txs:
        if tx.get("tx_type") != "transfer":
            continue

        frm = (tx.get("from") or "").lower()
        to = (tx.get("to") or "").lower()

        is_sent = frm == wallet and to == counterparty
        is_received = to == wallet and frm == counterparty

        if direction == "sent" and not is_sent:
            continue
        if direction == "received" and not is_received:
            continue
        if direction == "all" and not (is_sent or is_received):
            continue

        result.append(format_tx_for_frontend(tx))

    result.sort(key=lambda x: x.get("timestamp", 0))
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
