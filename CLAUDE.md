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
├── backend/               # Python backend
│   ├── CLAUDE.md          # 📚 Backend documentation (API, modules, architecture)
│   ├── main.py            # Cielo API client (fetch transactions)
│   ├── analyze.py         # AI analysis engine (Gemini via OpenRouter)
│   ├── categories.py      # Wallet category management
│   ├── debank_parser.py   # DeBank wallet classification (Protocol detection)
│   ├── portfolio.py       # Portfolio statistics (Grade A-F, P&L)
│   └── server.py          # FastAPI REST API + background tasks
├── frontend/              # React application
│   ├── CLAUDE.md          # 📚 Frontend documentation (components, data flow)
│   └── src/
│       ├── App.jsx
│       └── components/
│           ├── WalletSidebar.jsx   # Wallet list + refresh button
│           ├── ReportView.jsx      # Markdown reports + related wallets
│           ├── ProfileView.jsx     # AI-generated wallet profile
│           └── PortfolioView.jsx   # Aggregated statistics
├── data/                  # Transaction JSON files
│   └── CLAUDE.md          # 📚 Data formats (transactions, tags, excluded wallets)
├── reports/               # Markdown reports + state files
│   └── CLAUDE.md          # 📚 Report structure, state files, portfolio JSON
└── .env                   # API keys (not committed)
```

## 📚 Documentation Map
- **[backend/CLAUDE.md](backend/CLAUDE.md)** — Backend modules, API endpoints, background tasks, error handling
- **[frontend/CLAUDE.md](frontend/CLAUDE.md)** — React components, data flow, UI patterns, API usage
- **[data/CLAUDE.md](data/CLAUDE.md)** — Transaction formats, metadata files (tags, categories, excluded)
- **[reports/CLAUDE.md](reports/CLAUDE.md)** — Report structure, state files, portfolio/profile JSON formats

## Commands

### Backend
```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (required for DeBank classification)
playwright install chromium

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
- **Add new wallet**: Frontend → POST `/api/refresh/{wallet}` → auto fetch + analyze
- **Update wallet**: WalletSidebar refresh button → background task
- **View report**: ReportView loads `reports/{wallet}.md`
- **Classify related wallet**: ReportView → "Classify" button → DeBank protocol detection
- **Exclude wallet**: Related card → "Exclude" → saved to `excluded_wallets.json`

### Key API Endpoints
- `GET /api/wallets` — list of wallets with metadata
- `GET /api/report/{wallet}` — markdown report + related wallets
- `POST /api/refresh/{wallet}` — start background refresh (fetch + analyze + auto-classify related)
- `GET /api/refresh-status/{wallet}` — refresh status
- `GET /api/classify-status/{wallet}` — classification progress for related wallets
- `POST /api/classify-wallet/{address}` — classify single wallet via DeBank
- `GET /api/portfolio/{wallet}` — Grade A-F, P&L, win rate
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
- **Related wallets auto-classify in background** after analysis completes (DeBank classification with threading lock for stability)
- Classification continues even if browser is closed - check status via `/api/classify-status/{wallet}`

## Environment Variables (.env)
- `CIELO_API_KEY` — primary Cielo Finance API key
- `CIELO_API_KEY_1..N` — additional keys for rotation
- `OPENROUTER_API_KEY` — OpenRouter API key for AI analysis
- `FULL_CHRONOLOGY_COUNT` — number of recent analyses for full context (default: 1)
- `AUTO_CLASSIFY_BATCH_SIZE` — number of related wallets to classify in parallel (default: 3)

### Context Compression (Advanced)
- `CONTEXT_COMPRESSION_ENABLED` — enable hierarchical compression (default: true)
- `CONTEXT_DAILY_COUNT` — Tier 1: number of recent summaries without compression (default: 30)
- `CONTEXT_WEEKLY_COUNT` — Tier 2: number of summaries to compress into groups (default: 30)
- `CONTEXT_TIER2_GROUP_SIZE` — summaries per group in Tier 2 (default: 5)
- `CONTEXT_TIER3_SUPER_SIZE` — groups per super-group in Tier 3 (default: 3)

## Context Compression System
For large wallets (10K+ transactions), LLM context grows linearly as each chunk receives summaries from all previous chunks. The compression system reduces token usage without losing quality.

### How It Works
**3-Tier Hierarchical Compression** (chunk-based grouping):
- **Tier 1** (newest): Last N summaries shown individually (no compression)
- **Tier 2** (middle): Groups of 5 summaries → LLM compression (2-3 sentences)
- **Tier 3** (oldest): Groups of 5 → super-groups of 3 → double LLM compression

**Key Features**:
- Only **complete groups** are compressed (incomplete groups remain as individual lines)
- LLM calls happen **once per group** (every 5 chunks), not every chunk
- Content-hash caching prevents re-compression
- Compression saved to `reports/{wallet}_state.json` → `compression_cache`
- Context inspection file: `reports/{wallet}_context.md` (updated before each LLM call)

**Example** (72 summaries, defaults):
- Tier 1: 30 individual lines
- Tier 2: 6 compressed groups (30÷5)
- Tier 3: 2 super-compressed blocks (12 groups → 4 super-groups)
- **Total**: ~40 lines instead of 72

**Token Savings**: For 73-chunk wallet, context plateaus at ~4K input tokens (vs ~18K+ without compression).

Set `CONTEXT_COMPRESSION_ENABLED=false` to disable and revert to flat list behavior.

## Portfolio Analysis (Grade A-F)
New module `portfolio.py` replays all transactions chronologically (FIFO cost basis tracking) and calculates:
- **Grade (A-F)** based on win rate + profitability magnitude
- **Realized P&L** per token, protocol, and overall
- **Win Rate** and average trade metrics
- **Expandable drilldown** in UI — click token/protocol to see all individual trades

### Known Limitations & TODOs
1. **Zero-cost tokens**: Tokens acquired via lending borrow, LP, or untracked transfers have $0 cost basis. When sold, P&L is set to $0 (conservative) since true cost is unknown. This may undercount profits if the wallet acquired tokens via recognized on-chain sources (rewards, airdrops, etc.) but those weren't captured by Cielo API.

2. **No unrealized P&L**: Only realized P&L is calculated (when tokens are sold). Current holdings show quantity only, not USD value — would require live price feed.

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
