import hashlib
import json
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Configuration ──────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "google/gemini-3-flash-preview"  # easy to change
DUST_THRESHOLD_USD = 1.0
CHUNK_MAX_TRANSACTIONS = 30
MAX_CONTEXT_SUMMARIES = None  # None = все "Суть дня", или число для ограничения на больших кошельках
FULL_CHRONOLOGY_COUNT = int(os.getenv("FULL_CHRONOLOGY_COUNT", 1))
CONTEXT_COMPRESSION_ENABLED = os.getenv("CONTEXT_COMPRESSION_ENABLED", "true").lower() in ("true", "1", "yes")
CONTEXT_DAILY_COUNT = int(os.getenv("CONTEXT_DAILY_COUNT", 30))
CONTEXT_WEEKLY_COUNT = int(os.getenv("CONTEXT_WEEKLY_COUNT", 30))
TIER2_GROUP_SIZE = int(os.getenv("CONTEXT_TIER2_GROUP_SIZE", 5))
TIER3_SUPER_SIZE = int(os.getenv("CONTEXT_TIER3_SUPER_SIZE", 3))
DATA_DIR = Path(__file__).parent.parent / "data"
REPORTS_DIR = Path(__file__).parent.parent / "reports"

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
- Сразу после строки «Суть дня» добавь строку оценки важности дня: \
«**Важность: N**», где N — число от 1 до 5:
  - 1 = рутина: пополнение газа, пылевые переводы, мелкое обслуживание
  - 2 = обычный: стандартные свопы, регулярные переводы
  - 3 = заметный: интересные сделки, значимые суммы, новые платформы
  - 4 = важный: крупные операции, заметные прибыли/убытки, сложные стратегии
  - 5 = ключевой: масштабные операции, смена стратегии, исключительные прибыли/убытки
- Используй эмодзи умеренно (1-2 на секцию дня) для ключевых действий \
в тексте описания и в строке «Суть дня». Примеры уместных эмодзи: \
🔄 свопы, 🌉 мосты, 💰 крупные суммы, 📈 прибыль, 📉 убыток, \
🏦 лендинг, 💸 переводы, 🎯 стратегические действия, ⚡ быстрые операции. \
Не перегружай текст эмодзи — они должны выделять только ключевые моменты.
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
def call_llm(system_prompt: str, user_prompt: str, model: str = None, max_tokens: int = 4096, plugins: list = None) -> str:
    payload = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
    }
    if plugins:
        payload["plugins"] = plugins

    max_retries = 5
    delay = 5

    for attempt in range(max_retries + 1):
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/your-username/defi-wallet-analyzer",
                "X-Title": "DeFi Wallet Analyzer",
            },
            json=payload,
            timeout=120,
        )
        if response.status_code == 429 and attempt < max_retries:
            print(f"  Rate limited, waiting {delay}s...")
            time.sleep(delay)
            delay *= 2
            continue
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


# ── Context compression ──────────────────────────────────────────────────
COMPRESS_PROMPT = """\
Сожми дневные сводки активности криптокошелька в одно краткое резюме (2-3 предложения).
Сохрани: ключевые действия, суммы в $, платформы, токены, чейны.
Не добавляй ничего от себя. Отвечай ТОЛЬКО текстом резюме, без заголовков и маркеров."""


