# Backend (Python FastAPI)

Backend-логика для получения транзакций, AI-анализа и REST API.

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
**Purpose**: Получение транзакций из Cielo Finance API с ротацией ключей и пагинацией.

**Key Functions**:
- `fetch_transactions(wallet, max_pages=None)` — основная функция
  - Пагинация: `limit=1000`, автоматический cursor
  - Ротация API ключей при 429 (rate limit)
  - Сохранение в `data/{wallet}.json`
  - Дедупликация транзакций по `tx_hash`
  - Возвращает: `(new_count, total_count)`

**API Key Rotation**:
- Загружает `CIELO_API_KEY`, `CIELO_API_KEY_1..99` из `.env`
- При 429 автоматически переключается на следующий ключ
- Логирует ротацию: `"Rate limit, rotating to key 3/10"`

**Error Handling**:
- 429 → rotate key, retry
- 401/403 → fatal error, stop
- Network errors → retry с exponential backoff (до 3 попыток)

**Data Format**: см. `data/CLAUDE.md`

### analyze.py — AI Analysis
**Purpose**: Инкрементальный анализ транзакций через LLM (Google Gemini).

**Key Functions**:
- `analyze_wallet(wallet_address, force_full=False)` — основная функция
  - Загружает транзакции из `data/{wallet}.json`
  - Читает `reports/{wallet}_state.json` (если есть)
  - Фильтрует новые транзакции (по `processed_tx_keys`)
  - Разбивает по дням → чанки по 30 транзакций
  - Отправляет в LLM с контекстом
  - Обновляет state, генерирует `.md`

**Chunking Strategy**:
```python
# Группировка по дням
days = group_by_day(transactions)
# Разбивка на чанки (max 30 txs на чанк)
chunks = split_into_chunks(days, max_size=30)
```

**LLM Context Management**:
- **Full context**: все предыдущие "Суть дня" (для последнего чанка)
- **Limited context**: последние N "Суть дня" (FULL_CHRONOLOGY_COUNT, default=1)
- Это балансирует контекст и стоимость токенов

**System Prompt**: Описан в `analyze.py` (SYSTEM_PROMPT)
- Требования к формату (заголовки по дням, "Суть дня")
- Правила описания операций (человекочитаемо)
- Учёт контекста предыдущей активности

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

**State Management**: см. `reports/CLAUDE.md`

### categories.py — Wallet Classification
**Purpose**: Автоматическая классификация кошельков (CEX, bridge, protocol, user).

**Key Functions**:
- `classify_wallet(address, sample_txs)` — классифицировать адрес
  - Принимает несколько транзакций для анализа
  - Отправляет в LLM с промптом классификации
  - Возвращает: `{"category": "cex_deposit", "confidence": 0.95, "reasoning": "..."}`

**Categories**:
- `cex_deposit` — депозитный адрес биржи (Binance, Coinbase, etc.)
- `bridge` — межсетевой мост (LayerZero, Stargate, etc.)
- `defi_protocol` — контракт протокола (Uniswap router, Aave pool, etc.)
- `user_wallet` — обычный пользовательский кошелёк
- `unknown` — не удалось классифицировать

**Confidence Threshold**:
- `>= 0.8` → auto-exclude (если category = cex_deposit / bridge / defi_protocol)
- `< 0.8` → показать пользователю, не auto-exclude

**LLM Prompt Example**:
```
Classify the wallet based on these transactions:
[transaction list]

Return JSON: {"category": "...", "confidence": 0.0-1.0, "reasoning": "..."}
Categories: cex_deposit, bridge, defi_protocol, user_wallet, unknown
```

### portfolio.py — Portfolio Aggregation
**Purpose**: Агрегация статистики по токенам, протоколам, чейнам.

**Key Functions**:
- `generate_portfolio(wallet_address)` — генерировать статистику
  - Читает `data/{wallet}.json`
  - Агрегирует по токенам, протоколам, датам
  - Сохраняет в `reports/{wallet}_portfolio.json`

**Output**: см. `reports/CLAUDE.md` (Portfolio Files)

### server.py — FastAPI Server
**Purpose**: REST API + фоновые задачи обновления.

**Framework**: FastAPI (async)
**Port**: 8000
**CORS**: localhost:5173, localhost:5174

