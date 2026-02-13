import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
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
from fastapi.responses import FileResponse
from pydantic import BaseModel

from main import fetch_all_transactions, load_existing_data, save_data
from db import init_db, get_db, User, Database
from auth import (
    create_verification_code,
    verify_code,
    create_jwt_token,
    get_current_user,
    verify_google_token,
    get_or_create_user_from_google
)
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
from portfolio import analyze_portfolio, load_cached_portfolio, is_cache_valid
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
from debank_parser import get_protocol_type

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
TAGS_FILE = DATA_DIR / "wallet_tags.json"
REFRESH_STATUS_FILE = DATA_DIR / "refresh_status.json"
EXCLUDED_WALLETS_FILE = DATA_DIR / "excluded_wallets.json"
HIDDEN_WALLETS_FILE = DATA_DIR / "hidden_wallets.json"
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

# Payment settings
ONECLICK_API_BASE = "https://1click.chaindefuser.com"
ONECLICK_CACHE_TTL_SECONDS = 5 * 60
PAYMENT_RECEIVE_ADDRESS = (os.getenv("RECEIVE_ADDRESS") or "").strip()
PAYMENT_RECEIVE_TOKEN = (os.getenv("RECEIVE_TOKEN") or "base:usdc").strip()
PAYMENT_STATUS_DESCRIPTIONS = {
    "PENDING_DEPOSIT": "Waiting for deposit...",
    "KNOWN_DEPOSIT_TX": "Deposit detected, confirming...",
    "INCOMPLETE_DEPOSIT": "Deposit incomplete",
    "PROCESSING": "Processing swap...",
    "SUCCESS": "Payment received!",
    "FAILED": "Payment failed",
    "REFUNDED": "Refunded to sender",
}
_oneclick_tokens_cache: list | None = None
_oneclick_tokens_cache_time = 0.0
_oneclick_tokens_lock = threading.Lock()

# Profile generation settings
PROFILE_MODEL = os.getenv("PROFILE_MODEL", "google/gemini-3-pro-preview")
PROFILE_MAX_TOKENS = int(os.getenv("PROFILE_MAX_TOKENS", 15192))
PROFILE_COST_BASE_USD = float(os.getenv("PROFILE_COST_BASE_USD", "0.03686"))
PROFILE_COST_PER_WORD_USD = float(os.getenv("PROFILE_COST_PER_WORD_USD", "0.000005846"))
PROFILE_SYSTEM_PROMPT = """Ты — опытный ончейн-аналитик. Тебе дана подробная хронология активности крипто-кошелька.

Прочитай отчёт целиком и составь глубокий профиль владельца. Не следуй шаблону — каждый кошелёк уникален, и профиль должен отражать именно то, что делает этого владельца особенным. Пиши о том, что действительно бросается в глаза и заслуживает внимания.

Не пересказывай транзакции. Анализируй поведение, читай между строк, делай выводы. Ссылайся на конкретные события из отчёта как доказательства. Пиши на русском, используй markdown."""

# Wallet classification settings
# Note: DeBank classification uses a lock, so parallel requests are serialized anyway
AUTO_CLASSIFY_ENABLED = os.getenv("AUTO_CLASSIFY_ENABLED", "false").lower() == "true"
AUTO_CLASSIFY_BATCH_SIZE = int(os.getenv("AUTO_CLASSIFY_BATCH_SIZE", 1))

# Auto-refresh settings
AUTO_REFRESH_ENABLED = os.getenv("AUTO_REFRESH_ENABLED", "false").lower() == "true"
AUTO_REFRESH_TIME = os.getenv("AUTO_REFRESH_TIME", "23:00")

# DeBank classification lock (Playwright is not thread-safe)
debank_lock = threading.Lock()

# Background task status tracking: {wallet: {status, detail, thread_id}}
refresh_tasks: dict[str, dict] = {}
# Active threads: {wallet: Thread object}
active_threads: dict[str, threading.Thread] = {}


def get_user_data_dir(user_id: int) -> Path:
    """Get user-specific data directory."""
    user_dir = DATA_DIR / "users" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def load_wallet_tags(user_id: int) -> dict:
    """Load wallet tags/names from user's file."""
    user_dir = get_user_data_dir(user_id)
    tags_file = user_dir / "wallet_tags.json"
    if tags_file.exists():
        with open(tags_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_wallet_tags(user_id: int, tags: dict) -> None:
    """Save wallet tags/names to user's file."""
    user_dir = get_user_data_dir(user_id)
    tags_file = user_dir / "wallet_tags.json"
    with open(tags_file, "w", encoding="utf-8") as f:
        json.dump(tags, f, indent=2, ensure_ascii=False)


def load_refresh_status(user_id: int, cleanup: bool = False, db: Database = None) -> dict:
    """Load refresh task statuses from user's file.

    Args:
        user_id: User ID
        cleanup: If True, remove statuses for wallets user no longer owns
        db: Database instance (required if cleanup=True)
    """
    user_dir = get_user_data_dir(user_id)
    status_file = user_dir / "refresh_status.json"
    if status_file.exists():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                statuses = json.load(f)

            # Cleanup orphaned statuses if requested
            if cleanup and db:
                cleaned = {}
                for wallet, status in statuses.items():
                    # Keep if user owns wallet or if it's a classify task key
                    if wallet.startswith("classify_") or check_wallet_ownership(db, user_id, wallet):
                        cleaned[wallet] = status
                    else:
                        print(f"[Cleanup] Removing orphaned status for {wallet} (user {user_id})")

                # Save if anything was removed
                if len(cleaned) < len(statuses):
                    save_refresh_status(user_id, cleaned)
                return cleaned

            return statuses
        except Exception:
            return {}
    return {}


def save_refresh_status(user_id: int, status_dict: dict) -> None:
    """Save refresh task statuses to user's file."""
    user_dir = get_user_data_dir(user_id)
    status_file = user_dir / "refresh_status.json"
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status_dict, f, indent=2, ensure_ascii=False)


