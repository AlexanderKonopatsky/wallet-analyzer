import io
import json
import sys
import threading
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

# Background task status tracking: {wallet: {status, detail}}
refresh_tasks: dict[str, dict] = {}


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
    try:
        # Step 1: Fetch transactions
        refresh_tasks[wallet] = {"status": "fetching", "detail": "Fetching transactions from API..."}

        existing_data = load_existing_data(wallet)
        existing_txs = {tx["tx_hash"]: tx for tx in existing_data["transactions"]}

        all_transactions = fetch_all_transactions(wallet, existing_txs)
        if all_transactions:
            all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            save_data(wallet, all_transactions)

        # Step 2: Analyze
        refresh_tasks[wallet] = {"status": "analyzing", "detail": "Analyzing transactions with AI..."}
        run_analysis_pipeline(wallet)

        refresh_tasks[wallet] = {"status": "done", "detail": "Refresh complete!"}
    except Exception as e:
        refresh_tasks[wallet] = {"status": "error", "detail": str(e)}


# ── API Endpoints ─────────────────────────────────────────────────────────────


@app.get("/api/wallets")
def list_wallets():
    """List all tracked wallets."""
    tags = load_wallet_tags()
    wallets = []
    if DATA_DIR.exists():
        for filepath in sorted(DATA_DIR.glob("*.json")):
            if filepath.name == "wallet_tags.json":
                continue
            address = filepath.stem
            meta = get_wallet_meta(address)
            if meta:
                report_path = REPORTS_DIR / f"{address}.md"
                meta["has_report"] = report_path.exists()
                meta["tag"] = tags.get(address.lower(), "")
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

    # Check if already running
    current = refresh_tasks.get(wallet_lower, {})
    if current.get("status") in ("fetching", "analyzing"):
        return {"status": "already_running", "detail": current.get("detail", "")}

    refresh_tasks[wallet_lower] = {"status": "fetching", "detail": "Starting..."}
    thread = threading.Thread(target=background_refresh, args=(wallet,), daemon=True)
    thread.start()

    return {"status": "started"}


@app.get("/api/refresh-status/{wallet}")
def get_refresh_status(wallet: str):
    """Check refresh progress for a wallet."""
    wallet_lower = wallet.lower()
    status = refresh_tasks.get(wallet_lower, {"status": "idle", "detail": ""})
    return status


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