def parse_summary_date(summary: str) -> tuple[str, str]:
    """Parse 'YYYY-MM-DD: text' into (date_str, text)."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2}):\s*(.+)$", summary)
    if match:
        return match.group(1), match.group(2)
    return "", summary


def _content_hash(texts: list[str]) -> str:
    """Generate a short hash of text content for stable cache keys."""
    content = "\n".join(texts)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _compress_via_llm(summaries_text: str) -> str:
    """Call LLM to compress a group of summaries into 2-3 sentences."""
    try:
        return call_llm(COMPRESS_PROMPT, summaries_text, max_tokens=300)
    except Exception as e:
        print(f"  Compression LLM error: {e}, using fallback")
        return summaries_text


def _compress_group(summaries: list[str], cache: dict = None) -> str:
    """Compress a group of summaries into a single text via LLM, with content-hash caching."""
    cache_key = _content_hash(summaries)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    input_text = "\n".join(f"- {s}" for s in summaries)
    compressed = _compress_via_llm(input_text).strip()

    if cache is not None:
        cache[cache_key] = compressed
    return compressed


def _get_date_range(summaries: list[str]) -> str:
    """Extract date range label from a list of summaries."""
    dates = []
    for s in summaries:
        date_str, _ = parse_summary_date(s)
        if date_str:
            dates.append(date_str)
    if not dates:
        return "?"
    if len(dates) == 1 or dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} — {dates[-1]}"


def _apply_hierarchical_compression(all_summaries: list[str], cache: dict = None) -> list[str]:
    """Apply 3-tier chunk-based compression.

    Groups are fixed from the beginning of the list (stable for caching).
    Only COMPLETE groups are compressed — incomplete groups shown as individual lines.
    This means LLM compression calls happen only every TIER2_GROUP_SIZE chunks.

    Tier 1 (newest CONTEXT_DAILY_COUNT): individual summaries as-is
    Tier 2 (next ~CONTEXT_WEEKLY_COUNT): full groups of TIER2_GROUP_SIZE → LLM compression
    Tier 3 (oldest): full groups → full super-groups of TIER3_SUPER_SIZE → double compression
    """
    total = len(all_summaries)

    # Tier 1: last N summaries shown individually
    tier1_count = min(CONTEXT_DAILY_COUNT, total)
    tier1_summaries = all_summaries[-tier1_count:]
    remaining = all_summaries[:-tier1_count] if tier1_count < total else []

    if not remaining:
        return tier1_summaries

    group_cache = cache.get("groups", {}) if cache else None
    super_cache = cache.get("super_groups", {}) if cache else None

    # Build fixed groups from the beginning (stable alignment for caching)
    groups = []
    for i in range(0, len(remaining), TIER2_GROUP_SIZE):
        groups.append(remaining[i:i + TIER2_GROUP_SIZE])

    # Split groups into Tier 2 and Tier 3
    tier2_group_count = max(1, CONTEXT_WEEKLY_COUNT // TIER2_GROUP_SIZE)
    if len(groups) <= tier2_group_count:
        tier2_groups = groups
        tier3_groups = []
    else:
        tier2_groups = groups[-tier2_group_count:]
        tier3_groups = groups[:-tier2_group_count]

    result = []

    # Tier 3: two-level compression (summaries → groups → super-groups)
    # Only compress complete groups and complete super-groups
    if tier3_groups:
        # Step 1: compress only full groups, keep incomplete as individual lines
        intermediate = []
        for group in tier3_groups:
            if len(group) == TIER2_GROUP_SIZE:
                compressed = _compress_group(group, group_cache)
                date_range = _get_date_range(group)
                intermediate.append((date_range, compressed))
            else:
                result.extend(group)

        # Step 2: form super-groups only from complete sets of TIER3_SUPER_SIZE
        full_super_count = len(intermediate) // TIER3_SUPER_SIZE
        for i in range(full_super_count):
            start = i * TIER3_SUPER_SIZE
            super_items = intermediate[start:start + TIER3_SUPER_SIZE]

            first_date = super_items[0][0].split(" — ")[0]
            last_parts = super_items[-1][0].split(" — ")
            last_date = last_parts[-1] if len(last_parts) > 1 else last_parts[0]
            date_range = f"{first_date} — {last_date}"

            super_input = [f"{dr}: {t}" for dr, t in super_items]
            cache_key = _content_hash(super_input)
            if super_cache is not None and cache_key in super_cache:
                result.append(f"{date_range}: {super_cache[cache_key]}")
            else:
                input_text = "\n".join(f"- {s}" for s in super_input)
                compressed = _compress_via_llm(input_text).strip()
                if super_cache is not None:
                    super_cache[cache_key] = compressed
                result.append(f"{date_range}: {compressed}")

        # Remaining compressed groups that don't form a full super-group
        for dr, text in intermediate[full_super_count * TIER3_SUPER_SIZE:]:
            result.append(f"{dr}: {text}")

    # Tier 2: single-level compression, only full groups
    for group in tier2_groups:
        if len(group) == TIER2_GROUP_SIZE:
            compressed = _compress_group(group, group_cache)
            date_range = _get_date_range(group)
            result.append(f"{date_range}: {compressed}")
        else:
            result.extend(group)

    # Tier 1: no compression
    result.extend(tier1_summaries)

    # Save cache back
    if cache is not None:
        if group_cache is not None:
            cache["groups"] = group_cache
        if super_cache is not None:
            cache["super_groups"] = super_cache

    return result


def build_context_for_llm(chronology_parts: list[str], compression_cache: dict = None) -> str:
    """Build LLM context from chronology parts with optional hierarchical compression.

    Args:
        chronology_parts: list of chronology texts from previous chunks
        compression_cache: dict for caching compressed summaries (mutated in-place).
            Structure: {"weekly": {"2024-W03": "..."}, "monthly": {"2024-01": "..."}}
    """
    if not chronology_parts:
        return "## Контекст предыдущей активности:\nЭто начало анализа, предыдущих данных нет."

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

        if MAX_CONTEXT_SUMMARIES is not None:
            all_summaries = all_summaries[-MAX_CONTEXT_SUMMARIES:]

        if all_summaries:
            if CONTEXT_COMPRESSION_ENABLED:
                lines = _apply_hierarchical_compression(all_summaries, compression_cache)
            else:
                lines = all_summaries
            context_sections.append(
                "## Краткий контекст предыдущей активности:\n"
                + "\n".join(f"- {s}" for s in lines)
            )

    if recent_parts:
        context_sections.append(
            "## Подробная хронология последних дней:\n\n"
            + "\n\n".join(recent_parts)
        )

    return "\n\n".join(context_sections)


# ── State management ───────────────────────────────────────────────────────
def load_state(wallet: str) -> dict:
    state_path = REPORTS_DIR / f"{wallet.lower()}_state.json"
    if state_path.exists():
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Migration from old format (no tx key tracking)
        state.setdefault("processed_tx_keys", [])
        state.setdefault("pending_tx_keys", [])
        # Migrate old calendar-based cache to chunk-based format
        cc = state.get("compression_cache", {})
        if "weekly" in cc or "monthly" in cc or not cc:
            state["compression_cache"] = {"groups": {}, "super_groups": {}}
        else:
            cc.setdefault("groups", {})
            cc.setdefault("super_groups", {})
        return state
    return {
        "chunk_index": 0,
        "chronology_parts": [],
        "processed_tx_keys": [],
        "pending_tx_keys": [],
        "compression_cache": {"groups": {}, "super_groups": {}},
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
    compression_cache = state.get("compression_cache", {"weekly": {}, "monthly": {}})

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

        # Build context: compressed summaries + last N full chronologies
        context = build_context_for_llm(chronology_parts, compression_cache)

        # Save context for inspection
        context_path = REPORTS_DIR / f"{wallet.lower()}_context.md"
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(f"# LLM Context for chunk {i + 1}/{total_chunks}\n\n{context}")

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
                "compression_cache": compression_cache,
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
            "compression_cache": compression_cache,
        })
        print(f"  Done.")

    # Batch complete: move pending keys to processed
    processed_keys.update(batch_keys)
    save_state(wallet, {
        "chunk_index": 0,
        "chronology_parts": chronology_parts,
        "processed_tx_keys": list(processed_keys),
        "pending_tx_keys": [],
        "compression_cache": compression_cache,
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
