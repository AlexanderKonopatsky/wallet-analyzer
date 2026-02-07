import json
import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "google/gemini-3-flash-preview"  # easy to change
DUST_THRESHOLD_USD = 1.0
CHUNK_MAX_TRANSACTIONS = 30
MAX_CONTEXT_SUMMARIES = None  # None = все "Суть дня", или число для ограничения на больших кошельках
FULL_CHRONOLOGY_COUNT = int(os.getenv("FULL_CHRONOLOGY_COUNT", 1))
DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")

SYSTEM_PROMPT = """\
Ты — аналитик DeFi-транзакций. Твоя задача — описать на русском языке, \
что делал пользователь криптокошелька, опираясь на список его транзакций.

Правила:
- Пиши хронологию по дням. Каждый день — отдельный заголовок (### YYYY-MM-DD).
- Описывай действия человекочитаемо: «занял», «погасил долг», «обменял», \
«добавил ликвидность», «вывел из пула», «перевёл на другой адрес», \
«перебросил через мост» и т.д.
- Указывай суммы, токены, платформы и чейны.
- Если несколько операций — логическая цепочка (например: занял → обменял → \
погасил долг на другой платформе), объясняй общий смысл этой последовательности.
- Учитывай контекст предыдущей активности (если он есть) для понимания общей стратегии.
- После описания каждого дня ОБЯЗАТЕЛЬНО добавь строку \
«**Суть дня:** ...» — одно предложение, резюмирующее главное действие/цель дня. \
Обязательно указывай ключевые суммы в долларах.
- Не придумывай то, чего нет в данных.
"""


# ── Helpers ────────────────────────────────────────────────────────────────
def fmt_amount(amount: float) -> str:
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.2f}K"
    if amount >= 1:
        return f"{amount:.2f}"
    return f"{amount:.6f}"


