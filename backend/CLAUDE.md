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

### main.py вЂ” Data Fetching
**Purpose**: Fetch transactions from Cielo Finance API with key rotation and pagination.

**Key Functions**:
- `fetch_transactions(wallet, max_pages=None)` вЂ” main function
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
- 429 в†’ rotate key, retry
- 401/403 в†’ fatal error, stop
- Network errors в†’ retry with exponential backoff (up to 3 attempts)

**Data Format**: see `data/CLAUDE.md`

### analyze.py вЂ” AI Analysis
**Purpose**: Incremental transaction analysis via LLM (Google Gemini).

**Key Functions**:
- `analyze_wallet(wallet_address, force_full=False)` вЂ” main function
  - Loads transactions from `data/{wallet}.json`
  - Reads `reports/{wallet}_state.json` (if exists)
  - Filters new transactions (by `processed_tx_keys`)
  - Groups by days в†’ chunks with configurable max size (`CHUNK_MAX_TRANSACTIONS`)
  - Sends to LLM with context
  - Updates state, generates `.md`

**Chunking Strategy**:
```python
# Group by day
days = group_by_day(transactions)
# Split into chunks (max txs per chunk from CHUNK_MAX_TRANSACTIONS)
chunks = split_into_chunks(days, max_size=CHUNK_MAX_TRANSACTIONS)
```

**LLM Context Management**:
- **Full context**: all previous "Day summary" (for last chunk)
- **Limited context**: last N "Day summary" (FULL_CHRONOLOGY_COUNT, default=1)
- **Hierarchical compression** (enabled by default): 3-tier chunk-based grouping
  - Tier 1 (newest): individual summaries
  - Tier 2 (middle): groups of 5 в†’ LLM compression
  - Tier 3 (oldest): groups в†’ super-groups of 3 в†’ double compression
  - Only complete groups compressed (partial groups remain as individual lines)
  - Content-hash caching in `compression_cache` state field
  - Configurable via `.env`: `CONTEXT_COMPRESSION_ENABLED`, `CONTEXT_DAILY_COUNT`, `CONTEXT_WEEKLY_COUNT`, `CONTEXT_TIER2_GROUP_SIZE`, `CONTEXT_TIER3_SUPER_SIZE`
- In tx-window mode, additional compression calls are disabled by default; enable with `CONTEXT_COMPRESSION_WITH_WINDOW_ENABLED=true` if needed
- **Optional tx-window mode** (disabled by default): old context keeps only day summaries covering last N txs + high-importance day anchors
  - Configurable via `.env`: `CONTEXT_OPTIMIZED_WINDOW_ENABLED`, `CONTEXT_WINDOW_TX_COUNT`, `CONTEXT_IMPORTANCE_ANCHORS`, `CONTEXT_IMPORTANCE_MIN`, `CONTEXT_TX_FALLBACK_PER_DAY`
- This balances context and token cost (plateaus at ~4K tokens for large wallets)

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

### categories.py вЂ” Wallet Categories Management
**Purpose**: User-created category management for organizing wallets.

**Key Functions**:
- `get_all_categories()` вЂ” list all user-created categories
- `create_category(name, color)` вЂ” create new category
- `set_wallet_category(address, category_id)` вЂ” assign wallet to category
- `get_wallet_category(address)` вЂ” get wallet's category

See `data/CLAUDE.md` for categories.json format.

**Purpose**: Automatic wallet classification (protocol/contract vs personal wallet) using DeBank data.

**Key Functions**:
  - Returns: `{"protocol": "Clipper", "balance": "$42,839", "success": True}`
  - If `protocol` is found в†’ contract/protocol (excluded)
  - If `protocol` is None в†’ personal wallet (not excluded)

**Classification Logic** (in server.py):
- `classify_wallet_address(address)` вЂ” classify address using DeBank
  - If protocol found в†’ `{"is_excluded": True, "label": "contract", "name": "Protocol Name"}`
  - If no protocol в†’ `{"is_excluded": False, "label": "personal"}`
  - Results cached in `data/excluded_wallets.json`

**Labels**:
- `contract` вЂ” protocol/contract (auto-excluded)
- `personal` вЂ” personal wallet (not excluded)
- `unknown` вЂ” classification failed


### portfolio.py вЂ” Portfolio Aggregation
**Purpose**: Aggregate statistics by tokens, protocols, chains.

**Key Functions**:
- `generate_portfolio(wallet_address)` вЂ” generate statistics
  - Reads `data/{wallet}.json`
  - Aggregates by tokens, protocols, dates
  - Saves to `reports/{wallet}_portfolio.json`

**Output**: see `reports/CLAUDE.md` (Portfolio Files)

### server.py вЂ” FastAPI Server
**Purpose**: REST API + background refresh tasks.

**Framework**: FastAPI (async)
**Port**: 8000
**CORS**: localhost:5173, localhost:5174

**Endpoints**:

#### Settings

#### Wallets
- `GET /api/wallets` в†’ list of wallets + metadata
  - Reads `data/*.json`, `wallet_tags.json`, `refresh_status.json`
  - Returns: `[{address, tag, tx_count, last_updated, refresh_status}, ...]`

- `GET /api/tags` в†’ `{"0x...": "Main", ...}`
- `PUT /api/tags/{wallet}` + body `{"tag": "New Name"}` в†’ update tag

#### Reports
- `GET /api/report/{wallet}` в†’ `{"report": "# Markdown...", "related_wallets": [...]}`
  - Reads `reports/{wallet}.md`
  - Computes related wallets (top addresses by volume)
  - Adds classification from `categories.json`

  - Returns transactions between wallet and counterparty
  - Used for "Show Txs" in related wallet cards

#### Background Tasks
- `POST /api/refresh/{wallet}` в†’ `{"status": "started", "task_id": "..."}`
  - Starts background task: `fetch_transactions()` в†’ `analyze_wallet()`
  - Updates `refresh_status.json` as it progresses
  - Task runs in separate thread (non-daemon)

- `GET /api/refresh-status/{wallet}` в†’ `{"status": "processing", "stage": "analyzing", ...}`
  - Reads from `refresh_status.json`

- `GET /api/active-tasks` в†’ `[{wallet, status, stage, started_at}, ...]`
  - All active refresh tasks

#### Classification
  - Calls `categories.classify_wallet()`
  - If confidence >= 0.8 and category != user_wallet в†’ auto-exclude
  - Saves to `categories.json` and `excluded_wallets.json`

#### Exclusions

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

**Important**: `daemon=False` в†’ thread is not interrupted when browser closes!

## Environment Variables

**Required**:
- `CIELO_API_KEY` вЂ” primary Cielo Finance API key
- `OPENROUTER_API_KEY` вЂ” OpenRouter API key

**Optional**:
- `CIELO_API_KEY_1..99` вЂ” additional keys for rotation
- `FULL_CHRONOLOGY_COUNT` вЂ” how many recent analyses to use for context (default: 1)
- `CHUNK_MAX_TRANSACTIONS` вЂ” target max transactions per analysis chunk (default: 30)

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
   - Use transactional operations (write to temp в†’ rename)
   - Backup state files before updating

## Performance Considerations

- **Pagination**: Don't load all transactions at once (use `max_pages`)
- **Chunking**: Limit LLM prompt size via `CHUNK_MAX_TRANSACTIONS` (default 30)
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