def load_excluded_wallets() -> dict:
    """Load excluded wallet addresses from global file (shared DeBank cache)."""
    if EXCLUDED_WALLETS_FILE.exists():
        try:
            with open(EXCLUDED_WALLETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_excluded_wallets(excluded: dict) -> None:
    """Save excluded wallet addresses to global file (shared DeBank cache)."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(EXCLUDED_WALLETS_FILE, "w", encoding="utf-8") as f:
        json.dump(excluded, f, indent=2, ensure_ascii=False)


def load_hidden_wallets(user_id: int) -> set:
    """Load hidden wallet addresses for specific user."""
    user_dir = get_user_data_dir(user_id)
    hidden_file = user_dir / "hidden_wallets.json"
    if hidden_file.exists():
        try:
            with open(hidden_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("hidden", []))
        except Exception:
            return set()
    return set()


def save_hidden_wallets(user_id: int, hidden: set) -> None:
    """Save hidden wallet addresses for specific user."""
    user_dir = get_user_data_dir(user_id)
    hidden_file = user_dir / "hidden_wallets.json"
    with open(hidden_file, "w", encoding="utf-8") as f:
        json.dump({"hidden": list(hidden)}, f, indent=2, ensure_ascii=False)


def load_analysis_consents(user_id: int) -> set:
    """Load wallets with explicit user consent for paid analysis."""
    user_dir = get_user_data_dir(user_id)
    consent_file = user_dir / "analysis_consents.json"

    if consent_file.exists():
        try:
            with open(consent_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {w.lower() for w in data.get("wallets", [])}
        except Exception:
            return set()

    # Backward compatibility: infer consent from completed/in-progress historical analysis statuses.
    statuses = load_refresh_status(user_id)
    inferred = {
        wallet.lower()
        for wallet, status in statuses.items()
        if not wallet.startswith("classify_")
        and isinstance(status, dict)
        and status.get("status") in ("done", "analyzing", "error")
    }
    if inferred:
        save_analysis_consents(user_id, inferred)
    return inferred


def save_analysis_consents(user_id: int, consents: set) -> None:
    """Persist wallets with explicit user consent for paid analysis."""
    user_dir = get_user_data_dir(user_id)
    consent_file = user_dir / "analysis_consents.json"
    normalized = sorted({w.lower() for w in consents if w})
    with open(consent_file, "w", encoding="utf-8") as f:
        json.dump({"wallets": normalized}, f, indent=2, ensure_ascii=False)


def grant_analysis_consent(user_id: int, wallet: str) -> None:
    """Mark wallet as consented for future bulk/auto analysis."""
    wallet_lower = wallet.lower()
    consents = load_analysis_consents(user_id)
    if wallet_lower in consents:
        return
    consents.add(wallet_lower)
    save_analysis_consents(user_id, consents)


def load_user_balance(user_id: int) -> dict:
    """Load user balance and transaction history."""
    user_dir = get_user_data_dir(user_id)
    balance_file = user_dir / "balance.json"
    if balance_file.exists():
        try:
            with open(balance_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"balance": 0.0, "transactions": []}
    return {"balance": 0.0, "transactions": []}


def save_user_balance(user_id: int, balance_data: dict) -> None:
    """Save user balance and transaction history."""
    user_dir = get_user_data_dir(user_id)
    balance_file = user_dir / "balance.json"
    with open(balance_file, "w", encoding="utf-8") as f:
        json.dump(balance_data, f, indent=2, ensure_ascii=False)


def ensure_user_balance_initialized(user_id: int, initial_balance: float = 1.0) -> None:
    """Initialize user balance if not already initialized."""
    balance_data = load_user_balance(user_id)
    if not balance_data.get("transactions"):  # New user
        balance_data["balance"] = initial_balance
        balance_data["transactions"] = [{
            "type": "signup_bonus",
            "amount": initial_balance,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
        save_user_balance(user_id, balance_data)


def load_user_payments(user_id: int) -> list:
    """Load user payments history."""
    user_dir = get_user_data_dir(user_id)
    payments_file = user_dir / "payments.json"
    if payments_file.exists():
        try:
            with open(payments_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_user_payments(user_id: int, payments: list) -> None:
    """Save user payments history."""
    user_dir = get_user_data_dir(user_id)
    payments_file = user_dir / "payments.json"
    with open(payments_file, "w", encoding="utf-8") as f:
        json.dump(payments, f, indent=2, ensure_ascii=False)


def create_user_payment(user_id: int, payment: dict) -> dict:
    """Create and persist a payment record for user."""
    payments = load_user_payments(user_id)
    payment["id"] = f"pay_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    payment["createdAt"] = datetime.now(timezone.utc).isoformat()
    payment["status"] = "PENDING_DEPOSIT"
    payment["completedAt"] = None
    payments.append(payment)
    save_user_payments(user_id, payments)
    return payment


def parse_positive_amount(value) -> float:
    """Parse positive numeric amount from string/number."""
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def apply_payment_credit_if_needed(user_id: int, payment: dict) -> dict:
    """Credit user balance for successful payment exactly once."""
    if payment.get("status") != "SUCCESS":
        return payment
    if payment.get("balanceCredited") is True:
        return payment

    payment_id = payment.get("id")
    if not payment_id:
        return payment

    balance_data = load_user_balance(user_id)
    transactions = balance_data.get("transactions", [])

    existing_topup = next(
        (
            tx for tx in transactions
            if tx.get("type") == "payment_topup" and tx.get("payment_id") == payment_id
        ),
        None,
    )
    if existing_topup:
        credited_at = existing_topup.get("timestamp") or datetime.now(timezone.utc).isoformat()
        amount = parse_positive_amount(existing_topup.get("amount"))
        updated = update_user_payment(user_id, payment_id, {
            "balanceCredited": True,
            "balanceCreditedAt": credited_at,
            "balanceCreditedAmount": amount,
        })
        return updated or {**payment, "balanceCredited": True, "balanceCreditedAt": credited_at, "balanceCreditedAmount": amount}

    credit_amount = (
        parse_positive_amount(payment.get("amountOut"))
        or parse_positive_amount(payment.get("amount"))
        or parse_positive_amount(payment.get("originAmount"))
    )
    if credit_amount <= 0:
        return payment

    credit_amount = round(credit_amount, 2)
    timestamp = datetime.now(timezone.utc).isoformat()

    current_balance = parse_positive_amount(balance_data.get("balance"))
    balance_data["balance"] = round(current_balance + credit_amount, 2)
    balance_data.setdefault("transactions", []).append({
        "type": "payment_topup",
        "amount": credit_amount,
        "payment_id": payment_id,
        "symbol": payment.get("destinationSymbol"),
        "timestamp": timestamp,
    })
    save_user_balance(user_id, balance_data)

    updated = update_user_payment(user_id, payment_id, {
        "balanceCredited": True,
        "balanceCreditedAt": timestamp,
        "balanceCreditedAmount": credit_amount,
    })
    return updated or {**payment, "balanceCredited": True, "balanceCreditedAt": timestamp, "balanceCreditedAmount": credit_amount}


def get_user_payment(user_id: int, payment_id: str) -> dict | None:
    """Get payment by ID for specific user."""
    payments = load_user_payments(user_id)
    return next((payment for payment in payments if payment.get("id") == payment_id), None)


def update_user_payment(user_id: int, payment_id: str, updates: dict) -> dict | None:
    """Update payment by ID for specific user."""
    payments = load_user_payments(user_id)
    for idx, payment in enumerate(payments):
        if payment.get("id") != payment_id:
            continue
        payments[idx].update(updates)
        save_user_payments(user_id, payments)
        return payments[idx]
    return None


def oneclick_headers() -> dict:
    """Build 1Click API headers."""
    headers = {"Content-Type": "application/json"}
    oneclick_jwt = (os.getenv("ONECLICK_JWT") or "").strip()
    if oneclick_jwt:
        headers["Authorization"] = f"Bearer {oneclick_jwt}"
    return headers


def oneclick_request(endpoint: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    """Make request to 1Click API."""
    url = f"{ONECLICK_API_BASE}{endpoint}"
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=oneclick_headers(),
            json=payload,
            timeout=25,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"1Click API request failed: {exc}") from exc

    if not response.ok:
        error_text = response.text[:500]
        raise HTTPException(
            status_code=502,
            detail=f"1Click API error ({response.status_code}): {error_text}",
        )

    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="1Click API returned invalid JSON") from exc


def get_cached_oneclick_tokens() -> list:
    """Get cached 1Click token list."""
    global _oneclick_tokens_cache, _oneclick_tokens_cache_time
    with _oneclick_tokens_lock:
        if (
            _oneclick_tokens_cache is not None
            and time.time() - _oneclick_tokens_cache_time < ONECLICK_CACHE_TTL_SECONDS
        ):
            return _oneclick_tokens_cache

        tokens = oneclick_request("/v0/tokens")
        if not isinstance(tokens, list):
            raise HTTPException(status_code=502, detail="Unexpected 1Click /v0/tokens response")

        _oneclick_tokens_cache = tokens
        _oneclick_tokens_cache_time = time.time()
        return tokens


def parse_token_id(token_id: str) -> tuple[str, str]:
    """Parse token string in format chain:token."""
    parts = token_id.split(":")
    if len(parts) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid token format: {token_id}. Expected chain:token",
        )
    return parts[0].lower(), ":".join(parts[1:])


def find_token(tokens: list, chain: str, token_id: str) -> dict | None:
    """Find token by chain + symbol/address/id."""
    chain_lower = chain.lower()
    token_lower = token_id.lower()

    for token in tokens:
        blockchain = (token.get("blockchain") or token.get("chain") or "").lower()
        if blockchain != chain_lower:
            continue

        symbol = (token.get("symbol") or "").lower()
        contract_address = (token.get("contractAddress") or token.get("address") or "").lower()
        asset_id = (token.get("assetId") or "").lower()
        defuse_asset_id = (token.get("defuseAssetId") or "").lower()

        if (
            symbol == token_lower
            or contract_address == token_lower
            or asset_id == token_lower
            or token_lower in defuse_asset_id
        ):
            return token
    return None


def to_base_units(amount_str: str, decimals: int) -> str:
    """Convert decimal amount to base units."""
    amount = (amount_str or "").strip()
    if not re.fullmatch(r"\d*\.?\d+", amount):
        raise HTTPException(status_code=400, detail=f"Invalid amount: {amount_str}")

    if "." in amount:
        int_part, frac_part = amount.split(".", 1)
    else:
        int_part, frac_part = amount, ""

    int_part = int_part or "0"
    frac_padded = (frac_part + ("0" * decimals))[:decimals]
    normalized = f"{int(int_part)}{frac_padded}".lstrip("0")
    return normalized or "0"


def from_base_units(base_units: str, decimals: int) -> str:
    """Convert base units to decimal amount."""
    raw = str(base_units or "0")
    if not raw.isdigit():
        return "0"
    if raw == "0":
        return "0"

    padded = raw.rjust(decimals + 1, "0")
    int_part = padded[:-decimals] if decimals > 0 else padded
    frac_part = padded[-decimals:].rstrip("0") if decimals > 0 else ""
    return f"{int_part}.{frac_part}" if frac_part else int_part


def get_chain_type(chain_id: str) -> str:
    """Map chain id to address validator type."""
    chain = chain_id.lower()
    if chain == "near":
        return "near"
    if chain in ("sol", "solana"):
        return "solana"
    if chain == "aptos":
        return "aptos"
    if chain == "sui":
        return "sui"
    if chain == "ton":
        return "ton"
    if chain == "stellar":
        return "stellar"
    if chain == "tron":
        return "tron"
    return "evm"


def is_valid_address(address: str, chain_type: str) -> bool:
    """Validate wallet address by chain type."""
    if not address:
        return False
    if chain_type == "near":
        return bool(re.fullmatch(r"[a-z0-9._-]{2,64}", address) or re.fullmatch(r"[0-9a-f]{64}", address))
    if chain_type == "evm":
        return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", address))
    if chain_type == "solana":
        return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address))
    if chain_type in ("aptos", "sui"):
        return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", address))
    if chain_type == "ton":
        return bool(
            re.fullmatch(r"[a-zA-Z0-9_-]{48}", address)
            or re.fullmatch(r"[UEk][Qf][a-zA-Z0-9_-]{46}", address)
        )
    if chain_type == "tron":
        return bool(re.fullmatch(r"T[a-zA-Z0-9]{33}", address))
    if chain_type == "stellar":
        return bool(re.fullmatch(r"G[A-Z0-9]{55}", address))
    return len(address) > 5


def payment_config() -> tuple[str, tuple[str, str]]:
    """Return payment destination config."""
    if not PAYMENT_RECEIVE_ADDRESS:
        raise HTTPException(
            status_code=503,
            detail="Payment service is not configured: RECEIVE_ADDRESS is missing",
        )
    try:
        destination_chain, destination_token = parse_token_id(PAYMENT_RECEIVE_TOKEN)
    except HTTPException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Payment service is not configured: invalid RECEIVE_TOKEN ({PAYMENT_RECEIVE_TOKEN})",
        ) from exc
    return PAYMENT_RECEIVE_ADDRESS, (destination_chain, destination_token)


def oneclick_get_quote(
    *,
    dry: bool,
    origin_asset: str,
    destination_asset: str,
    amount: str,
    recipient: str,
    refund_to: str,
    slippage_tolerance: int = 100,
) -> dict:
    """Get 1Click quote."""
    deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    payload = {
        "dry": dry,
        "swapType": "EXACT_INPUT",
        "slippageTolerance": slippage_tolerance,
        "originAsset": origin_asset,
        "depositType": "ORIGIN_CHAIN",
        "destinationAsset": destination_asset,
        "amount": str(amount),
        "refundTo": refund_to,
        "refundType": "ORIGIN_CHAIN",
        "recipient": recipient,
        "recipientType": "DESTINATION_CHAIN",
        "deadline": deadline,
        "quoteWaitingTimeMs": 5000,
    }
    response = oneclick_request("/v0/quote", method="POST", payload=payload)
    if not isinstance(response, dict):
        raise HTTPException(status_code=502, detail="Unexpected 1Click quote response")
    return response


def oneclick_execution_status(deposit_address: str) -> dict:
    """Get 1Click execution status by deposit address."""
    url = f"{ONECLICK_API_BASE}/v0/status"
    try:
        response = requests.get(
            url,
            headers=oneclick_headers(),
            params={"depositAddress": deposit_address},
            timeout=25,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"1Click API request failed: {exc}") from exc

    if not response.ok:
        error_text = response.text[:500]
        raise HTTPException(
            status_code=502,
            detail=f"1Click API error ({response.status_code}): {error_text}",
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="1Click API returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Unexpected 1Click status response")
    return data


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


# ── Helpers ───────────────────────────────────────────────────────────────────


def ensure_data_backup_access(current_user: User) -> None:
    """Allow backup/import only for configured admin emails (or any user if unset)."""
    if not DATA_BACKUP_ADMIN_EMAILS:
        return
    if current_user.email.lower() not in DATA_BACKUP_ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Backup/import access denied")


def has_running_background_tasks() -> bool:
    """Check if any refresh/analysis thread is currently running."""
    return any(thread and thread.is_alive() for thread in active_threads.values())


def create_data_backup_archive() -> Path:
    """Create ZIP archive with full data folder and return archive path."""
    DATA_BACKUP_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_path = DATA_BACKUP_ARCHIVE_DIR / f"data_backup_{timestamp}.zip"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in DATA_DIR.rglob("*"):
            if item.is_dir():
                continue
            if item.resolve() == archive_path.resolve():
                continue
            rel_path = item.relative_to(DATA_DIR)
            arcname = (Path("data") / rel_path).as_posix()
            archive.write(item, arcname=arcname)

    return archive_path


def safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    """Safely extract zip archive while preventing path traversal."""
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            member_name = member.filename.replace("\\", "/")
            if not member_name or member_name.endswith("/"):
                continue

            member_path = Path(member_name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise HTTPException(status_code=400, detail="Archive contains unsafe paths")

            output_path = (target_root / member_path).resolve()
            if not str(output_path).startswith(str(target_root)):
                raise HTTPException(status_code=400, detail="Archive contains unsafe paths")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, open(output_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def resolve_data_import_root(extract_root: Path) -> Path:
    """Detect where imported data files are located inside extracted archive."""
    direct_data = extract_root / "data"
    if direct_data.is_dir():
        return direct_data

    top_level_entries = [
        entry for entry in extract_root.iterdir()
        if entry.name != "__MACOSX"
    ]

    top_level_dirs = [entry for entry in top_level_entries if entry.is_dir()]
    top_level_files = [entry for entry in top_level_entries if entry.is_file()]

    if len(top_level_dirs) == 1 and not top_level_files:
        nested_data = top_level_dirs[0] / "data"
        if nested_data.is_dir():
            return nested_data
        return top_level_dirs[0]

    return extract_root


def copy_tree(src_dir: Path, dst_dir: Path, skip_top_level_dirs: set[str] | None = None) -> int:
    """Copy directory tree from src to dst. Returns copied file count."""
    skip_top_level_dirs = {name.lower() for name in (skip_top_level_dirs or set())}
    copied_files = 0
    for src in src_dir.rglob("*"):
        rel = src.relative_to(src_dir)
        if rel.parts and rel.parts[0].lower() in skip_top_level_dirs:
            continue
        dst = dst_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied_files += 1
    return copied_files


def clear_directory(dir_path: Path, keep_names: set[str] | None = None) -> None:
    """Remove all files/folders inside a directory."""
    keep_names = {name.lower() for name in (keep_names or set())}
    if not dir_path.exists():
        return
    for child in dir_path.iterdir():
        if child.name.lower() in keep_names:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def resolve_backup_archive_path(filename: str) -> Path:
    """Resolve and validate backup archive path inside data/backups."""
    raw_name = (filename or "").strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="Filename is required")

    candidate = Path(raw_name)
    if candidate.name != raw_name or candidate.suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    archive_path = (DATA_BACKUP_ARCHIVE_DIR / raw_name).resolve()
    backup_root = DATA_BACKUP_ARCHIVE_DIR.resolve()
    if not str(archive_path).startswith(str(backup_root)):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    return archive_path


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

    print(f"🚀 Начинаю анализ кошелька {wallet[:10]}...")
    print(f"📦 Найдено {len(new_txs)} новых транзакций, разбито на {total_chunks} chunks")
    if resuming:
        print(f"♻️ Продолжаю с chunk {start_chunk + 1}/{total_chunks}")

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
        print(f"📊 Анализ chunk {i + 1}/{total_chunks} для кошелька {wallet[:10]}... ({len(formatted_lines)} транзакций, {current_percent}%)")
        if progress_callback:
            progress_callback(i + 1, total_chunks, current_percent)

        response = call_llm(SYSTEM_PROMPT, user_prompt)
        chronology = parse_llm_response(response)

        # Report progress after LLM call
        completed_percent = int(((i + 1) / total_chunks) * 100)
        print(f"✅ Chunk {i + 1}/{total_chunks} обработан ({completed_percent}%)")
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
    print(f"🎉 Анализ кошелька {wallet[:10]}... завершен! Отчет сохранен.")


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
        current_balance = balance_data.get("balance", 0.0)
        if current_balance < analysis_cost:
            print(f"[Refresh] Insufficient balance for {wallet_lower}: need ${analysis_cost}, have ${current_balance}", flush=True)
            user_refresh_tasks = load_refresh_status(user_id)
            user_refresh_tasks[wallet_lower] = {"status": "error", "detail": f"Insufficient balance: need ${analysis_cost}, have ${current_balance}"}
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

        # Step 3: Auto-classify related wallets in background (if enabled)
        if AUTO_CLASSIFY_ENABLED:
            print(f"[Refresh] Step 3: Starting auto-classification for {wallet_lower}", flush=True)
            classify_thread = threading.Thread(
                target=classify_related_wallets_background,
                args=(wallet_lower,),
                daemon=False  # Non-daemon so it continues after browser closes
            )
            task_key = f"classify_{wallet_lower}"
            active_threads[task_key] = classify_thread
            classify_thread.start()
        else:
            print(f"[Refresh] Step 3: Auto-classification disabled (AUTO_CLASSIFY_ENABLED=false)", flush=True)

    except Exception as e:
        print(f"[Refresh] ERROR for {wallet_lower}: {e}", flush=True)
        user_refresh_tasks = load_refresh_status(user_id)
        user_refresh_tasks[wallet_lower] = {"status": "error", "detail": str(e)}
        save_refresh_status(user_id, user_refresh_tasks)
        refresh_tasks[wallet_lower] = user_refresh_tasks[wallet_lower]
    finally:
        # Clean up thread reference (but not classify thread - it runs independently)
        active_threads.pop(wallet_lower, None)


# ── Startup: Initialize refresh tasks tracking ──────────────────────────────

# Note: refresh_tasks will now be loaded per-user when needed
# Global dict still used for in-memory tracking of active background tasks
refresh_tasks: dict[str, dict] = {}


# ── Request/Response Models ──────────────────────────────────────────────────


class RequestCodeRequest(BaseModel):
    """Request body for /api/auth/request-code"""
    email: str


class VerifyCodeRequest(BaseModel):
    """Request body for /api/auth/verify-code"""
    email: str
    code: str


class GoogleAuthRequest(BaseModel):
    """Request body for /api/auth/google"""
    token: str


# ── Auth Endpoints ────────────────────────────────────────────────────────────


class PaymentQuoteRequest(BaseModel):
    """Request body for /api/quote"""
    amount: str
    originToken: str
    refundAddress: str


class PaymentCreateRequest(BaseModel):
    """Request body for /api/payment/create"""
    amount: str
    originToken: str
    refundAddress: str
    originAmount: str


@app.get("/api/auth/config")
async def auth_config():
    """Return public auth config (Google Client ID) for frontend."""
    from auth import GOOGLE_CLIENT_ID
    return {"google_client_id": GOOGLE_CLIENT_ID or ""}


@app.post("/api/auth/request-code")
async def request_code(body: RequestCodeRequest, db: Database = Depends(get_db)):
    """Send verification code to email."""
    try:
        email = body.email.lower().strip()
        create_verification_code(db, email)
        return {"status": "sent", "email": email}
    except Exception as e:
        print(f"[Auth] Error sending code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/verify-code")
async def verify_code_endpoint(body: VerifyCodeRequest, db: Database = Depends(get_db)):
    """Verify code and return JWT token."""
    email = body.email.lower().strip()
    user = verify_code(db, email, body.code)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    # Initialize balance for new users
    ensure_user_balance_initialized(user.id)

    token = create_jwt_token(user.id)

    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email
        }
    }


@app.post("/api/auth/google")
async def google_auth(body: GoogleAuthRequest, db: Database = Depends(get_db)):
    """Authenticate with Google OAuth token."""
    # Verify Google token
    google_info = verify_google_token(body.token)

    if not google_info:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    # Get or create user
    user = get_or_create_user_from_google(db, google_info)

    # Initialize balance for new users
    ensure_user_balance_initialized(user.id)

    # Create JWT token
    token = create_jwt_token(user.id)

    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email
        }
    }


@app.get("/api/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user info."""
    return {
        "id": current_user.id,
        "email": current_user.email
    }


# ── API Endpoints ─────────────────────────────────────────────────────────────


@app.get("/api/settings")
def get_settings():
    """Get application settings."""
    return {
        "auto_classify_enabled": AUTO_CLASSIFY_ENABLED,
        "auto_classify_batch_size": AUTO_CLASSIFY_BATCH_SIZE,
        "auto_refresh_enabled": AUTO_REFRESH_ENABLED,
        "auto_refresh_time": AUTO_REFRESH_TIME,
        "data_backup_restricted": bool(DATA_BACKUP_ADMIN_EMAILS),
        "data_import_max_mb": DATA_IMPORT_MAX_MB,
    }


@app.get("/api/user/balance")
def get_user_balance(current_user: User = Depends(get_current_user)):
    """Get current user's balance."""
    balance_data = load_user_balance(current_user.id)
    return {
        "balance": balance_data.get("balance", 0.0),
        "currency": "USD"
    }


@app.post("/api/user/balance/deduct")
def deduct_balance(
    amount: float,
    current_user: User = Depends(get_current_user)
):
    """Deduct amount from user's balance (for analysis cost)."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    balance_data = load_user_balance(current_user.id)
    current_balance = balance_data.get("balance", 0.0)

    if current_balance < amount:
        raise HTTPException(status_code=402, detail="Insufficient balance")

    # Deduct amount and save transaction
    balance_data["balance"] = round(current_balance - amount, 2)
    if "transactions" not in balance_data:
        balance_data["transactions"] = []

    balance_data["transactions"].append({
        "type": "deduction",
        "amount": amount,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    save_user_balance(current_user.id, balance_data)

    return {"balance": balance_data["balance"]}


@app.get("/api/admin/data-backup")
def download_data_backup(current_user: User = Depends(get_current_user)):
    """Download full backup of data/ directory as zip archive."""
    ensure_data_backup_access(current_user)

    if not data_backup_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Backup/import is already in progress")

    try:
        archive_path = create_data_backup_archive()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create backup: {exc}") from exc
    finally:
        data_backup_lock.release()

    return FileResponse(
        path=str(archive_path),
        media_type="application/zip",
        filename=archive_path.name,
    )


@app.get("/api/admin/data-backups")
def list_data_backups(current_user: User = Depends(get_current_user)):
    """List existing backup archives in data/backups."""
    ensure_data_backup_access(current_user)

    if not data_backup_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Backup/import is already in progress")

    try:
        DATA_BACKUP_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        backups = []
        for archive in DATA_BACKUP_ARCHIVE_DIR.glob("*.zip"):
            stat = archive.stat()
            backups.append({
                "filename": archive.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        backups.sort(key=lambda item: item["updated_at"], reverse=True)
        return {"backups": backups}
    finally:
        data_backup_lock.release()


@app.get("/api/admin/data-backups/{filename}")
def download_existing_data_backup(filename: str, current_user: User = Depends(get_current_user)):
    """Download an existing backup archive from data/backups."""
    ensure_data_backup_access(current_user)

    if not data_backup_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Backup/import is already in progress")

    try:
        archive_path = resolve_backup_archive_path(filename)
        if not archive_path.exists() or not archive_path.is_file():
            raise HTTPException(status_code=404, detail="Backup archive not found")
        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=archive_path.name,
        )
    finally:
        data_backup_lock.release()


@app.delete("/api/admin/data-backups/{filename}")
def delete_data_backup(filename: str, current_user: User = Depends(get_current_user)):
    """Delete a backup archive from data/backups."""
    ensure_data_backup_access(current_user)

    if not data_backup_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Backup/import is already in progress")

    try:
        archive_path = resolve_backup_archive_path(filename)
        if not archive_path.exists() or not archive_path.is_file():
            raise HTTPException(status_code=404, detail="Backup archive not found")
        archive_path.unlink()
        return {"status": "deleted", "filename": archive_path.name}
    finally:
        data_backup_lock.release()


@app.post("/api/admin/data-import")
async def import_data_backup(
    request: Request,
    mode: str = "replace",
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Import data/ directory from uploaded zip archive (replace or merge)."""
    ensure_data_backup_access(current_user)

    normalized_mode = (mode or "replace").lower()
    if normalized_mode not in {"replace", "merge"}:
        raise HTTPException(status_code=400, detail="Invalid mode. Use 'replace' or 'merge'")

    if has_running_background_tasks():
        raise HTTPException(
            status_code=409,
            detail="Stop active refresh/analysis tasks before importing backup",
        )

    if not data_backup_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Backup/import is already in progress")

    try:
        with tempfile.TemporaryDirectory(prefix="data-import-", dir=str(PROJECT_ROOT)) as tmp:
            tmp_dir = Path(tmp)
            upload_path = tmp_dir / "upload.zip"

            total_bytes = 0
            with open(upload_path, "wb") as uploaded_file:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > DATA_IMPORT_MAX_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Archive is too large (max {DATA_IMPORT_MAX_MB} MB)",
                        )
                    uploaded_file.write(chunk)

            if total_bytes == 0:
                raise HTTPException(status_code=400, detail="Request body is empty")

            if not zipfile.is_zipfile(upload_path):
                raise HTTPException(status_code=400, detail="Uploaded file must be a valid ZIP archive")

            extract_dir = tmp_dir / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(upload_path, extract_dir)

            import_root = resolve_data_import_root(extract_dir)
            source_files = [item for item in import_root.rglob("*") if item.is_file()]
            if not source_files:
                raise HTTPException(status_code=400, detail="Archive does not contain data files")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if normalized_mode == "replace":
                # Keep local backup history on server during restore.
                clear_directory(DATA_DIR, keep_names={"backups"})
                DATA_DIR.mkdir(parents=True, exist_ok=True)

            # Do not overwrite local backup archive store from imported snapshot.
            copied_files = copy_tree(import_root, DATA_DIR, skip_top_level_dirs={"backups"})

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        DATA_BACKUP_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Reload in-memory DB state from restored users.json.
        db.load()
        refresh_tasks.clear()

        return {
            "status": "ok",
            "mode": normalized_mode,
            "imported_files": copied_files,
            "size_bytes": total_bytes,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to import backup: {exc}") from exc
    finally:
        data_backup_lock.release()


@app.get("/api/tokens")
def get_payment_tokens(current_user: User = Depends(get_current_user)):
    """List supported payment tokens grouped by chain."""
    _ = current_user
    tokens = get_cached_oneclick_tokens()
    stablecoins = {"USDC", "USDT", "DAI"}

    grouped = {}
    filtered_count = 0
    for token in tokens:
        symbol = str(token.get("symbol") or "").upper()
        if symbol not in stablecoins:
            continue
        filtered_count += 1

        chain = token.get("blockchain") or token.get("chain") or "unknown"
        if chain not in grouped:
            grouped[chain] = []
        grouped[chain].append({
            "symbol": token.get("symbol"),
            "name": token.get("name") or "",
            "decimals": token.get("decimals"),
            "chain": chain,
            "defuseAssetId": token.get("defuseAssetId") or token.get("assetId"),
            "contractAddress": token.get("contractAddress") or token.get("address"),
        })

    print(f"[/api/tokens] Filtered {filtered_count} stablecoins across {len(grouped)} chains")
    return {"tokens": grouped}


@app.post("/api/quote")
def get_payment_quote(
    body: PaymentQuoteRequest,
    current_user: User = Depends(get_current_user),
):
    """Get dry quote for payment."""
    _ = current_user
    receive_address, (dest_chain, dest_token_id) = payment_config()

    amount = body.amount.strip()
    origin_token = body.originToken.strip()
    refund_address = body.refundAddress.strip()
    if not amount or not origin_token or not refund_address:
        raise HTTPException(
            status_code=400,
            detail="amount, originToken, and refundAddress are required",
        )

    tokens = get_cached_oneclick_tokens()
    source_chain, source_token_id = parse_token_id(origin_token)

    from_token = find_token(tokens, source_chain, source_token_id)
    if not from_token:
        raise HTTPException(status_code=400, detail=f"Token not found: {origin_token}")

    to_token = find_token(tokens, dest_chain, dest_token_id)
    if not to_token:
        raise HTTPException(status_code=500, detail="Destination token is not configured correctly")

    chain_type = get_chain_type(source_chain)
    if not is_valid_address(refund_address, chain_type):
        raise HTTPException(status_code=400, detail=f"Invalid refund address for {source_chain}")

    from_decimals = int(from_token.get("decimals") or 0)
    to_decimals = int(to_token.get("decimals") or 0)
    origin_amount_base = to_base_units(amount, from_decimals)
    if origin_amount_base == "0":
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")

    quote_response = oneclick_get_quote(
        dry=True,
        origin_asset=from_token.get("defuseAssetId") or from_token.get("assetId"),
        destination_asset=to_token.get("defuseAssetId") or to_token.get("assetId"),
        amount=origin_amount_base,
        recipient=receive_address,
        refund_to=refund_address,
    )
    quote_data = quote_response.get("quote", quote_response)

    return {
        "originToken": origin_token,
        "originSymbol": from_token.get("symbol"),
        "originChain": source_chain,
        "originAmount": amount,
        "originDecimals": from_decimals,
        "destinationAmount": from_base_units(quote_data.get("amountOut") or "0", to_decimals),
        "destinationSymbol": to_token.get("symbol"),
        "destinationChain": dest_chain,
        "feeUsd": quote_data.get("feeUsd"),
    }


@app.post("/api/payment/create")
def create_payment_endpoint(
    body: PaymentCreateRequest,
    current_user: User = Depends(get_current_user),
):
    """Create payment and return deposit address."""
    receive_address, (dest_chain, dest_token_id) = payment_config()

    amount = body.amount.strip()
    origin_token = body.originToken.strip()
    refund_address = body.refundAddress.strip()
    origin_amount = body.originAmount.strip()
    if not amount or not origin_token or not refund_address or not origin_amount:
        raise HTTPException(
            status_code=400,
            detail="amount, originToken, refundAddress, and originAmount are required",
        )

    tokens = get_cached_oneclick_tokens()
    source_chain, source_token_id = parse_token_id(origin_token)

    from_token = find_token(tokens, source_chain, source_token_id)
    if not from_token:
        raise HTTPException(status_code=400, detail=f"Token not found: {origin_token}")

    to_token = find_token(tokens, dest_chain, dest_token_id)
    if not to_token:
        raise HTTPException(status_code=500, detail="Destination token is not configured")

    chain_type = get_chain_type(source_chain)
    if not is_valid_address(refund_address, chain_type):
        raise HTTPException(status_code=400, detail=f"Invalid refund address for {source_chain}")

    from_decimals = int(from_token.get("decimals") or 0)
    to_decimals = int(to_token.get("decimals") or 0)
    origin_amount_base = to_base_units(origin_amount, from_decimals)
    if origin_amount_base == "0":
        raise HTTPException(status_code=400, detail="originAmount must be greater than 0")

    quote_response = oneclick_get_quote(
        dry=False,
        origin_asset=from_token.get("defuseAssetId") or from_token.get("assetId"),
        destination_asset=to_token.get("defuseAssetId") or to_token.get("assetId"),
        amount=origin_amount_base,
        recipient=receive_address,
        refund_to=refund_address,
    )
    quote_data = quote_response.get("quote", quote_response)
    deposit_address = quote_data.get("depositAddress")
    if not deposit_address:
        raise HTTPException(status_code=502, detail="No deposit address received from payment provider")

    payment = create_user_payment(current_user.id, {
        "amount": amount,
        "originAmount": origin_amount,
        "originAmountBase": origin_amount_base,
        "originToken": origin_token,
        "originSymbol": from_token.get("symbol"),
        "originChain": source_chain,
        "originDecimals": from_decimals,
        "destinationToken": PAYMENT_RECEIVE_TOKEN,
        "destinationSymbol": to_token.get("symbol"),
        "depositAddress": deposit_address,
        "refundAddress": refund_address,
        "amountOut": from_base_units(quote_data.get("amountOut") or "0", to_decimals),
        "swapDetails": None,
        "balanceCredited": False,
    })

    return {
        "id": payment["id"],
        "depositAddress": payment["depositAddress"],
        "originAmount": payment["originAmount"],
        "originSymbol": payment["originSymbol"],
        "originChain": payment["originChain"],
        "amountOut": payment["amountOut"],
        "destinationSymbol": payment["destinationSymbol"],
        "status": payment["status"],
    }


@app.get("/api/payment/{payment_id}/status")
def get_payment_status(
    payment_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get payment status by ID."""
    payment = get_user_payment(current_user.id, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    terminal_statuses = {"SUCCESS", "FAILED", "REFUNDED"}
    if payment.get("status") in terminal_statuses:
        if payment.get("status") == "SUCCESS":
            payment = apply_payment_credit_if_needed(current_user.id, payment)
        return {
            "id": payment["id"],
            "status": payment["status"],
            "statusDescription": PAYMENT_STATUS_DESCRIPTIONS.get(payment["status"], payment["status"]),
            "originAmount": payment.get("originAmount"),
            "originSymbol": payment.get("originSymbol"),
            "originChain": payment.get("originChain"),
            "amountOut": payment.get("amountOut"),
            "destinationSymbol": payment.get("destinationSymbol"),
            "depositAddress": payment.get("depositAddress"),
            "refundAddress": payment.get("refundAddress"),
            "createdAt": payment.get("createdAt"),
            "completedAt": payment.get("completedAt"),
            "swapDetails": payment.get("swapDetails"),
        }

    try:
        api_status = oneclick_execution_status(payment["depositAddress"])
        new_status = api_status.get("status")
        updates = {}
        if new_status and new_status != payment.get("status"):
            updates["status"] = new_status
            if new_status in terminal_statuses:
                updates["completedAt"] = datetime.now(timezone.utc).isoformat()
        if api_status.get("swapDetails"):
            updates["swapDetails"] = api_status.get("swapDetails")
        if updates:
            payment = update_user_payment(current_user.id, payment_id, updates) or payment
        if payment.get("status") == "SUCCESS":
            payment = apply_payment_credit_if_needed(current_user.id, payment)
    except HTTPException as api_error:
        if payment.get("status") == "SUCCESS":
            payment = apply_payment_credit_if_needed(current_user.id, payment)
        return {
            "id": payment["id"],
            "status": payment.get("status"),
            "statusDescription": PAYMENT_STATUS_DESCRIPTIONS.get(payment.get("status"), payment.get("status")),
            "originAmount": payment.get("originAmount"),
            "originSymbol": payment.get("originSymbol"),
            "originChain": payment.get("originChain"),
            "amountOut": payment.get("amountOut"),
            "destinationSymbol": payment.get("destinationSymbol"),
            "depositAddress": payment.get("depositAddress"),
            "refundAddress": payment.get("refundAddress"),
            "createdAt": payment.get("createdAt"),
            "completedAt": payment.get("completedAt"),
            "swapDetails": payment.get("swapDetails"),
            "apiError": api_error.detail,
        }

    return {
        "id": payment["id"],
        "status": payment.get("status"),
        "statusDescription": PAYMENT_STATUS_DESCRIPTIONS.get(payment.get("status"), payment.get("status")),
        "originAmount": payment.get("originAmount"),
        "originSymbol": payment.get("originSymbol"),
        "originChain": payment.get("originChain"),
        "amountOut": payment.get("amountOut"),
        "destinationSymbol": payment.get("destinationSymbol"),
        "depositAddress": payment.get("depositAddress"),
        "refundAddress": payment.get("refundAddress"),
        "createdAt": payment.get("createdAt"),
        "completedAt": payment.get("completedAt"),
        "swapDetails": payment.get("swapDetails"),
    }


@app.get("/api/payments")
def list_user_payments(current_user: User = Depends(get_current_user)):
    """List payment history for current user."""
    payments = load_user_payments(current_user.id)
    payments_sorted = sorted(payments, key=lambda payment: payment.get("createdAt", ""), reverse=True)
    return {"payments": payments_sorted}


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


# ── Portfolio Analysis ────────────────────────────────────────────────────────


@app.get("/api/portfolio/{wallet}")
def get_portfolio(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get portfolio analysis for a wallet. Data is shared globally (cached). Returns cached if valid."""
    wallet = wallet.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    filepath = DATA_DIR / f"{wallet}.json"

    # If transaction data doesn't exist, return 404
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="No transaction data found")

    if is_cache_valid(wallet):
        cached = load_cached_portfolio(wallet)
        if cached:
            return cached

    return analyze_portfolio(wallet)


@app.post("/api/portfolio/{wallet}/refresh")
def refresh_portfolio(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Force recompute portfolio analysis (user must own wallet)."""
    wallet = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    filepath = DATA_DIR / f"{wallet}.json"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="No transaction data found")

    return analyze_portfolio(wallet)


# ── Wallet Exclusion (classification) ─────────────────────────────────────────


def classify_wallet_address(address: str, context: str = "") -> dict:
    """Use DeBank to classify whether a wallet is a known contract/protocol or personal wallet.

    Uses a lock to ensure only one DeBank request at a time (Playwright is not thread-safe).
    """
    # Acquire lock to prevent parallel Playwright instances
    with debank_lock:
        try:
            print(f"[Classify DeBank] Fetching info for {address}...")
            result = get_protocol_type(address, timeout=30000)

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                print(f"[Classify DeBank] Failed to fetch info: {error_msg}")
                return {
                    "is_excluded": False,
                    "label": "unknown",
                    "name": "",
                    "reasoning": f"DeBank fetch failed: {error_msg}",
                }

            protocol = result.get("protocol")
            balance = result.get("balance")

            # If protocol is found, it's a contract/protocol (exclude it)
            if protocol:
                print(f"[Classify DeBank] Found protocol: {protocol}")
                reasoning = f"DeBank identifies this as a protocol: {protocol}"
                if balance:
                    reasoning += f" (Balance: {balance})"

                return {
                    "is_excluded": True,
                    "label": "contract",
                    "name": protocol,
                    "reasoning": reasoning,
                }
            else:
                # No protocol = personal wallet
                print(f"[Classify DeBank] No protocol found, likely personal wallet")
                reasoning = "DeBank shows no protocol tag - appears to be a personal wallet"
                if balance:
                    reasoning += f" (Balance: {balance})"

                return {
                    "is_excluded": False,
                    "label": "personal",
                    "name": "",
                    "reasoning": reasoning,
                }

        except Exception as e:
            print(f"[Classify DeBank] Classification failed for {address}: {e}")
            return {
                "is_excluded": False,
                "label": "unknown",
                "name": "",
                "reasoning": f"DeBank classification error: {str(e)}",
            }


def classify_related_wallets_background(wallet: str) -> None:
    """Background task: classify all related wallets for a given wallet.

    This runs automatically after wallet refresh/analysis and continues
    even if the user closes the browser.
    """
    wallet_lower = wallet.lower()
    task_key = f"classify_{wallet_lower}"

    try:
        print(f"[Auto-classify] Starting background classification for {wallet_lower}")

        # Verify data file exists before proceeding
        data_file = DATA_DIR / f"{wallet_lower}.json"
        if not data_file.exists():
            print(f"[Auto-classify] Data file not found for {wallet_lower}, skipping classification")
            refresh_tasks[task_key] = {
                "status": "error",
                "detail": "Transaction data not found",
                "progress": "0/0"
            }
            save_refresh_status(refresh_tasks)
            return

        # Update status
        refresh_tasks[task_key] = {
            "status": "classifying",
            "detail": "Finding related wallets...",
            "progress": "0/0"
        }
        save_refresh_status(refresh_tasks)

        # Load transactions and find related wallets
        raw_txs = load_transactions(wallet_lower)
        if not raw_txs:
            print(f"[Auto-classify] No transactions found for {wallet_lower}")
            refresh_tasks[task_key] = {
                "status": "done",
                "detail": "No transactions found",
                "progress": "0/0"
            }
            save_refresh_status(refresh_tasks)
            return

        txs = filter_transactions(raw_txs)

        # Track related addresses (same logic as /api/related-wallets)
        sent_to: dict[str, list] = {}
        received_from: dict[str, list] = {}

        for tx in txs:
            if tx.get("tx_type") != "transfer":
                continue

            frm = (tx.get("from") or "").lower()
            to = (tx.get("to") or "").lower()

            if frm == wallet_lower and to and to != wallet_lower:
                sent_to.setdefault(to, []).append(tx)
            elif to == wallet_lower and frm and frm != wallet_lower:
                received_from.setdefault(frm, []).append(tx)

        # Find bidirectional addresses
        bidirectional = set(sent_to.keys()) & set(received_from.keys())
        related_addresses = list(bidirectional)

        if not related_addresses:
            print(f"[Auto-classify] No related wallets found for {wallet_lower}")
            refresh_tasks[task_key] = {
                "status": "done",
                "detail": "No related wallets found",
                "progress": "0/0"
            }
            save_refresh_status(refresh_tasks)
            return

        print(f"[Auto-classify] Found {len(related_addresses)} related wallets")

        # Load existing classifications
        classified = load_excluded_wallets()

        # Filter out already classified
        to_classify = [addr for addr in related_addresses if addr not in classified]

        if not to_classify:
            print(f"[Auto-classify] All {len(related_addresses)} wallets already classified")
            refresh_tasks[task_key] = {
                "status": "done",
                "detail": f"All {len(related_addresses)} wallets already classified",
                "progress": f"{len(related_addresses)}/{len(related_addresses)}"
            }
            save_refresh_status(refresh_tasks)
            return

        print(f"[Auto-classify] Classifying {len(to_classify)} new wallets (skipping {len(related_addresses) - len(to_classify)} cached)")

        # Classify each wallet
        for i, address in enumerate(to_classify, 1):
            try:
                # Update progress
                refresh_tasks[task_key] = {
                    "status": "classifying",
                    "detail": f"Classifying {address[:10]}... ({i}/{len(to_classify)})",
                    "progress": f"{i}/{len(to_classify)}"
                }
                save_refresh_status(refresh_tasks)

                print(f"[Auto-classify] [{i}/{len(to_classify)}] Classifying {address}")

                # Classify using DeBank (with lock)
                classification = classify_wallet_address(address)

                # Save result
                is_excluded = classification.get("is_excluded", False)
                classified[address] = {
                    "is_excluded": is_excluded,
                    "label": classification.get("label", "unknown"),
                    "name": classification.get("name", ""),
                    "reason": classification.get("reasoning", ""),
                    "source": "debank_auto",
                    "classified_at": datetime.now(timezone.utc).isoformat(),
                }
                save_excluded_wallets(classified)

                print(f"[Auto-classify] [{i}/{len(to_classify)}] {address} → {classification.get('label')} (excluded: {is_excluded})")

            except Exception as e:
                print(f"[Auto-classify] Error classifying {address}: {e}")
                # Continue with next wallet even if one fails
                continue

        # Done
        total = len(related_addresses)
        refresh_tasks[task_key] = {
            "status": "done",
            "detail": f"Classified {len(to_classify)} wallets ({total - len(to_classify)} were cached)",
            "progress": f"{total}/{total}"
        }
        save_refresh_status(refresh_tasks)
        print(f"[Auto-classify] Done! Classified {len(to_classify)} new wallets for {wallet_lower}")

    except Exception as e:
        print(f"[Auto-classify] Failed for {wallet_lower}: {e}")
        refresh_tasks[task_key] = {
            "status": "error",
            "detail": str(e),
            "progress": "0/0"
        }
        save_refresh_status(refresh_tasks)
    finally:
        # Clean up thread reference
        active_threads.pop(task_key, None)


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
    """Classify a wallet address using DeBank. Auto-excludes protocols/contracts."""
    address = address.lower()
    print(f"[Classify] Request for {address}")

    # Check if already classified (cache hit)
    classified = load_excluded_wallets()
    if address in classified:
        print(f"[Classify] Cache hit for {address}: {classified[address]['label']}")
        return {"address": address, "cached": True, **classified[address]}

    print(f"[Classify] Cache miss, calling DeBank for {address}")
    # Classify using DeBank
    classification = classify_wallet_address(address)

    # Save ALL classification results as cache (both excluded and personal)
    is_excluded = classification.get("is_excluded", False)
    classified[address] = {
        "is_excluded": is_excluded,
        "label": classification.get("label", "unknown"),
        "name": classification.get("name", ""),
        "reason": classification.get("reasoning", ""),
        "source": "debank",
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }
    save_excluded_wallets(classified)

    print(f"[Classify] Classified {address} as {classified[address]['label']} (excluded: {is_excluded})")
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


@app.get("/api/classify-status/{wallet}")
def get_classify_status(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Check classification progress for related wallets of a wallet (user must own it)."""
    wallet_lower = wallet.lower()

    # Check ownership
    if not check_wallet_ownership(db, current_user.id, wallet_lower):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    task_key = f"classify_{wallet_lower}"
    user_refresh_tasks = load_refresh_status(current_user.id)
    status = user_refresh_tasks.get(task_key, {"status": "idle", "detail": "", "progress": "0/0"})
    return status


@app.get("/api/active-tasks")
def get_active_tasks(current_user: User = Depends(get_current_user), db: Database = Depends(get_db)):
    """Get active refresh tasks for current user (only for wallets user still owns)."""
    # Load with cleanup to remove statuses for deleted wallets
    user_refresh_tasks = load_refresh_status(current_user.id, cleanup=True, db=db)
    active = {
        wallet: status
        for wallet, status in user_refresh_tasks.items()
        if status.get("status") in ("fetching", "analyzing", "classifying")
    }
    return active


@app.get("/api/related-wallets/{wallet}")
def get_related_wallets(
    wallet: str,
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Find wallets that have bidirectional transfers with this wallet. Data is shared globally (cached)."""
    wallet = wallet.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    raw_txs = load_transactions(wallet)

    # If no transaction data, return 404
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
    print(f"[Related] Wallet {wallet}: found {len(related)} related, {len(classified)} in cache")

    filtered_related = []
    excluded_in_results = []
    cached_count = 0
    for rw in related:
        entry = classified.get(rw["address"])
        if entry and entry.get("is_excluded", False):
            excluded_in_results.append({**rw, "exclusion": entry})
        else:
            # Attach classification if cached (even for non-excluded)
            if entry:
                rw["classification"] = entry
                cached_count += 1
            filtered_related.append(rw)

    print(f"[Related] Returning {len(filtered_related)} related ({cached_count} with classification), {len(excluded_in_results)} excluded")

    return {
        "wallet": wallet,
        "related_count": len(filtered_related),
        "related_wallets": filtered_related,
        "excluded_count": len(excluded_in_results),
        "excluded_wallets": excluded_in_results,
    }


@app.get("/api/related-transactions/{wallet}")
def get_related_transactions(
    wallet: str,
    counterparty: str,
    direction: str = "all",
    current_user: User = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Get transfer transactions between wallet and a specific counterparty. Data is shared globally (cached)."""
    wallet = wallet.lower()
    counterparty = counterparty.lower()

    # Security: only allow viewing if user owns wallet
    if not check_wallet_ownership(db, current_user.id, wallet):
        raise HTTPException(status_code=403, detail="Wallet not found in your list")

    raw_txs = load_transactions(wallet)

    # If no transaction data, return 404
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