def fmt_usd(usd: float) -> str:
    return f"${fmt_amount(usd)}"


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def ts_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_date(date_str: str) -> datetime | None:
    """Parse a date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def filter_by_period(txs: list, date_from: datetime | None, date_to: datetime | None) -> list:
    """Filter transactions by date range (inclusive)."""
    filtered = []
    for tx in txs:
        ts = tx.get("timestamp", 0)
        tx_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if date_from and tx_dt < date_from:
            continue
        if date_to and tx_dt >= date_to.replace(hour=23, minute=59, second=59):
            continue
        filtered.append(tx)
    return filtered


def prompt_period(txs: list) -> tuple[datetime | None, datetime | None]:
    """Ask the user to select an analysis period. Returns (date_from, date_to) or (None, None) for all."""
    timestamps = [tx.get("timestamp", 0) for tx in txs if tx.get("timestamp")]
    if not timestamps:
        return None, None

    min_date = ts_to_date(min(timestamps))
    max_date = ts_to_date(max(timestamps))

    print(f"\nAvailable transaction period: {min_date} — {max_date}")
    print("Select analysis period:")
    print("  1) All period")
    print("  2) Last 7 days")
    print("  3) Last 30 days")
    print("  4) Custom date range")

    choice = input("Your choice (1-4) [1]: ").strip() or "1"

    if choice == "1":
        return None, None

    if choice == "2":
        date_to = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
        date_from = date_to - timedelta(days=7)
        print(f"Period: {date_from.strftime('%Y-%m-%d')} — {date_to.strftime('%Y-%m-%d')}")
        return date_from, date_to

    if choice == "3":
        date_to = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
        date_from = date_to - timedelta(days=30)
        print(f"Period: {date_from.strftime('%Y-%m-%d')} — {date_to.strftime('%Y-%m-%d')}")
        return date_from, date_to

    if choice == "4":
        date_from_str = input(f"Start date (YYYY-MM-DD) [{min_date}]: ").strip() or min_date
        date_to_str = input(f"End date (YYYY-MM-DD) [{max_date}]: ").strip() or max_date

        date_from = parse_date(date_from_str)
        date_to = parse_date(date_to_str)

        if date_from is None:
            print(f"Invalid start date format: {date_from_str}, using {min_date}")
            date_from = parse_date(min_date)
        if date_to is None:
            print(f"Invalid end date format: {date_to_str}, using {max_date}")
            date_to = parse_date(max_date)

        print(f"Period: {date_from.strftime('%Y-%m-%d')} — {date_to.strftime('%Y-%m-%d')}")
        return date_from, date_to

    return None, None


def get_tx_key(tx: dict) -> str:
    """Get a unique key for a transaction (for incremental processing)."""
    for field in ("id", "tx_hash", "hash", "transaction_hash"):
        if tx.get(field):
            return str(tx[field])
    # Fallback: composite key from core fields
    parts = [
        str(tx.get("timestamp", "")),
        tx.get("chain", ""),
        tx.get("tx_type", ""),
        str(tx.get("token0_amount", tx.get("amount", ""))),
        tx.get("token0_symbol", tx.get("symbol", "")),
    ]
    return "|".join(parts)


# ── Load & filter ──────────────────────────────────────────────────────────
def load_transactions(wallet: str) -> list:
    filepath = DATA_DIR / f"{wallet.lower()}.json"
    if not filepath.exists():
        print(f"File not found: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("transactions", [])


def get_tx_usd(tx: dict) -> float:
    """Extract the main USD value from a transaction."""
    tx_type = tx.get("tx_type", "")

    if tx_type == "swap":
        return max(
            tx.get("token0_amount_usd", 0) or 0,
            tx.get("token1_amount_usd", 0) or 0,
        )
    if tx_type == "lp":
        return (tx.get("token0_amount_usd", 0) or 0) + (
            tx.get("token1_amount_usd", 0) or 0
        )
    if tx_type in ("lending", "wrap"):
        return tx.get("amount_usd", 0) or 0
    if tx_type == "transfer":
        return tx.get("amount_usd", tx.get("token_amount_usd", 0)) or 0
    if tx_type == "bridge":
        return tx.get("amount_usd", 0) or 0
    # nft_transfer — keep regardless of value
    if tx_type == "nft_transfer":
        return float("inf")
    return 0


def filter_transactions(txs: list, threshold: float = DUST_THRESHOLD_USD) -> list:
    filtered = []
    for tx in txs:
        if tx.get("tx_type") == "contract_interaction":
            continue
        if get_tx_usd(tx) < threshold:
            continue
        filtered.append(tx)
    return filtered


# ── Format for LLM ────────────────────────────────────────────────────────
def format_tx_for_llm(tx: dict) -> str:
    ts = fmt_ts(tx.get("timestamp", 0))
    chain = tx.get("chain", "?")
    tx_type = tx.get("tx_type", "?")

    if tx_type == "swap":
        t0 = tx.get("token0_symbol", "?")
        t0a = fmt_amount(tx.get("token0_amount", 0))
        t0u = fmt_usd(tx.get("token0_amount_usd", 0))
        t1 = tx.get("token1_symbol", "?")
        t1a = fmt_amount(tx.get("token1_amount", 0))
        dex = tx.get("dex", "?") or "DEX"
        return f"[{ts}] SWAP {chain}: {t0a} {t0} ({t0u}) → {t1a} {t1} on {dex}"

    if tx_type == "lending":
        action = tx.get("action", "?")
        sym = tx.get("symbol", "?")
        amt = fmt_amount(tx.get("amount", 0))
        usd = fmt_usd(tx.get("amount_usd", 0))
        platform = tx.get("platform", "?")
        hf = tx.get("health_factor", 0)
        hf_str = f" [HF={hf}]" if hf and hf < 100 else ""
        return f"[{ts}] LENDING {chain}: {action} {amt} {sym} ({usd}) on {platform}{hf_str}"

    if tx_type == "transfer":
        sym = tx.get("symbol", tx.get("token_symbol", "?"))
        amt = fmt_amount(tx.get("amount", tx.get("token_amount", 0)))
        usd = fmt_usd(tx.get("amount_usd", tx.get("token_amount_usd", 0)))
        frm = tx.get("from", "")
        to = tx.get("to", "")
        from_label = tx.get("from_label", "") or (
            f"{frm[:6]}...{frm[-4:]}" if len(frm) > 10 else frm
        )
        to_label = tx.get("to_label", "") or (
            f"{to[:6]}...{to[-4:]}" if len(to) > 10 else to
        )
        return f"[{ts}] TRANSFER {chain}: {amt} {sym} ({usd}) from {from_label} to {to_label}"

    if tx_type == "lp":
        lp_type = tx.get("type", "?")
        t0 = tx.get("token0_symbol", "?")
        t0a = fmt_amount(tx.get("token0_amount", 0))
        t1 = tx.get("token1_symbol", "?")
        t1a = fmt_amount(tx.get("token1_amount", 0))
        dex = tx.get("dex", "") or "DEX"
        total_usd = fmt_usd(
            (tx.get("token0_amount_usd", 0) or 0)
            + (tx.get("token1_amount_usd", 0) or 0)
        )
        lb = tx.get("lower_bound")
        ub = tx.get("upper_bound")
        range_str = f" range [{lb:.0f}-{ub:.0f}]" if lb and ub else ""
        return f"[{ts}] LP {chain}: {lp_type} {t0a} {t0} + {t1a} {t1} ({total_usd}) on {dex}{range_str}"

    if tx_type == "bridge":
        sym = tx.get("token_symbol", "?")
        amt = fmt_amount(tx.get("amount", 0))
        usd = fmt_usd(tx.get("amount_usd", 0))
        from_chain = tx.get("from_chain", "?") or "?"
        to_chain = tx.get("to_chain", "?") or "?"
        platform = tx.get("platform", "?")
        return f"[{ts}] BRIDGE {chain}: {amt} {sym} ({usd}) {from_chain} → {to_chain} via {platform}"

    if tx_type == "wrap":
        action = tx.get("action", "?")
        amt = fmt_amount(tx.get("amount", 0))
        sym = tx.get("symbol", "?")
        usd = fmt_usd(tx.get("amount_usd", 0))
        return f"[{ts}] WRAP {chain}: {action} {amt} {sym} ({usd})"

    if tx_type == "nft_transfer":
        name = tx.get("nft_name", "?")
        token_id = tx.get("nft_token_id", "?")
        frm = tx.get("from_label", "?")
        to = tx.get("to_label", "?")
        return f"[{ts}] NFT {chain}: {name} #{token_id} from {frm} to {to}"

    return f"[{ts}] {tx_type.upper()} {chain}"


# ── Chunking ───────────────────────────────────────────────────────────────
def group_by_days(txs: list) -> OrderedDict:
    """Group transactions by date (oldest first)."""
    txs_sorted = sorted(txs, key=lambda x: x.get("timestamp", 0))
    days = OrderedDict()
    for tx in txs_sorted:
        day = ts_to_date(tx.get("timestamp", 0))
        days.setdefault(day, []).append(tx)
    return days


def make_chunks(day_groups: OrderedDict, max_txs: int = CHUNK_MAX_TRANSACTIONS) -> list:
    """Split day groups into chunks of ~max_txs transactions."""
    chunks = []
    current_chunk = OrderedDict()
    current_count = 0

    for day, txs in day_groups.items():
        # If this single day exceeds the limit, it goes alone
        if len(txs) > max_txs and current_count > 0:
            chunks.append(current_chunk)
            current_chunk = OrderedDict()
            current_count = 0

        if current_count + len(txs) > max_txs and current_count > 0:
            chunks.append(current_chunk)
            current_chunk = OrderedDict()
            current_count = 0

        current_chunk[day] = txs
        current_count += len(txs)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ── LLM ────────────────────────────────────────────────────────────────────
def call_llm(system_prompt: str, user_prompt: str, model: str = None, max_tokens: int = 4096) -> str:
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def parse_llm_response(text: str) -> str:
    """Extract chronology from LLM response."""
    # Remove optional "## Хронология" header if present
    text = re.sub(r"^##\s*Хронология\s*\n", "", text.strip(), flags=re.IGNORECASE)
    return text.strip()


def extract_day_summaries(chronology: str) -> list:
    """Extract 'date: Суть дня' pairs from chronology text."""
    summaries = []
    current_date = None
    for line in chronology.split("\n"):
        date_match = re.match(r"^###\s+(\d{4}-\d{2}-\d{2})", line)
        if date_match:
            current_date = date_match.group(1)
        summary_match = re.match(r"\*\*Суть дня:\*\*\s*(.+)", line)
        if summary_match and current_date:
            summaries.append(f"{current_date}: {summary_match.group(1)}")
            current_date = None
    return summaries


# ── State management ───────────────────────────────────────────────────────
def load_state(wallet: str) -> dict:
    state_path = REPORTS_DIR / f"{wallet.lower()}_state.json"
    if state_path.exists():
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Migration from old format (no tx key tracking)
        state.setdefault("processed_tx_keys", [])
        state.setdefault("pending_tx_keys", [])
        return state
    return {
        "chunk_index": 0,
        "chronology_parts": [],
        "processed_tx_keys": [],
        "pending_tx_keys": [],
    }


def save_state(wallet: str, state: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    state_path = REPORTS_DIR / f"{wallet.lower()}_state.json"
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def save_report(wallet: str, chronology_parts: list) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{wallet.lower()}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Хронология кошелька {wallet}\n\n")
        f.write("\n\n".join(chronology_parts))
    return str(report_path)


# ── Main pipeline ──────────────────────────────────────────────────────────
def analyze_wallet(wallet: str) -> None:
    # Load all transactions
    raw_txs = load_transactions(wallet)
    if not raw_txs:
        return

    txs = filter_transactions(raw_txs)
    print(f"Found {len(raw_txs)} transactions, after filtering: {len(txs)}")

    # Ask user to select analysis period
    date_from, date_to = prompt_period(txs)
    if date_from or date_to:
        txs = filter_by_period(txs, date_from, date_to)
        print(f"After filtering by period: {len(txs)} transactions")
        if not txs:
            print("No transactions for the selected period.")
            return

    # Load existing state
    state = load_state(wallet)
    chronology_parts = state["chronology_parts"]
    processed_keys = set(state["processed_tx_keys"])
    pending_keys = set(state.get("pending_tx_keys", []))
    start_chunk = state["chunk_index"]

    # Determine which transactions need processing
    resuming = bool(pending_keys and start_chunk > 0)

    if resuming:
        # Resume interrupted batch: re-select the same transactions
        new_txs = [tx for tx in txs if get_tx_key(tx) in pending_keys]
        print(f"Continuing interrupted analysis: {len(new_txs)} transactions")
    else:
        # Find genuinely new transactions
        new_txs = [tx for tx in txs if get_tx_key(tx) not in processed_keys]
        start_chunk = 0

        if not new_txs:
            # Migration: old state had no processed_tx_keys tracking
            if not processed_keys and chronology_parts:
                all_keys = [get_tx_key(tx) for tx in txs]
                save_state(wallet, {
                    "chunk_index": 0,
                    "chronology_parts": chronology_parts,
                    "processed_tx_keys": all_keys,
                    "pending_tx_keys": [],
                })
                print("State migrated to new format. No new transactions found.")
            else:
                print("No new transactions found.")
            return

        print(f"Found {len(new_txs)} new transactions for analysis")

    # Track keys of current batch (for resume capability)
    batch_keys = [get_tx_key(tx) for tx in new_txs]

    day_groups = group_by_days(new_txs)
    chunks = make_chunks(day_groups)
    total_chunks = len(chunks)
    print(f"Formed {total_chunks} chunks for analysis\n")

    if resuming:
        print(f"Continuing from chunk {start_chunk + 1}/{total_chunks}\n")

    for i in range(start_chunk, total_chunks):
        chunk = chunks[i]
        days_list = list(chunk.keys())
        days_range = f"{days_list[0]} — {days_list[-1]}" if len(days_list) > 1 else days_list[0]
        tx_count = sum(len(dtxs) for dtxs in chunk.values())
        print(f"Processing chunk {i + 1}/{total_chunks} (days: {days_range}, transactions: {tx_count})...")

        # Format transactions for this chunk
        formatted_lines = []
        for day, day_txs in chunk.items():
            for tx in day_txs:
                formatted_lines.append(format_tx_for_llm(tx))

        tx_text = "\n".join(formatted_lines)

        # Build context: compact "Суть дня" summaries + last N full chronologies
        if chronology_parts:
            context_sections = []

            # Split into old (summaries only) and recent (full text)
            if len(chronology_parts) > FULL_CHRONOLOGY_COUNT:
                old_parts = chronology_parts[:-FULL_CHRONOLOGY_COUNT]
                recent_parts = chronology_parts[-FULL_CHRONOLOGY_COUNT:]
            else:
                old_parts = []
                recent_parts = chronology_parts

            # Extract day summaries from older chronologies
            if old_parts:
                all_summaries = []
                for part in old_parts:
                    all_summaries.extend(extract_day_summaries(part))

                if MAX_CONTEXT_SUMMARIES is not None:
                    all_summaries = all_summaries[-MAX_CONTEXT_SUMMARIES:]

                if all_summaries:
                    context_sections.append(
                        "## Краткий контекст предыдущей активности:\n"
                        + "\n".join(f"- {s}" for s in all_summaries)
                    )

            # Add full recent chronologies
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

        # Call LLM
        try:
            response = call_llm(SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            print(f"  API Error: {e}")
            save_state(wallet, {
                "chunk_index": i,
                "chronology_parts": chronology_parts,
                "processed_tx_keys": list(processed_keys),
                "pending_tx_keys": batch_keys,
            })
            print(f"  State saved, you can continue later.")
            return

        chronology = parse_llm_response(response)

        if chronology:
            chronology_parts.append(chronology)

        # Save state after each chunk
        save_state(wallet, {
            "chunk_index": i + 1,
            "chronology_parts": chronology_parts,
            "processed_tx_keys": list(processed_keys),
            "pending_tx_keys": batch_keys,
        })
        print(f"  Done.")

    # Batch complete: move pending keys to processed
    processed_keys.update(batch_keys)
    save_state(wallet, {
        "chunk_index": 0,
        "chronology_parts": chronology_parts,
        "processed_tx_keys": list(processed_keys),
        "pending_tx_keys": [],
    })

    report_path = save_report(wallet, chronology_parts)
    print(f"\nAnalysis completed! Result: {report_path}")


def main() -> None:
    if not OPENROUTER_API_KEY:
        print("Error: specify OPENROUTER_API_KEY in .env file")
        return

    wallet = input("Enter wallet address: ").strip()
    if not wallet:
        print("Address cannot be empty.")
        return

    analyze_wallet(wallet)


if __name__ == "__main__":
    main()
