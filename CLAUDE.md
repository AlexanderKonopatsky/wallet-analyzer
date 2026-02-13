# DeFi Wallet Analyzer

## Project Overview
Full-stack application for analyzing cryptocurrency wallet transactions. Fetches transactions via Cielo Finance API, analyzes them using AI (Google Gemini via OpenRouter), and generates reports in Russian language.

## Tech Stack
- **Backend**: Python 3, FastAPI, Uvicorn
- **Frontend**: React 19, Vite 7, react-markdown
- **AI**: OpenRouter API (Google Gemini 3-Flash-Preview)
- **Data API**: Cielo Finance API

## Project Structure
```
в”њв”Ђв”Ђ backend/               # Python backend
в”‚   в”њв”Ђв”Ђ CLAUDE.md          # рџ“љ Backend documentation (API, modules, architecture)
в”‚   в”њв”Ђв”Ђ main.py            # Cielo API client (fetch transactions)
в”‚   в”њв”Ђв”Ђ analyze.py         # AI analysis engine (Gemini via OpenRouter)
в”‚   в”њв”Ђв”Ђ categories.py      # Wallet category management
в”‚   в”њв”Ђв”Ђ portfolio.py       # Portfolio statistics (Grade A-F, P&L)
в”‚   в””в”Ђв”Ђ server.py          # FastAPI REST API + background tasks
в”њв”Ђв”Ђ frontend/              # React application
в”‚   в”њв”Ђв”Ђ CLAUDE.md          # рџ“љ Frontend documentation (components, data flow)
в”‚   в””в”Ђв”Ђ src/
в”‚       в”њв”Ђв”Ђ App.jsx
в”‚       в””в”Ђв”Ђ components/
в”‚           в”њв”Ђв”Ђ WalletSidebar.jsx   # Wallet list + refresh button
в”‚           в”њв”Ђв”Ђ ReportView.jsx      # Markdown reports + related wallets
в”‚           в”њв”Ђв”Ђ ProfileView.jsx     # AI-generated wallet profile
в”‚           в””в”Ђв”Ђ PortfolioView.jsx   # Aggregated statistics
в”њв”Ђв”Ђ data/                  # Transaction JSON files
в”‚   в””в”Ђв”Ђ CLAUDE.md          # рџ“љ Data formats (transactions, tags, excluded wallets)
в”њв”Ђв”Ђ reports/               # Markdown reports + state files
в”‚   в””в”Ђв”Ђ CLAUDE.md          # рџ“љ Report structure, state files, portfolio JSON
в””в”Ђв”Ђ .env                   # API keys (not committed)
```

## рџ“љ Documentation Map
- **[backend/CLAUDE.md](backend/CLAUDE.md)** вЂ” Backend modules, API endpoints, background tasks, error handling
- **[frontend/CLAUDE.md](frontend/CLAUDE.md)** вЂ” React components, data flow, UI patterns, API usage
- **[data/CLAUDE.md](data/CLAUDE.md)** вЂ” Transaction formats, metadata files (tags, categories, excluded)
- **[reports/CLAUDE.md](reports/CLAUDE.md)** вЂ” Report structure, state files, portfolio/profile JSON formats

## Commands

### Backend
```bash
# Install dependencies
pip install -r requirements.txt


# Run server (port 8000)
python backend/server.py

# Fetch transactions for wallet directly
python backend/main.py

# Analyze transactions directly
python backend/analyze.py
```

### Frontend
```bash
cd frontend

# Install dependencies
npm install

# Run dev server (port 5173, proxy to :8000)
npm run dev

# Build for production
npm run build
```

## Quick Reference

### Common Tasks
- **Add new wallet**: Frontend в†’ POST `/api/refresh/{wallet}` в†’ auto fetch + analyze
- **Update wallet**: WalletSidebar refresh button в†’ background task
- **Auto-refresh wallets**: Enable `AUTO_REFRESH_ENABLED=true` in `.env` в†’ all wallets refreshed daily at `AUTO_REFRESH_TIME`
- **View report**: ReportView loads `reports/{wallet}.md`
- **Classify related wallet**: ReportView в†’ "Classify" button в†’ DeBank protocol detection
- **Exclude wallet**: Related card в†’ "Exclude" в†’ saved to `excluded_wallets.json`

### Key API Endpoints
- `GET /api/wallets` вЂ” list of wallets with metadata
- `GET /api/report/{wallet}` вЂ” markdown report + related wallets
- `POST /api/refresh/{wallet}` вЂ” start background refresh (fetch + analyze + auto-classify related)
- `GET /api/refresh-status/{wallet}` вЂ” refresh status
- `GET /api/portfolio/{wallet}` вЂ” Grade A-F, P&L, win rate
- **Full list**: see [backend/CLAUDE.md](backend/CLAUDE.md)

## Key Conventions
- Interface language: **English**, Reports language: **Russian**
- Transactions stored in `data/{wallet_address}.json`
- Reports in `reports/{wallet_address}.md`, state in `reports/{wallet}_state.json`
- Refresh status in `data/refresh_status.json` (persistent)
- Excluded wallets in `data/excluded_wallets.json` (human-editable: set `is_excluded` to `false` to restore)
- API keys rotate on 429 errors (up to 99 keys: CIELO_API_KEY_1..99)
- Analysis is incremental: only new transactions are processed
- Background tasks use non-daemon threads (continue independently from browser)
- When adding a new wallet, fetch + analyze automatically starts
- When enabled, classification runs in background after analysis completes (DeBank classification with threading lock for stability)
- **Scheduled auto-refresh** is optional (disabled by default, enable with `AUTO_REFRESH_ENABLED=true`)
- When enabled, all wallets for all users are automatically refreshed at the scheduled time (default: 23:00)
- Auto-refresh skips wallets that are already being refreshed to avoid conflicts

