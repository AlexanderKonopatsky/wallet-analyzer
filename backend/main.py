import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

API_URL = "https://feed-api.cielo.finance/api/v1/feed"
DATA_DIR = Path(__file__).parent.parent / "data"


def _load_api_keys() -> list[str]:
    """Load all CIELO_API_KEY variants from environment.

    Reads CIELO_API_KEY, CIELO_API_KEY_1, CIELO_API_KEY_2, ... up to _99.
    Returns list of valid (non-empty) keys.
    """
    keys = []
    base = os.getenv("CIELO_API_KEY", "")
    if base:
        keys.append(base)
    for i in range(1, 100):
        k = os.getenv(f"CIELO_API_KEY_{i}", "")
        if k:
            keys.append(k)
        elif i > 10:
            # Stop scanning after a gap beyond index 10
            break
    return keys


API_KEYS = _load_api_keys()
_current_key_index = 0


def _get_current_key() -> str:
    """Return the current API key."""
    global _current_key_index
    if not API_KEYS:
        return ""
    return API_KEYS[_current_key_index % len(API_KEYS)]


def _rotate_key() -> bool:
    """Switch to the next API key. Returns False if all keys exhausted."""
    global _current_key_index
    if len(API_KEYS) <= 1:
        return False
    old_index = _current_key_index
    _current_key_index = (_current_key_index + 1) % len(API_KEYS)
    # Full circle — all keys exhausted
    if _current_key_index == 0:
        _current_key_index = old_index
        return False
    print(f"  → API key limit reached, switching to key #{_current_key_index + 1}/{len(API_KEYS)}")
    return True


def _api_request(params: dict) -> requests.Response:
    """Make an API request with automatic key rotation on 429."""
    while True:
        response = requests.get(
            API_URL,
            headers={"X-API-KEY": _get_current_key()},
            params=params,
            timeout=15,
        )
        if response.status_code == 429:
            if _rotate_key():
                continue
        return response


def fetch_all_transactions(wallet: str, existing_txs: dict) -> list:
    """Fetches all wallet transactions with pagination (100 per request).
    Saves data after each page for protection against interruption."""
    start_from = None
    page = 1
    new_count = 0

    while True:
        # Rate limiting: 10 credits/sec, 3 credits per request = max ~3 req/sec
        # Add delay after first request to stay within limits
        if page > 1:
            time.sleep(0.4)  # ~2.5 requests/sec to be safe

        print(f"Requesting page {page}...")
        params = {"wallet": wallet, "limit": 100}
        if start_from:
            params["startFrom"] = start_from

        response = _api_request(params)
        response.raise_for_status()
        result = response.json()

        status = result.get("status")
        if status == "pending":
            print("Data is loading (first request for this wallet). Try again in a few seconds.")
            return []
        elif status != "ok":
            print(f"API Error: {result.get('message', 'unknown error')}")
            return []

        items = result.get("data", {}).get("items", [])
        print(f"  → Received {len(items)} items from API")
        print(f"  → Current existing_txs count: {len(existing_txs)}")

        # Add new transactions
        page_new_count = 0
        for tx in items:
            if tx["tx_hash"] not in existing_txs:
                existing_txs[tx["tx_hash"]] = tx
                page_new_count += 1

        new_count += page_new_count

        # Save after each page
        if page_new_count > 0:
            all_transactions = list(existing_txs.values())
            all_transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            save_data(wallet, all_transactions)
            print(f"  → Saved {page_new_count} new transactions (total new: {new_count})")
        else:
            print(f"  → No new transactions on this page (all duplicates)")

        # If no transactions on page - this is the end of data
        if not items:
            break

        paging = result.get("data", {}).get("paging", {})
        has_next = paging.get("has_next_page", False)

        if has_next:
            start_from = paging.get("next_object_id") or paging.get("next_cursor")
            page += 1
        else:
            break

    return list(existing_txs.values())


def load_existing_data(wallet: str) -> dict:
    """Loads existing data from JSON file."""
    filepath = DATA_DIR / f"{wallet.lower()}.json"
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"wallet": wallet, "last_updated": None, "transactions": []}


