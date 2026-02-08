# DeFi Wallet Analyzer

## Project Overview
Full-stack приложение для анализа криптовалютных кошельков. Получает транзакции через Cielo Finance API, анализирует их с помощью AI (Google Gemini через OpenRouter) и генерирует отчёты на русском языке.

## Tech Stack
- **Backend**: Python 3, FastAPI, Uvicorn
- **Frontend**: React 19, Vite 7, react-markdown
- **AI**: OpenRouter API (Google Gemini 3-Flash-Preview)
- **Data API**: Cielo Finance API

## Project Structure
```
├── backend_CLAUDE.md      # 📚 Backend documentation (detailed API, modules)
├── main.py                # Получение транзакций из Cielo API
├── analyze.py             # AI-анализ транзакций
├── categories.py          # Классификация кошельков (LLM)
├── portfolio.py           # Статистика портфолио (Grade A-F, P&L)
├── server.py              # FastAPI REST API + фоновые задачи
├── frontend/              # React приложение
│   ├── CLAUDE.md          # 📚 Frontend documentation (components, data flow)
│   └── src/
│       ├── App.jsx
│       └── components/
│           ├── WalletSidebar.jsx   # Список кошельков + refresh
│           ├── ReportView.jsx      # Markdown отчёты + related wallets
│           ├── ProfileView.jsx     # AI-профиль кошелька
│           └── PortfolioView.jsx   # Агрегированная статистика
├── data/                  # JSON-файлы транзакций
│   └── CLAUDE.md          # 📚 Data formats: transactions, tags, excluded wallets
├── reports/               # Markdown-отчёты + state-файлы
│   └── CLAUDE.md          # 📚 Report structure, state files, portfolio JSON
└── .env                   # API ключи
```

## 📚 Documentation Map
- **[backend_CLAUDE.md](backend_CLAUDE.md)** — Backend modules, API endpoints, background tasks, error handling
- **[frontend/CLAUDE.md](frontend/CLAUDE.md)** — React components, data flow, UI patterns, API usage
- **[data/CLAUDE.md](data/CLAUDE.md)** — Transaction formats, metadata files (tags, categories, excluded)
- **[reports/CLAUDE.md](reports/CLAUDE.md)** — Report structure, state files, portfolio/profile JSON formats
- **[SKILLS_GUIDE.md](SKILLS_GUIDE.md)** — Руководство по Skills (автоматизация задач в Claude Code)
- **[IMPROVEMENTS_SUMMARY.md](IMPROVEMENTS_SUMMARY.md)** — Обзор улучшений и рекомендации

## Commands

### Backend
```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск сервера (порт 8000)
python server.py

# Получение транзакций для кошелька напрямую
python main.py

# Анализ транзакций напрямую
python analyze.py
```

### Frontend
```bash
cd frontend

# Установка зависимостей
npm install

# Запуск dev-сервера (порт 5173, прокси на :8000)
npm run dev

# Сборка
npm run build
```

## Quick Reference

### Common Tasks
- **Add new wallet**: Frontend → POST `/api/refresh/{wallet}` → auto fetch + analyze
- **Update wallet**: WalletSidebar refresh button → background task
- **View report**: ReportView loads `reports/{wallet}.md`
- **Classify related wallet**: ReportView → "Classify" button → LLM analysis
- **Exclude wallet**: Related card → "Exclude" → saved to `excluded_wallets.json`

### Key API Endpoints (подробнее в backend_CLAUDE.md)
- `GET /api/wallets` — список кошельков с метаданными
- `GET /api/report/{wallet}` — markdown-отчёт + related wallets
- `POST /api/refresh/{wallet}` — запуск фонового обновления (fetch + analyze)
- `GET /api/refresh-status/{wallet}` — статус обновления
- `POST /api/classify-wallet/{address}` — классифицировать через LLM
- `GET /api/portfolio/{wallet}` — Grade A-F, P&L, win rate
- **Полный список**: см. [backend_CLAUDE.md](backend_CLAUDE.md)

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
- Related wallets auto-classify in batches (parallel processing, configurable via `AUTO_CLASSIFY_BATCH_SIZE`)

## Environment Variables (.env)
- `CIELO_API_KEY` — primary Cielo Finance API key
- `CIELO_API_KEY_1..N` — additional keys for rotation
- `OPENROUTER_API_KEY` — OpenRouter API key for AI analysis
- `FULL_CHRONOLOGY_COUNT` — number of recent analyses for full context (default: 1)
- `AUTO_CLASSIFY_BATCH_SIZE` — number of related wallets to classify in parallel (default: 3)

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
