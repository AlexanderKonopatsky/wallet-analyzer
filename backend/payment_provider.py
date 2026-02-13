import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from fastapi import HTTPException

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
