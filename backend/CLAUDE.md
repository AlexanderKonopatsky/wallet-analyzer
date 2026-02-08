# Backend (Python FastAPI)

Backend logic for fetching transactions, AI analysis, and REST API.

## Architecture

```
main.py          # Cielo Finance API client (fetch transactions)
analyze.py       # AI analysis engine (Gemini via OpenRouter)
categories.py    # Wallet classification (LLM-based)
portfolio.py     # Portfolio statistics aggregation
server.py        # FastAPI REST API + background tasks
```

## Module Responsibilities

### main.py — Data Fetching
**Purpose**: Fetch transactions from Cielo Finance API with key rotation and pagination.

**Key Functions**:
- `fetch_transactions(wallet, max_pages=None)` — main function
  - Pagination: `limit=1000`, automatic cursor
  - Key rotation on 429 (rate limit)
  - Saves to `data/{wallet}.json`
  - Transaction deduplication by `tx_hash`
  - Returns: `(new_count, total_count)`

**API Key Rotation**:
- Loads `CIELO_API_KEY`, `CIELO_API_KEY_1..99` from `.env`
- On 429 automatically switches to next key
- Logs rotation: `"Rate limit, rotating to key 3/10"`

**Error Handling**:
- 429 → rotate key, retry
- 401/403 → fatal error, stop
- Network errors → retry with exponential backoff (up to 3 attempts)

**Data Format**: see `data/CLAUDE.md`

### analyze.py — AI Analysis
**Purpose**: Incremental transaction analysis via LLM (Google Gemini).

**Key Functions**:
- `analyze_wallet(wallet_address, force_full=False)` — main function
  - Loads transactions from `data/{wallet}.json`
  - Reads `reports/{wallet}_state.json` (if exists)
  - Filters new transactions (by `processed_tx_keys`)
  - Groups by days → chunks of 30 transactions
  - Sends to LLM with context
  - Updates state, generates `.md`

**Chunking Strategy**:
```python
# Group by day
days = group_by_day(transactions)
# Split into chunks (max 30 txs per chunk)
chunks = split_into_chunks(days, max_size=30)
```

**LLM Context Management**:
- **Full context**: all previous "Day summary" (for last chunk)
- **Limited context**: last N "Day summary" (FULL_CHRONOLOGY_COUNT, default=1)
- This balances context and token cost

**System Prompt**: Described in `analyze.py` (SYSTEM_PROMPT)
- Format requirements (day headers, "Day summary")
- Operation description rules (human-readable)
- Previous activity context awareness

**OpenRouter API**:
```python
response = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
    json={
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context + chunk_text}
        ]
    }
)
```

**State Management**: see `reports/CLAUDE.md`

### categories.py — Wallet Classification
**Purpose**: Automatic wallet classification (CEX, bridge, protocol, user).

**Key Functions**:
- `classify_wallet(address, sample_txs)` — classify address
  - Takes sample transactions for analysis
  - Sends to LLM with classification prompt
  - Returns: `{"category": "cex_deposit", "confidence": 0.95, "reasoning": "..."}`

**Categories**:
- `cex_deposit` — exchange deposit address (Binance, Coinbase, etc.)
- `bridge` — cross-chain bridge (LayerZero, Stargate, etc.)
- `defi_protocol` — protocol contract (Uniswap router, Aave pool, etc.)
- `user_wallet` — regular user wallet
- `unknown` — unable to classify

**Confidence Threshold**:
- `>= 0.8` → auto-exclude (if category = cex_deposit / bridge / defi_protocol)
- `< 0.8` → show to user, don't auto-exclude

**LLM Prompt Example**:
```
Classify the wallet based on these transactions:
[transaction list]

Return JSON: {"category": "...", "confidence": 0.0-1.0, "reasoning": "..."}
Categories: cex_deposit, bridge, defi_protocol, user_wallet, unknown
```

### portfolio.py — Portfolio Aggregation
**Purpose**: Aggregate statistics by tokens, protocols, chains.

**Key Functions**:
- `generate_portfolio(wallet_address)` — generate statistics
  - Reads `data/{wallet}.json`
  - Aggregates by tokens, protocols, dates
  - Saves to `reports/{wallet}_portfolio.json`

**Output**: see `reports/CLAUDE.md` (Portfolio Files)

### server.py — FastAPI Server
**Purpose**: REST API + background refresh tasks.