**Endpoints**:

#### Settings
- `GET /api/settings` → `{"auto_classify_batch_size": 3, ...}`

#### Wallets
- `GET /api/wallets` → список кошельков + метаданные
  - Читает `data/*.json`, `wallet_tags.json`, `refresh_status.json`
  - Возвращает: `[{address, tag, tx_count, last_updated, refresh_status}, ...]`

- `GET /api/tags` → `{"0x...": "Main", ...}`
- `PUT /api/tags/{wallet}` + body `{"tag": "New Name"}` → обновить тег

#### Reports
- `GET /api/report/{wallet}` → `{"report": "# Markdown...", "related_wallets": [...]}`
  - Читает `reports/{wallet}.md`
  - Вычисляет related wallets (топ адресов по обороту)
  - Добавляет классификацию из `categories.json`

- `GET /api/related-transactions/{wallet}?counterparty={addr}&direction={sent|received}`
  - Возвращает транзакции между wallet и counterparty
  - Используется для "Show Txs" в related wallet cards

#### Background Tasks
- `POST /api/refresh/{wallet}` → `{"status": "started", "task_id": "..."}`
  - Запускает фоновую задачу: `fetch_transactions()` → `analyze_wallet()`
  - Обновляет `refresh_status.json` по ходу выполнения
  - Задача выполняется в отдельном потоке (non-daemon)

- `GET /api/refresh-status/{wallet}` → `{"status": "processing", "stage": "analyzing", ...}`
  - Читает из `refresh_status.json`

- `GET /api/active-tasks` → `[{wallet, status, stage, started_at}, ...]`
  - Все активные задачи обновления

#### Classification
- `POST /api/classify-wallet/{address}` → `{"category": "...", "confidence": 0.95, ...}`
  - Вызывает `categories.classify_wallet()`
  - Если confidence >= 0.8 и category != user_wallet → auto-exclude
  - Сохраняет в `categories.json` и `excluded_wallets.json`

#### Exclusions
- `GET /api/excluded-wallets` → `[{address, category, confidence, reasoning, source}, ...]`
- `POST /api/excluded-wallets` + body `{"address": "0x..."}` → добавить (manual)
- `DELETE /api/excluded-wallets/{address}` → убрать из исключений

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

**Важно**: `daemon=False` → поток не прерывается при закрытии браузера!

## Environment Variables

**Required**:
- `CIELO_API_KEY` — primary Cielo Finance API key
- `OPENROUTER_API_KEY` — OpenRouter API key

**Optional**:
- `CIELO_API_KEY_1..99` — дополнительные ключи для ротации
- `FULL_CHRONOLOGY_COUNT` — сколько последних анализов использовать для контекста (default: 1)
- `AUTO_CLASSIFY_BATCH_SIZE` — параллельная классификация related wallets (default: 3)

## Error Handling Best Practices

1. **API Errors**:
   - Логируйте все ошибки с контекстом (wallet, endpoint, status code)
   - Используйте `HTTPException` для FastAPI
   - Возвращайте понятные сообщения пользователю

2. **LLM Errors**:
   - Retry с exponential backoff (rate limits)
   - Сохраняйте частичный результат (state файлы)
   - Логируйте промпты и ответы для отладки

3. **Data Integrity**:
   - Всегда проверяйте существование файлов перед чтением
   - Используйте транзакционные операции (write to temp → rename)
   - Бэкапьте state файлы перед обновлением

## Performance Considerations

- **Пагинация**: Не загружайте все транзакции сразу (используйте `max_pages`)
- **Chunking**: Ограничивайте размер LLM промптов (30 txs = ~2000 tokens)
- **Caching**: Не пересчитывайте одни и те же данные (state files)
- **Async**: FastAPI endpoints должны быть async (где возможно)
- **Threading**: Фоновые задачи в отдельных потоках (не блокируют API)

## Logging

Используйте `print()` или `logging` для отладки:
```python
print(f"[{wallet[:8]}] Fetching transactions: page {page_num}")
print(f"[{wallet[:8]}] Analyzing chunk {i+1}/{len(chunks)}")
print(f"[{wallet[:8]}] Classification: {category} ({confidence:.0%})")
```

Формат: `[0xdf4e06] Message` (первые 8 символов адреса)
