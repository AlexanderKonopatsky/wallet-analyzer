import json
import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "google/gemini-3-pro-preview"  # easy to change
DUST_THRESHOLD_USD = 1.0
CHUNK_MAX_TRANSACTIONS = 30
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
- После описания каждого дня ОБЯЗАТЕЛЬНО добавь строку \
«**Суть дня:** ...» — одно предложение, резюмирующее главное действие/цель дня.
- Не придумывай то, чего нет в данных.
- Отвечай СТРОГО в формате двух секций:

## Хронология
(описание по дням)

## Резюме
(краткое резюме всей активности до текущего момента, не более 300 слов — \
оно будет использовано как контекст для следующей порции транзакций)\
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


# ── Load & filter ──────────────────────────────────────────────────────────
def load_transactions(wallet: str) -> list:
    filepath = DATA_DIR / f"{wallet.lower()}.json"
    if not filepath.exists():
        print(f"Файл не найден: {filepath}")
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
def call_llm(system_prompt: str, user_prompt: str) -> str:
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def parse_llm_response(text: str) -> tuple[str, str]:
    """Split LLM response into (chronology, summary) sections."""
    chronology = ""
    summary = ""

    # Try to find ## Хронология and ## Резюме sections
    chron_match = re.search(
        r"## Хронология\s*\n(.*?)(?=## Резюме|$)", text, re.DOTALL | re.IGNORECASE
    )
    summary_match = re.search(r"## Резюме\s*\n(.*)", text, re.DOTALL | re.IGNORECASE)

    if chron_match:
        chronology = chron_match.group(1).strip()
    else:
        chronology = text.strip()

    if summary_match:
        summary = summary_match.group(1).strip()

    return chronology, summary


# ── State management ───────────────────────────────────────────────────────
def load_state(wallet: str) -> dict:
    state_path = REPORTS_DIR / f"{wallet.lower()}_state.json"
    if state_path.exists():
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"chunk_index": 0, "rolling_summary": "", "chronology_parts": []}


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
    # Load
    raw_txs = load_transactions(wallet)
    if not raw_txs:
        return

    txs = filter_transactions(raw_txs)
    print(f"Найдено {len(raw_txs)} транзакций, после фильтрации: {len(txs)}")

    day_groups = group_by_days(txs)
    chunks = make_chunks(day_groups)
    total_chunks = len(chunks)
    print(f"Сформировано {total_chunks} чанков для анализа\n")

    # Load existing state (for resume)
    state = load_state(wallet)
    start_chunk = state["chunk_index"]
    rolling_summary = state["rolling_summary"]
    chronology_parts = state["chronology_parts"]

    if start_chunk > 0:
        print(f"Продолжаем с чанка {start_chunk + 1}/{total_chunks} (найдено сохранённое состояние)\n")

    for i in range(start_chunk, total_chunks):
        chunk = chunks[i]
        days_list = list(chunk.keys())
        days_range = f"{days_list[0]} — {days_list[-1]}" if len(days_list) > 1 else days_list[0]
        tx_count = sum(len(txs) for txs in chunk.values())
        print(f"Обработка чанка {i + 1}/{total_chunks} (дни: {days_range}, транзакций: {tx_count})...")

        # Format transactions for this chunk
        formatted_lines = []
        for day, day_txs in chunk.items():
            for tx in day_txs:
                formatted_lines.append(format_tx_for_llm(tx))

        tx_text = "\n".join(formatted_lines)

        # Build prompt
        if rolling_summary:
            context = f"## Контекст предыдущей активности:\n{rolling_summary}"
        else:
            context = "## Контекст предыдущей активности:\nЭто начало анализа, предыдущих данных нет."

        user_prompt = f"""{context}

## Транзакции для анализа:
{tx_text}

Опиши хронологию действий пользователя по дням и обнови резюме."""

        # Call LLM
        try:
            response = call_llm(SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            print(f"  Ошибка API: {e}")
            save_state(wallet, {
                "chunk_index": i,
                "rolling_summary": rolling_summary,
                "chronology_parts": chronology_parts,
            })
            print(f"  Состояние сохранено, можно продолжить позже.")
            return

        chronology, summary = parse_llm_response(response)

        if chronology:
            chronology_parts.append(chronology)
        if summary:
            rolling_summary = summary

        # Save state after each chunk
        save_state(wallet, {
            "chunk_index": i + 1,
            "rolling_summary": rolling_summary,
            "chronology_parts": chronology_parts,
        })
        print(f"  Готово.")

    # Save final report
    report_path = save_report(wallet, chronology_parts)
    print(f"\nАнализ завершён! Результат: {report_path}")


def main() -> None:
    if not OPENROUTER_API_KEY:
        print("Ошибка: укажите OPENROUTER_API_KEY в .env файле")
        return

    wallet = input("Введите адрес кошелька: ").strip()
    if not wallet:
        print("Адрес не может быть пустым.")
        return

    analyze_wallet(wallet)


if __name__ == "__main__":
    main()