**Framework**: FastAPI (async)
**Port**: 8000
**CORS**: localhost:5173, localhost:5174

**Endpoints**:

#### Settings
- `GET /api/settings` → `{"auto_classify_batch_size": 3, ...}`

#### Wallets
- `GET /api/wallets` → list of wallets + metadata
  - Reads `data/*.json`, `wallet_tags.json`, `refresh_status.json`
  - Returns: `[{address, tag, tx_count, last_updated, refresh_status}, ...]`

- `GET /api/tags` → `{"0x...": "Main", ...}`
- `PUT /api/tags/{wallet}` + body `{"tag": "New Name"}` → update tag

#### Reports
- `GET /api/report/{wallet}` → `{"report": "# Markdown...", "related_wallets": [...]}`
  - Reads `reports/{wallet}.md`
  - Computes related wallets (top addresses by volume)
  - Adds classification from `categories.json`

- `GET /api/related-transactions/{wallet}?counterparty={addr}&direction={sent|received}`
  - Returns transactions between wallet and counterparty
  - Used for "Show Txs" in related wallet cards

#### Background Tasks
- `POST /api/refresh/{wallet}` → `{"status": "started", "task_id": "..."}`
  - Starts background task: `fetch_transactions()` → `analyze_wallet()`
  - Updates `refresh_status.json` as it progresses
  - Task runs in separate thread (non-daemon)

- `GET /api/refresh-status/{wallet}` → `{"status": "processing", "stage": "analyzing", ...}`
  - Reads from `refresh_status.json`

- `GET /api/active-tasks` → `[{wallet, status, stage, started_at}, ...]`
  - All active refresh tasks

#### Classification
- `POST /api/classify-wallet/{address}` → `{"category": "...", "confidence": 0.95, ...}`
  - Calls `categories.classify_wallet()`
  - If confidence >= 0.8 and category != user_wallet → auto-exclude
  - Saves to `categories.json` and `excluded_wallets.json`

#### Exclusions
- `GET /api/excluded-wallets` → `[{address, category, confidence, reasoning, source}, ...]`
- `POST /api/excluded-wallets` + body `{"address": "0x..."}` → add (manual)
- `DELETE /api/excluded-wallets/{address}` → remove from exclusions

**Background Task Implementation**:
```python
import threading

def refresh_task(wallet):
    # Update status: fetching
    new_count, total = fetch_transactions(wallet)
    # Update status: analyzing
    analyze_wallet(wallet)
    # Update status: completed

@app.post("/api/refresh/{wallet}")
async def refresh_wallet(wallet):
    thread = threading.Thread(target=refresh_task, args=(wallet,), daemon=False)
    thread.start()
    return {"status": "started"}
```

**Important**: `daemon=False` → thread is not interrupted when browser closes!

## Environment Variables

**Required**:
- `CIELO_API_KEY` — primary Cielo Finance API key
- `OPENROUTER_API_KEY` — OpenRouter API key

**Optional**:
- `CIELO_API_KEY_1..99` — additional keys for rotation
- `FULL_CHRONOLOGY_COUNT` — how many recent analyses to use for context (default: 1)
- `AUTO_CLASSIFY_BATCH_SIZE` — parallel classification of related wallets (default: 3)

## Error Handling Best Practices

1. **API Errors**:
   - Log all errors with context (wallet, endpoint, status code)
   - Use `HTTPException` for FastAPI
   - Return user-friendly messages

2. **LLM Errors**:
   - Retry with exponential backoff (rate limits)
   - Save partial results (state files)
   - Log prompts and responses for debugging

3. **Data Integrity**:
   - Always check file existence before reading
   - Use transactional operations (write to temp → rename)
   - Backup state files before updating

## Performance Considerations

- **Pagination**: Don't load all transactions at once (use `max_pages`)
- **Chunking**: Limit LLM prompt size (30 txs = ~2000 tokens)
- **Caching**: Don't recalculate same data (state files)
- **Async**: FastAPI endpoints should be async (where possible)
- **Threading**: Background tasks in separate threads (don't block API)

## Logging

Use `print()` or `logging` for debugging:
```python
print(f"[{wallet[:8]}] Fetching transactions: page {page_num}")
print(f"[{wallet[:8]}] Analyzing chunk {i+1}/{len(chunks)}")
print(f"[{wallet[:8]}] Classification: {category} ({confidence:.0%})")
```

Format: `[0xdf4e06] Message` (first 8 characters of address)
