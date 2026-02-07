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
- `GET /api/wallets` — список кошельков с метаданными
- `GET /api/tags` — теги кошельков
- `PUT /api/tags/{wallet}` — обновить тег
- `GET /api/report/{wallet}` — получить markdown-отчёт
- `POST /api/refresh/{wallet}` — запуск фонового обновления (fetch + analyze)
- `GET /api/refresh-status/{wallet}` — статус обновления

## Key Conventions
- Язык интерфейса и отчётов: **русский**
- Транзакции хранятся в `data/{wallet_address}.json`
- Отчёты в `reports/{wallet_address}.md`, состояние в `reports/{wallet}_state.json`
- Ключи API ротируются при 429 ошибках (до 99 ключей: CIELO_API_KEY_1..99)
- Анализ инкрементальный: обрабатываются только новые транзакции
- Фоновые задачи через daemon threads

## Environment Variables (.env)
- `CIELO_API_KEY` — основной ключ Cielo Finance
- `CIELO_API_KEY_1..N` — дополнительные ключи для ротации
- `OPENROUTER_API_KEY` — ключ OpenRouter для AI-анализа
- `FULL_CHRONOLOGY_COUNT` — кол-во последних анализов для полного контекста (default: 1)

## Important Notes
- Не коммитить `.env`, `data/`, `reports/` (в .gitignore)
- CORS настроен для localhost:5173 и localhost:5174
- Vite проксирует `/api` на backend (порт 8000)
