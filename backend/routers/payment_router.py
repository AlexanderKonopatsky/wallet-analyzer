from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from db import User
from payment_provider import (
    PAYMENT_RECEIVE_TOKEN,
    PAYMENT_STATUS_DESCRIPTIONS,
    find_token,
    from_base_units,
    get_cached_oneclick_tokens,
    get_chain_type,
    is_valid_address,
    oneclick_execution_status,
    oneclick_get_quote,
    parse_token_id,
    payment_config,
    to_base_units,
)
from user_data_store import (
    apply_payment_credit_if_needed,
    create_user_payment,
    get_user_payment,
    load_user_payments,
    update_user_payment,
)

router = APIRouter()


class PaymentQuoteRequest(BaseModel):
    """Request body for /api/quote."""

    amount: str
    originToken: str
    refundAddress: str


class PaymentCreateRequest(BaseModel):
    """Request body for /api/payment/create."""

    amount: str
    originToken: str
    refundAddress: str
    originAmount: str


def _serialize_payment_status(payment: dict, api_error: str | None = None) -> dict:
    response = {
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
    if api_error:
        response["apiError"] = api_error
    return response


@router.get("/api/tokens")
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


@router.post("/api/quote")
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


@router.post("/api/payment/create")
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


@router.get("/api/payment/{payment_id}/status")
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
        return _serialize_payment_status(payment)

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
        return _serialize_payment_status(payment, api_error=str(api_error.detail))

    return _serialize_payment_status(payment)


@router.get("/api/payments")
def list_user_payments(current_user: User = Depends(get_current_user)):
    """List payment history for current user."""
    payments = load_user_payments(current_user.id)
    payments_sorted = sorted(payments, key=lambda payment: payment.get("createdAt", ""), reverse=True)
    return {"payments": payments_sorted}