## Environment Variables (.env)
- `CIELO_API_KEY` вЂ” primary Cielo Finance API key
- `CIELO_API_KEY_1..N` вЂ” additional keys for rotation
- `OPENROUTER_API_KEY` вЂ” OpenRouter API key for AI analysis
- `FULL_CHRONOLOGY_COUNT` вЂ” number of recent analyses for full context (default: 1)
- `CHUNK_MAX_TRANSACTIONS` вЂ” target max transactions per analysis chunk (default: 30)
- `AUTO_REFRESH_ENABLED` вЂ” enable automatic scheduled refresh of all wallets (default: false)
- `AUTO_REFRESH_TIME` вЂ” time to run auto-refresh in UTC, 24-hour format, e.g., "23:00" (default: 23:00)

### Context Compression (Advanced)
- `CONTEXT_COMPRESSION_ENABLED` вЂ” enable hierarchical compression (default: true)
- `CONTEXT_COMPRESSION_WITH_WINDOW_ENABLED` вЂ” allow extra compression calls when tx-window mode is enabled (default: false)
- `CONTEXT_DAILY_COUNT` вЂ” Tier 1: number of recent summaries without compression (default: 30)
- `CONTEXT_WEEKLY_COUNT` вЂ” Tier 2: number of summaries to compress into groups (default: 30)
- `CONTEXT_TIER2_GROUP_SIZE` вЂ” summaries per group in Tier 2 (default: 5)
- `CONTEXT_TIER3_SUPER_SIZE` вЂ” groups per super-group in Tier 3 (default: 3)
- `CONTEXT_OPTIMIZED_WINDOW_ENABLED` вЂ” enable tx-window context mode (default: false)
- `CONTEXT_WINDOW_TX_COUNT` вЂ” keep summaries that cover this many recent txs (default: 500)
- `CONTEXT_IMPORTANCE_ANCHORS` вЂ” max old high-importance days kept as anchors (default: 10)
- `CONTEXT_IMPORTANCE_MIN` вЂ” minimum day importance (1-5) for anchor inclusion (default: 4)
- `CONTEXT_TX_FALLBACK_PER_DAY` вЂ” fallback tx/day when exact day count is missing (default: 1)

## Context Compression System
For large wallets (10K+ transactions), LLM context grows linearly as each chunk receives summaries from all previous chunks. The compression system reduces token usage without losing quality.

### How It Works
**3-Tier Hierarchical Compression** (chunk-based grouping):
- **Tier 1** (newest): Last N summaries shown individually (no compression)
- **Tier 2** (middle): Groups of 5 summaries в†’ LLM compression (2-3 sentences)
- **Tier 3** (oldest): Groups of 5 в†’ super-groups of 3 в†’ double LLM compression

**Key Features**:
- Only **complete groups** are compressed (incomplete groups remain as individual lines)
- LLM calls happen **once per group** (every 5 chunks), not every chunk
- Content-hash caching prevents re-compression
- Compression saved to `reports/{wallet}_state.json` в†’ `compression_cache`
- Context inspection file: `reports/{wallet}_context.md` (updated before each LLM call)

**Example** (72 summaries, defaults):
- Tier 1: 30 individual lines
- Tier 2: 6 compressed groups (30Г·5)
- Tier 3: 2 super-compressed blocks (12 groups в†’ 4 super-groups)
- **Total**: ~40 lines instead of 72

**Token Savings**: For 73-chunk wallet, context plateaus at ~4K input tokens (vs ~18K+ without compression).

Set `CONTEXT_COMPRESSION_ENABLED=false` to disable and revert to flat list behavior.

## Portfolio Analysis (Grade A-F)
New module `portfolio.py` replays all transactions chronologically (FIFO cost basis tracking) and calculates:
- **Grade (A-F)** based on win rate + profitability magnitude
- **Realized P&L** per token, protocol, and overall
- **Win Rate** and average trade metrics
- **Expandable drilldown** in UI вЂ” click token/protocol to see all individual trades

### Known Limitations & TODOs
1. **Zero-cost tokens**: Tokens acquired via lending borrow, LP, or untracked transfers have $0 cost basis. When sold, P&L is set to $0 (conservative) since true cost is unknown. This may undercount profits if the wallet acquired tokens via recognized on-chain sources (rewards, airdrops, etc.) but those weren't captured by Cielo API.

2. **No unrealized P&L**: Only realized P&L is calculated (when tokens are sold). Current holdings show quantity only, not USD value вЂ” would require live price feed.

3. **Dust filtering**: Trades <$1 cost or proceeds are excluded from metrics.

4. **Missing transaction sources**: If Cielo API doesn't capture some transfer/lending events, cost basis tracking may be incomplete.

### Future Improvements
- Integrate live price feed (Uniswap, CoinGecko) for unrealized P&L
- Classify tokens by source (swap, transfer, airdrop) to improve cost basis estimation
- Add portfolio composition heatmap (token allocation over time)
- Export portfolio data (CSV, JSON) for external analysis
- Support multi-wallet portfolio aggregation

## Important Notes
- Don't commit `.env`, `data/`, `reports/` (in .gitignore)
- CORS configured for localhost:5173 and localhost:5174
- Vite proxies `/api` to backend (port 8000)
