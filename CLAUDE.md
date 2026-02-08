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
├── main.py          # Получение транзакций из Cielo API (ротация ключей, пагинация)
├── analyze.py       # AI-анализ транзакций (чанкинг по дням, инкрементальная обработка)
├── server.py        # FastAPI сервер (REST API, фоновые задачи)
├── frontend/        # React приложение (Vite)
│   └── src/
│       ├── App.jsx
│       └── components/
│           ├── WalletInput.jsx   # Выбор кошелька, обновление данных
│           └── ReportView.jsx    # Отображение отчётов (markdown)
├── data/            # JSON-файлы транзакций (по кошелькам) + wallet_tags.json
├── reports/         # Markdown-отчёты + state-файлы анализа
└── .env             # API ключи (CIELO_API_KEY, OPENROUTER_API_KEY и др.)
```

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

## API Endpoints
- `GET /api/settings` — получить настройки приложения (batch size и др.)
- `GET /api/wallets` — список кошельков с метаданными
- `GET /api/tags` — теги кошельков
- `PUT /api/tags/{wallet}` — обновить тег
- `GET /api/report/{wallet}` — получить markdown-отчёт
- `POST /api/refresh/{wallet}` — запуск фонового обновления (fetch + analyze)
- `GET /api/refresh-status/{wallet}` — статус обновления для конкретного кошелька
- `GET /api/active-tasks` — все активные задачи обновления
- `GET /api/excluded-wallets` — список исключённых кошельков
- `POST /api/excluded-wallets` — добавить кошелёк в исключения (manual)
- `DELETE /api/excluded-wallets/{address}` — убрать из исключений
- `POST /api/classify-wallet/{address}` — классифицировать кошелёк через LLM (auto-exclude if confident)
- `GET /api/portfolio/{wallet}` — анализ эффективности кошелька (Grade A-F, P&L, win rate, по токенам/протоколам)
- `POST /api/portfolio/{wallet}/refresh` — пересчитать анализ

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
