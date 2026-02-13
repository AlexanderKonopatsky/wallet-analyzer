import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"


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


def _build_ownership_checker(db, user_id: int) -> Callable[[str], bool] | None:
    if db is None or not hasattr(db, "get_user_by_id"):
        return None
    user = db.get_user_by_id(user_id)
    if not user:
        return lambda _wallet: False
    owned = {wallet.lower() for wallet in getattr(user, "wallet_addresses", [])}
    return lambda wallet: wallet.lower() in owned


def load_refresh_status(
    user_id: int,
    cleanup: bool = False,
    db=None,
    ownership_checker: Callable[[str], bool] | None = None,
) -> dict:
    """Load refresh task statuses from user's file.

    Args:
        user_id: User ID
        cleanup: If True, remove statuses for wallets user no longer owns
        db: Optional DB instance with get_user_by_id(user_id)
        ownership_checker: Optional callable(wallet) -> bool
    """
    user_dir = get_user_data_dir(user_id)
    status_file = user_dir / "refresh_status.json"
    if status_file.exists():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                statuses = json.load(f)

            # Cleanup orphaned statuses if requested
            if cleanup:
                checker = ownership_checker or _build_ownership_checker(db, user_id)
                if checker:
                    cleaned = {}
                    for wallet, status in statuses.items():
                        if checker(wallet):
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
        if isinstance(status, dict)
        and isinstance(wallet, str)
        and wallet.lower().startswith("0x")
        and len(wallet) == 42
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


def revoke_analysis_consent(user_id: int, wallet: str) -> None:
    """Revoke previously granted paid-analysis consent for wallet."""
    wallet_lower = wallet.lower()
    consents = load_analysis_consents(user_id)
    if wallet_lower not in consents:
        return
    consents.remove(wallet_lower)
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
        return updated or {
            **payment,
            "balanceCredited": True,
            "balanceCreditedAt": credited_at,
            "balanceCreditedAmount": amount,
        }

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
    return updated or {
        **payment,
        "balanceCredited": True,
        "balanceCreditedAt": timestamp,
        "balanceCreditedAmount": credit_amount,
    }
