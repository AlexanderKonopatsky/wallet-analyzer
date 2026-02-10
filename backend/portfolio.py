"""
Portfolio replay engine — analyses wallet effectiveness by replaying
all transactions chronologically, maintaining a virtual portfolio
with FIFO cost basis tracking.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from analyze import (
    DATA_DIR,
    DUST_THRESHOLD_USD,
    REPORTS_DIR,
    filter_transactions,
    load_transactions,
)

# ── Constants ─────────────────────────────────────────────────────────────

# Native coin → wrapped mapping per chain (for wrap/unwrap cost basis transfer)
NATIVE_WRAPPED = {
    "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
    "optimism": "0x4200000000000000000000000000000000000006",
    "base": "0x4200000000000000000000000000000000000006",
    "bnb": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "polygon": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",  # WMATIC
    "avalanche": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",  # WAVAX
}


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class CostBasisLot:
    amount: float
    cost_usd: float
    timestamp: int
    source: str  # "swap", "transfer_in", "lp_remove", "lending_withdraw", "bridge_in"
    tx_hash: str


@dataclass
class TokenPosition:
    symbol: str
    address: str
    chain: str
    lots: list[CostBasisLot] = field(default_factory=list)
    realized_pnl_usd: float = 0.0
    total_buy_volume_usd: float = 0.0
    total_sell_volume_usd: float = 0.0
    buy_count: int = 0
    sell_count: int = 0


@dataclass
class TradeRecord:
    token_symbol: str
    chain: str
    cost_basis_usd: float
    proceeds_usd: float
    pnl_usd: float
    roi_pct: float
    hold_seconds: int
    buy_timestamp: int
    sell_timestamp: int
    dex: str
    tx_hash: str


@dataclass
class ProtocolStats:
    name: str
    volume_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    tx_count: int = 0
    first_used: int = 0
    last_used: int = 0


@dataclass
class PortfolioState:
    wallet: str
    positions: dict[str, TokenPosition] = field(default_factory=dict)
    protocols: dict[str, ProtocolStats] = field(default_factory=dict)
    trades: list[TradeRecord] = field(default_factory=list)

    total_deposited_usd: float = 0.0
    total_withdrawn_usd: float = 0.0
    total_swap_volume_usd: float = 0.0
    total_realized_pnl_usd: float = 0.0

    lp_deposited_usd: float = 0.0
    lp_withdrawn_usd: float = 0.0

    lending_supplied_usd: float = 0.0
    lending_withdrawn_usd: float = 0.0
    lending_borrowed_usd: float = 0.0
    lending_repaid_usd: float = 0.0

    first_tx_timestamp: int = 0
    last_tx_timestamp: int = 0
    total_tx_count: int = 0
    active_days: set = field(default_factory=set)

    warnings: list[str] = field(default_factory=list)
    _negative_balance_count: int = 0


# ── Core helpers ──────────────────────────────────────────────────────────

def token_key(chain: str, address: str) -> str:
    return f"{chain}:{address.lower()}" if address else f"{chain}:native"


def get_or_create_position(state: PortfolioState, chain: str, address: str, symbol: str) -> TokenPosition:
    key = token_key(chain, address)
    if key not in state.positions:
        state.positions[key] = TokenPosition(
            symbol=symbol,
            address=address.lower() if address else "",
            chain=chain,
        )
    pos = state.positions[key]
    if not pos.symbol and symbol:
        pos.symbol = symbol
    return pos


def current_holding(pos: TokenPosition) -> float:
    return sum(lot.amount for lot in pos.lots)


def avg_lot_timestamp(lots: list[CostBasisLot], amount: float) -> int:
    """Weighted average timestamp of lots consumed up to `amount`."""
    if not lots:
        return 0
    remaining = amount
    weighted_sum = 0.0
    total = 0.0
    for lot in lots:
        take = min(lot.amount, remaining)
        weighted_sum += lot.timestamp * take
        total += take
        remaining -= take
        if remaining <= 0:
            break
    return int(weighted_sum / total) if total > 0 else 0


def consume_lots_fifo(pos: TokenPosition, amount: float) -> tuple[float, float, int]:
    """
    Remove `amount` tokens via FIFO.
    Returns (cost_basis_usd, amount_consumed, avg_buy_timestamp).
    """
    if amount <= 0:
        return 0.0, 0.0, 0

    avg_ts = avg_lot_timestamp(pos.lots, amount)
    remaining = amount
    total_cost = 0.0

    while remaining > 1e-12 and pos.lots:
        lot = pos.lots[0]
        if lot.amount <= remaining + 1e-12:
            total_cost += lot.cost_usd
            remaining -= lot.amount
            pos.lots.pop(0)
        else:
            fraction = remaining / lot.amount
            consumed_cost = lot.cost_usd * fraction
            total_cost += consumed_cost
            lot.amount -= remaining
            lot.cost_usd -= consumed_cost
            remaining = 0

    consumed = amount - max(remaining, 0)
    return total_cost, consumed, avg_ts


def add_lot(pos: TokenPosition, amount: float, cost_usd: float, timestamp: int, source: str, tx_hash: str):
    if amount <= 0:
        return
    pos.lots.append(CostBasisLot(
        amount=amount,
        cost_usd=cost_usd,
        timestamp=timestamp,
        source=source,
        tx_hash=tx_hash,
    ))


def update_protocol(state: PortfolioState, name: str, volume_usd: float, pnl_usd: float, timestamp: int):
    if not name:
        return
    if name not in state.protocols:
        state.protocols[name] = ProtocolStats(name=name, first_used=timestamp)
    ps = state.protocols[name]
    ps.volume_usd += volume_usd
    ps.realized_pnl_usd += pnl_usd
    ps.tx_count += 1
    if timestamp < ps.first_used or ps.first_used == 0:
        ps.first_used = timestamp
    if timestamp > ps.last_used:
        ps.last_used = timestamp


# ── Transaction processors ───────────────────────────────────────────────

def process_swap(state: PortfolioState, tx: dict):
    chain = tx.get("chain", "")
    timestamp = tx.get("timestamp", 0)
    tx_hash = tx.get("tx_hash", "")
    dex = tx.get("dex", "")

    # token0 is always given away, token1 is received
    t0_addr = tx.get("token0_address", "")
    t0_symbol = tx.get("token0_symbol", "")
    t0_amount = tx.get("token0_amount", 0) or 0
    t0_usd = tx.get("token0_amount_usd", 0) or 0

    t1_addr = tx.get("token1_address", "")
    t1_symbol = tx.get("token1_symbol", "")
    t1_amount = tx.get("token1_amount", 0) or 0
    t1_usd = tx.get("token1_amount_usd", 0) or 0

    # Sell token0 (consume from portfolio)
    pos0 = get_or_create_position(state, chain, t0_addr, t0_symbol)
    cost_basis, consumed, avg_buy_ts = consume_lots_fifo(pos0, t0_amount)
    if consumed < t0_amount - 1e-6:
        state._negative_balance_count += 1

    # Realized P&L on the sold token
    # If cost basis is near-zero but sale is significant, the token was acquired
    # via lending borrow, untracked transfer, LP, etc. — we can't determine real P&L.
    if cost_basis < DUST_THRESHOLD_USD and t0_usd >= DUST_THRESHOLD_USD:
        pnl = 0.0
    else:
        pnl = t0_usd - cost_basis
    pos0.realized_pnl_usd += pnl
    pos0.total_sell_volume_usd += t0_usd
    pos0.sell_count += 1

    # Buy token1 (add to portfolio)
    pos1 = get_or_create_position(state, chain, t1_addr, t1_symbol)
    add_lot(pos1, t1_amount, t1_usd, timestamp, "swap", tx_hash)
    pos1.total_buy_volume_usd += t1_usd
    pos1.buy_count += 1

    # Record trade
    hold_secs = (timestamp - avg_buy_ts) if avg_buy_ts > 0 else 0
    roi = (pnl / cost_basis * 100) if cost_basis > DUST_THRESHOLD_USD else 0.0

    state.trades.append(TradeRecord(
        token_symbol=t0_symbol,
        chain=chain,
        cost_basis_usd=cost_basis,
        proceeds_usd=t0_usd,
        pnl_usd=pnl,
        roi_pct=roi,
        hold_seconds=hold_secs,
        buy_timestamp=avg_buy_ts,
        sell_timestamp=timestamp,
        dex=dex,
        tx_hash=tx_hash,
    ))

    state.total_realized_pnl_usd += pnl
    swap_vol = max(t0_usd, t1_usd)
    state.total_swap_volume_usd += swap_vol
    update_protocol(state, dex, swap_vol, pnl, timestamp)


def process_transfer(state: PortfolioState, tx: dict):
    chain = tx.get("chain", "")
    timestamp = tx.get("timestamp", 0)
    tx_hash = tx.get("tx_hash", "")
    wallet = state.wallet.lower()

    from_addr = (tx.get("from", "") or "").lower()
    to_addr = (tx.get("to", "") or "").lower()
    amount = tx.get("amount", 0) or 0
    amount_usd = tx.get("amount_usd", 0) or 0
    token_addr = tx.get("contract_address", "")
    symbol = tx.get("symbol", "")

    pos = get_or_create_position(state, chain, token_addr, symbol)

    if to_addr == wallet:
        # Incoming transfer
        add_lot(pos, amount, amount_usd, timestamp, "transfer_in", tx_hash)
        pos.total_buy_volume_usd += amount_usd
        pos.buy_count += 1
        state.total_deposited_usd += amount_usd
    elif from_addr == wallet:
        # Outgoing transfer
        cost_basis, consumed, _ = consume_lots_fifo(pos, amount)
        if consumed < amount - 1e-6:
            state._negative_balance_count += 1
        if cost_basis < DUST_THRESHOLD_USD and amount_usd >= DUST_THRESHOLD_USD:
            pnl = 0.0
        else:
            pnl = amount_usd - cost_basis
        pos.realized_pnl_usd += pnl
        pos.total_sell_volume_usd += amount_usd
        pos.sell_count += 1
        state.total_withdrawn_usd += amount_usd
        state.total_realized_pnl_usd += pnl


def process_lending(state: PortfolioState, tx: dict):
    chain = tx.get("chain", "")
    timestamp = tx.get("timestamp", 0)
    tx_hash = tx.get("tx_hash", "")
    action = tx.get("action", "")
    amount = tx.get("amount", 0) or 0
    amount_usd = tx.get("amount_usd", 0) or 0
    token_addr = tx.get("address", "")
    symbol = tx.get("symbol", "")
    platform = tx.get("platform", "") or tx.get("dex", "")

    # Fallback: if amount_usd is 0 but price_usd exists
    if amount_usd == 0:
        price = tx.get("price_usd", 0) or 0
        if price > 0:
            amount_usd = amount * price

    pos = get_or_create_position(state, chain, token_addr, symbol)

    if action == "Supplied":
        consume_lots_fifo(pos, amount)
        state.lending_supplied_usd += amount_usd
    elif action == "Withdrew":
        add_lot(pos, amount, amount_usd, timestamp, "lending_withdraw", tx_hash)
        state.lending_withdrawn_usd += amount_usd
    elif action == "Borrowed":
        add_lot(pos, amount, amount_usd, timestamp, "lending_borrow", tx_hash)
        state.lending_borrowed_usd += amount_usd
    elif action == "Repaid":
        consume_lots_fifo(pos, amount)
        state.lending_repaid_usd += amount_usd

    update_protocol(state, platform, amount_usd, 0, timestamp)


def process_lp(state: PortfolioState, tx: dict):
    chain = tx.get("chain", "")
    timestamp = tx.get("timestamp", 0)
    tx_hash = tx.get("tx_hash", "")
    lp_type = tx.get("type", "")
    dex = tx.get("dex", "")

    t0_addr = tx.get("token0_address", "")
    t0_symbol = tx.get("token0_symbol", "")
    t0_amount = tx.get("token0_amount", 0) or 0
    t0_usd = tx.get("token0_amount_usd", 0) or 0

    t1_addr = tx.get("token1_address", "")
    t1_symbol = tx.get("token1_symbol", "")
    t1_amount = tx.get("token1_amount", 0) or 0
    t1_usd = tx.get("token1_amount_usd", 0) or 0

    total_usd = t0_usd + t1_usd

    pos0 = get_or_create_position(state, chain, t0_addr, t0_symbol)
    pos1 = get_or_create_position(state, chain, t1_addr, t1_symbol)

    if lp_type == "add":
        consume_lots_fifo(pos0, t0_amount)
        consume_lots_fifo(pos1, t1_amount)
        state.lp_deposited_usd += total_usd
    elif lp_type == "remove":
        add_lot(pos0, t0_amount, t0_usd, timestamp, "lp_remove", tx_hash)
        add_lot(pos1, t1_amount, t1_usd, timestamp, "lp_remove", tx_hash)
        state.lp_withdrawn_usd += total_usd

    update_protocol(state, dex, total_usd, 0, timestamp)


def process_bridge(state: PortfolioState, tx: dict):
    chain = tx.get("chain", "")
    timestamp = tx.get("timestamp", 0)
    tx_hash = tx.get("tx_hash", "")
    wallet = state.wallet.lower()

    bridge_type = tx.get("type", "")  # "withdraw" = arriving, "deposit" = leaving
    from_addr = (tx.get("from", "") or "").lower()
    to_addr = (tx.get("to", "") or "").lower()
    amount = tx.get("amount", 0) or 0
    amount_usd = tx.get("amount_usd", 0) or 0
    token_addr = tx.get("token_address", "")
    symbol = tx.get("token_symbol", "")
    platform = tx.get("platform", "")

    pos = get_or_create_position(state, chain, token_addr, symbol)

    # "withdraw" means tokens arriving to the wallet
    if bridge_type == "withdraw" or to_addr == wallet:
        add_lot(pos, amount, amount_usd, timestamp, "bridge_in", tx_hash)
        state.total_deposited_usd += amount_usd
    elif bridge_type == "deposit" or from_addr == wallet:
        consume_lots_fifo(pos, amount)
        state.total_withdrawn_usd += amount_usd

    update_protocol(state, platform, amount_usd, 0, timestamp)


def process_wrap(state: PortfolioState, tx: dict):
    chain = tx.get("chain", "")
    timestamp = tx.get("timestamp", 0)
    tx_hash = tx.get("tx_hash", "")
    action = tx.get("action", "")
    amount = tx.get("amount", 0) or 0
    amount_usd = tx.get("amount_usd", 0) or 0
    contract = tx.get("contract_address", "")

    wrapped_addr = NATIVE_WRAPPED.get(chain, contract)

    if action == "wrapped":
        # ETH → WETH: consume from native, add to wrapped
        native_pos = get_or_create_position(state, chain, "", "ETH")
        cost_basis, consumed, _ = consume_lots_fifo(native_pos, amount)
        # Use cost basis if available, otherwise use current USD value
        lot_cost = cost_basis if consumed > 0 else amount_usd
        wrapped_pos = get_or_create_position(state, chain, wrapped_addr, "WETH")
        add_lot(wrapped_pos, amount, lot_cost, timestamp, "wrap", tx_hash)
    elif action == "unwrapped":
        # WETH → ETH: consume from wrapped, add to native
        wrapped_pos = get_or_create_position(state, chain, wrapped_addr, "WETH")
        cost_basis, consumed, _ = consume_lots_fifo(wrapped_pos, amount)
        lot_cost = cost_basis if consumed > 0 else amount_usd
        native_pos = get_or_create_position(state, chain, "", "ETH")
        add_lot(native_pos, amount, lot_cost, timestamp, "unwrap", tx_hash)


# ── Metrics computation ──────────────────────────────────────────────────

def compute_grade(win_rate: float, total_pnl: float, trade_count: int, avg_pnl: float) -> str:
    """
    Grade considers both win rate and profitability magnitude.
    A wallet with low win rate but large total P&L (cuts losses, lets winners run)
    should still grade well.
    """
    if trade_count < 3:
        return "D" if total_pnl > 0 else "F"

    # Score-based: win rate (0-50 pts) + profitability (0-50 pts)
    wr_score = min(win_rate, 100) / 2  # 0-50

    if total_pnl > 0 and avg_pnl > 0:
        # Log scale for P&L score: $10 avg → 10pts, $100 → 25pts, $1000 → 40pts, $10000 → 50pts
        import math
        pnl_score = min(10 * math.log10(max(avg_pnl, 1) + 1), 50)
    else:
        pnl_score = 0

    score = wr_score + pnl_score

    if score >= 60:
        return "A"
    if score >= 45:
        return "B"
    if score >= 30:
        return "C"
    if score >= 15:
        return "D"
    return "F"


def ts_to_iso(ts: int) -> str:
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def ts_to_date(ts: int) -> str:
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def compute_metrics(state: PortfolioState) -> dict:
    # Filter meaningful trades (exclude dust)
    meaningful_trades = [t for t in state.trades if t.cost_basis_usd >= DUST_THRESHOLD_USD or t.proceeds_usd >= DUST_THRESHOLD_USD]

    winning = [t for t in meaningful_trades if t.pnl_usd > 0]
    losing = [t for t in meaningful_trades if t.pnl_usd <= 0]
    total_trades = len(meaningful_trades)
    win_rate = (len(winning) / total_trades * 100) if total_trades > 0 else 0

    avg_pnl = (sum(t.pnl_usd for t in meaningful_trades) / total_trades) if total_trades > 0 else 0
    avg_roi = (sum(t.roi_pct for t in meaningful_trades) / total_trades) if total_trades > 0 else 0

    grade = compute_grade(win_rate, state.total_realized_pnl_usd, total_trades, avg_pnl)

    # Group trades by token and protocol for drill-down
    from collections import defaultdict
    trades_by_token = defaultdict(list)  # (chain, symbol) → [TradeRecord]
    trades_by_protocol = defaultdict(list)  # dex_name → [TradeRecord]
    for t in meaningful_trades:
        trades_by_token[(t.chain, t.token_symbol)].append(t)
        if t.dex:
            trades_by_protocol[t.dex].append(t)

    # Token stats
    token_stats = []
    for key, pos in state.positions.items():
        if pos.total_buy_volume_usd < DUST_THRESHOLD_USD and pos.total_sell_volume_usd < DUST_THRESHOLD_USD:
            continue
        holding = current_holding(pos)
        roi = (pos.realized_pnl_usd / pos.total_buy_volume_usd * 100) if pos.total_buy_volume_usd > 0 else 0
        avg_buy = (pos.total_buy_volume_usd / pos.buy_count) if pos.buy_count > 0 else 0
        avg_sell = (pos.total_sell_volume_usd / pos.sell_count) if pos.sell_count > 0 else 0
        token_trades = trades_by_token.get((pos.chain, pos.symbol), [])
        token_trades_sorted = sorted(token_trades, key=lambda t: t.sell_timestamp, reverse=True)
        token_stats.append({
            "symbol": pos.symbol,
            "address": pos.address,
            "chain": pos.chain,
            "total_bought_usd": round(pos.total_buy_volume_usd, 2),
            "total_sold_usd": round(pos.total_sell_volume_usd, 2),
            "realized_pnl_usd": round(pos.realized_pnl_usd, 2),
            "roi_pct": round(roi, 2),
            "buy_count": pos.buy_count,
            "sell_count": pos.sell_count,
            "current_holding": holding,
            "avg_buy_size_usd": round(avg_buy, 2),
            "avg_sell_size_usd": round(avg_sell, 2),
            "trades": [
                {
                    "cost_basis_usd": round(t.cost_basis_usd, 2),
                    "proceeds_usd": round(t.proceeds_usd, 2),
                    "pnl_usd": round(t.pnl_usd, 2),
                    "roi_pct": round(t.roi_pct, 2),
                    "hold_seconds": t.hold_seconds,
                    "buy_date": ts_to_iso(t.buy_timestamp),
                    "sell_date": ts_to_iso(t.sell_timestamp),
                    "dex": t.dex,
                }
                for t in token_trades_sorted
            ],
        })
    token_stats.sort(key=lambda x: abs(x["realized_pnl_usd"]), reverse=True)

    # Protocol stats
    protocol_stats = []
    for name, ps in state.protocols.items():
        proto_trades = trades_by_protocol.get(ps.name, [])
        proto_trades_sorted = sorted(proto_trades, key=lambda t: t.sell_timestamp, reverse=True)
        protocol_stats.append({
            "name": ps.name,
            "volume_usd": round(ps.volume_usd, 2),
            "realized_pnl_usd": round(ps.realized_pnl_usd, 2),
            "tx_count": ps.tx_count,
            "first_used": ts_to_iso(ps.first_used),
            "last_used": ts_to_iso(ps.last_used),
            "trades": [
                {
                    "token": t.token_symbol,
                    "chain": t.chain,
                    "cost_basis_usd": round(t.cost_basis_usd, 2),
                    "proceeds_usd": round(t.proceeds_usd, 2),
                    "pnl_usd": round(t.pnl_usd, 2),
                    "roi_pct": round(t.roi_pct, 2),
                    "hold_seconds": t.hold_seconds,
                    "sell_date": ts_to_iso(t.sell_timestamp),
                }
                for t in proto_trades_sorted
            ],
        })
    protocol_stats.sort(key=lambda x: x["volume_usd"], reverse=True)

    # Top trades
    sorted_by_pnl = sorted(meaningful_trades, key=lambda t: t.pnl_usd, reverse=True)
    best_5 = sorted_by_pnl[:5]
    worst_5 = sorted_by_pnl[-5:][::-1] if len(sorted_by_pnl) >= 5 else sorted_by_pnl[::-1]

    def trade_to_dict(t: TradeRecord) -> dict:
        return {
            "token": t.token_symbol,
            "chain": t.chain,
            "cost_basis_usd": round(t.cost_basis_usd, 2),
            "proceeds_usd": round(t.proceeds_usd, 2),
            "pnl_usd": round(t.pnl_usd, 2),
            "roi_pct": round(t.roi_pct, 2),
            "hold_seconds": t.hold_seconds,
            "buy_date": ts_to_iso(t.buy_timestamp),
            "sell_date": ts_to_iso(t.sell_timestamp),
            "dex": t.dex,
        }

    # Warnings
    warnings = list(state.warnings)
    if state._negative_balance_count > 0:
        warnings.append(
            f"{state._negative_balance_count} sell events exceeded known holdings "
            "(possible untracked acquisitions)"
        )

    avg_position = (state.total_swap_volume_usd / total_trades) if total_trades > 0 else 0

    return {
        "wallet": state.wallet,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "tx_count_analyzed": state.total_tx_count,
        "summary": {
            "grade": grade,
            "total_realized_pnl_usd": round(state.total_realized_pnl_usd, 2),
            "win_rate_pct": round(win_rate, 1),
            "total_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "avg_trade_pnl_usd": round(avg_pnl, 2),
            "avg_trade_roi_pct": round(avg_roi, 2),
            "total_volume_usd": round(state.total_swap_volume_usd, 2),
            "net_flow_usd": round(state.total_deposited_usd - state.total_withdrawn_usd, 2),
            "total_deposited_usd": round(state.total_deposited_usd, 2),
            "total_withdrawn_usd": round(state.total_withdrawn_usd, 2),
            "active_days": len(state.active_days),
            "first_activity": ts_to_iso(state.first_tx_timestamp),
            "last_activity": ts_to_iso(state.last_tx_timestamp),
            "avg_txs_per_day": round(state.total_tx_count / max(len(state.active_days), 1), 1),
            "avg_position_size_usd": round(avg_position, 2),
        },
        "defi": {
            "lp_deposited_usd": round(state.lp_deposited_usd, 2),
            "lp_withdrawn_usd": round(state.lp_withdrawn_usd, 2),
            "lp_net_pnl_usd": round(state.lp_withdrawn_usd - state.lp_deposited_usd, 2),
            "lending_supplied_usd": round(state.lending_supplied_usd, 2),
            "lending_withdrawn_usd": round(state.lending_withdrawn_usd, 2),
            "lending_borrowed_usd": round(state.lending_borrowed_usd, 2),
            "lending_repaid_usd": round(state.lending_repaid_usd, 2),
            "lending_net_pnl_usd": round(
                (state.lending_withdrawn_usd - state.lending_supplied_usd), 2
            ),
        },
        "tokens": token_stats[:50],  # top 50
        "protocols": protocol_stats,
        "top_trades": {
            "best": [trade_to_dict(t) for t in best_5],
            "worst": [trade_to_dict(t) for t in worst_5],
        },
        "warnings": warnings,
    }


# ── Main pipeline ────────────────────────────────────────────────────────

TX_PROCESSORS = {
    "swap": process_swap,
    "transfer": process_transfer,
    "lending": process_lending,
    "lp": process_lp,
    "bridge": process_bridge,
    "wrap": process_wrap,
}


def analyze_portfolio(wallet: str) -> dict:
    wallet = wallet.lower()
    txs = load_transactions(wallet)
    if not txs:
        return {"wallet": wallet, "error": "No transaction data found"}

    txs = filter_transactions(txs)
    txs.sort(key=lambda t: t.get("timestamp", 0))

    state = PortfolioState(wallet=wallet)

    for tx in txs:
        tx_type = tx.get("tx_type", "")
        timestamp = tx.get("timestamp", 0)

        processor = TX_PROCESSORS.get(tx_type)
        if not processor:
            continue

        processor(state, tx)

        state.total_tx_count += 1
        if state.first_tx_timestamp == 0 or timestamp < state.first_tx_timestamp:
            state.first_tx_timestamp = timestamp
        if timestamp > state.last_tx_timestamp:
            state.last_tx_timestamp = timestamp
        day = ts_to_date(timestamp)
        if day:
            state.active_days.add(day)

    result = compute_metrics(state)
    save_portfolio_cache(wallet, result)
    return result


# ── Caching ───────────────────────────────────────────────────────────────

def cache_path(wallet: str) -> Path:
    return REPORTS_DIR / f"{wallet.lower()}_portfolio.json"


def save_portfolio_cache(wallet: str, data: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_path(wallet), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cached_portfolio(wallet: str) -> dict | None:
    path = cache_path(wallet)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_cache_valid(wallet: str) -> bool:
    """Check if cache is newer than the data file."""
    c = cache_path(wallet)
    d = DATA_DIR / f"{wallet.lower()}.json"
    if not c.exists() or not d.exists():
        return False
    return c.stat().st_mtime > d.stat().st_mtime


# ── CLI entrypoint ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python portfolio.py <wallet_address>")
        sys.exit(1)
    result = analyze_portfolio(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
