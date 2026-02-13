# Reports Directory

Contains markdown analysis reports and state files for incremental processing.

## Report Files

**File**: `{wallet_address}.md`

**Format**: Markdown with chronological breakdown by day (in Russian language)

**Structure**:
```markdown
# Wallet Activity Analysis

### 2024-06-20
User actively performed intermediary or transit wallet functions on **Base** network.
- **Scenario 1:** Received 0.1 ETH and 2.9 ETH...
- **Scenario 2:** Later that evening...

**Day Summary:** Transit movement of ETH on Base network totaling around $10,000.

---

### 2024-06-21
...
```

**Generation rules** (see `analyze.py` SYSTEM_PROMPT):
- Each day — separate `### YYYY-MM-DD`
- Action descriptions are human-readable: "borrowed", "swapped", "added liquidity"
- Include amounts, tokens, platforms, chains
- Explain operation chain logic
- Required line `**Day Summary:**` with resume + key USD amounts

## State Files

**File**: `{wallet_address}_state.json`

**Purpose**: Track processed transactions for incremental analysis.

**Structure**:
```json
{
  "chunk_index": 0,
  "chronology_parts": [
    "### 2024-06-20\n...",
    "### 2024-06-21\n..."
  ],
  "processed_tx_keys": [
    "0x8bc3060cacadfa19a0d22148e625a7552013ed0a...",
    "0xf33a19248986e6fc59bdcc3266593bb779b24ddc..."
  ],
  "pending_tx_keys": []
}
```

**Fields**:
- `chunk_index` — index of last processed chunk
- `chronology_parts` — array of report parts (per chunk)
  - Each element = analysis of one chunk (usually 1 day = 1 chunk)
  - When fully generated, stitched into final `.md`
- `processed_tx_keys` — list of `tx_hash` of processed transactions
  - Used to skip already analyzed transactions
- `pending_tx_keys` — transactions in queue (usually empty)

## Profile Files

**File**: `{wallet_address}_profile.json`

**Purpose**: AI-generated wallet user profile.

**Structure**:
```json
{
  "profile_summary": "Active DeFi trader specializing in...",
  "behavior_patterns": [
    "Frequent stablecoin operations",
    "Bridge solutions use for arbitrage"
  ],
  "risk_level": "medium",
  "primary_activities": ["trading", "liquidity_provision"],
  "generated_at": "2026-02-07T..."
}
```

## Incremental Analysis Flow

1. **First analysis**:
   - `analyze.py` gets all transactions from `data/{wallet}.json`
   - Groups by days → chunks of 30 transactions
   - Sends each chunk to LLM (with context of previous "Day Summary")
   - Saves parts to `chronology_parts`, hashes to `processed_tx_keys`
   - Generates final `.md`

2. **Refresh (update)**:
   - `main.py` gets new transactions from API
   - `analyze.py` filters: takes only those whose `tx_hash` not in `processed_tx_keys`
   - Analyzes new only → adds new parts to `chronology_parts`
   - Regenerates `.md` from all parts

3. **LLM context**:
   - Full chronology (all `chronology_parts`) — for last chunk
   - Or last N "Day Summary" (FULL_CHRONOLOGY_COUNT) — for intermediate chunks
   - This lets LLM understand user's overall strategy

## Important Notes

- Markdown reports generated in **Russian language**
- `_state.json` files are critical for incremental processing — don't delete!
- For full re-analysis, delete `_state.json` (or backup first)
- `chronology_parts` can contain overlapping dates (if chunk = multiple days)
- LLM model: Google Gemini 3-Flash-Preview (via OpenRouter)
- Token limit considered during chunking (30 transactions ≈ safe size)