def save_data(wallet: str, transactions: list) -> None:
    """Saves data to JSON file."""
    DATA_DIR.mkdir(exist_ok=True)
    filepath = DATA_DIR / f"{wallet.lower()}.json"

    data = {
        "wallet": wallet,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "transactions": transactions,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def format_timestamp(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_amount(amount: float, decimals: int = 2) -> str:
    if amount >= 1000000:
        return f"{amount/1000000:.2f}M"
    elif amount >= 1000:
        return f"{amount/1000:.2f}K"
    else:
        return f"{amount:.{decimals}f}"


def format_transaction_details(tx: dict) -> str:
    tx_type = tx.get("tx_type", "?")

    if tx_type == "swap":
        token0_sym = tx.get("token0_symbol", "?")
        token0_amt = tx.get("token0_amount", 0)
        token0_usd = tx.get("token0_amount_usd", 0)
        token1_sym = tx.get("token1_symbol", "?")
        token1_amt = tx.get("token1_amount", 0)
        token1_usd = tx.get("token1_amount_usd", 0)
        dex = tx.get("dex", "?")

        return (
            f"Swapped {format_amount(token0_amt, 4)} {token0_sym} "
            f"(${format_amount(token0_usd)}) for {format_amount(token1_amt, 4)} {token1_sym} "
            f"(${format_amount(token1_usd)}) on {dex}"
        )

    elif tx_type == "lending":
        action = tx.get("action", "?")
        symbol = tx.get("symbol", "?")
        amount = tx.get("amount", 0)
        amount_usd = tx.get("amount_usd", 0)
        platform = tx.get("platform", "?")

        return (
            f"{action}: {format_amount(amount, 4)} {symbol} "
            f"(${format_amount(amount_usd)}) on {platform}"
        )

    elif tx_type == "transfer":
        symbol = tx.get("token_symbol", tx.get("symbol", "?"))
        amount = tx.get("token_amount", tx.get("amount", 0))
        amount_usd = tx.get("token_amount_usd", tx.get("amount_usd", 0))
        to_addr = tx.get("to", "")

        to_short = f"{to_addr[:6]}...{to_addr[-4:]}" if len(to_addr) > 10 else to_addr
        return (
            f"Transferred: {format_amount(amount, 4)} {symbol} "
            f"(${format_amount(amount_usd)}) to {to_short}"
        )

    else:
        return f"Type: {tx_type}"


def display_transaction(i: int, tx: dict) -> None:
    """Displays information about a single transaction."""
    tx_type = tx.get('tx_type', '?')
    chain = tx.get('chain', '?')
    timestamp = format_timestamp(tx.get('timestamp', 0))
    tx_hash = tx.get('tx_hash', '?')

    print(f"  [{i}] {tx_type:20s} | {chain:12s} | {timestamp}")
    print(f"      {format_transaction_details(tx)}")

    extras = []
    if tx.get('first_interaction'):
        extras.append("First interaction")
    if tx.get('from_label'):
        extras.append(f"From: {tx.get('from_label')}")

    if extras:
        print(f"      {' | '.join(extras)}")

    print(f"      tx: {tx_hash}")


def main() -> None:
    if not API_KEYS:
        print("Error: specify CIELO_API_KEY in .env file")
        return
    print(f"Loaded {len(API_KEYS)} API key(s)")

    wallet = input("Enter wallet address: ").strip()
    if not wallet:
        print("Address cannot be empty.")
        return

    # Load existing data
    existing_data = load_existing_data(wallet)
    existing_txs = {tx["tx_hash"]: tx for tx in existing_data["transactions"]}
    initial_hashes = set(existing_txs.keys())
    initial_count = len(existing_txs)

    print(f"\nLoading transactions for {wallet}...")
    if existing_txs:
        print(f"Found {len(existing_txs)} saved transactions")

    try:
        all_transactions = fetch_all_transactions(wallet, existing_txs)
    except requests.RequestException as e:
        print(f"Request error: {e}")
        return

    if not all_transactions:
        print("No transactions found or data is still loading.")
        return

    # Count new transactions
    new_count = len(all_transactions) - initial_count

    if new_count > 0:
        print(f"\n✓ Loading completed!")
        print(f"Found {new_count} new transactions")
        print(f"Total transactions: {len(all_transactions)}")
        print(f"Data saved to {DATA_DIR / f'{wallet.lower()}.json'}")

        # Show last 10 new transactions
        new_txs = [tx for tx in all_transactions if tx["tx_hash"] not in initial_hashes]
        new_txs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        if new_txs:
            print(f"\nLast {min(10, len(new_txs))} new transactions:\n")
            for i, tx in enumerate(new_txs[:10], 1):
                display_transaction(i, tx)
    else:
        print("\n✓ No new transactions. All data is up to date.")
        print(f"Total transactions: {len(all_transactions)}")


if __name__ == "__main__":
    main()
