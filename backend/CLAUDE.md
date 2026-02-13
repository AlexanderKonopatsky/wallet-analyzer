# Backend (FastAPI)

## Purpose
Backend provides authenticated API endpoints for:
- wallet management
- report/profile retrieval and generation
- transaction analysis orchestration
- payment/deposit flow
- admin backup/import operations

## Runtime Composition
- `server.py` creates FastAPI app, configures CORS, mounts static frontend, starts optional scheduler.
- `server.py` includes routers from `backend/routers/`.

## Router Split (Current)
- `auth_router.py`
  - `/api/auth/config`
  - `/api/auth/request-code`
  - `/api/auth/verify-code`
  - `/api/auth/google`
  - `/api/auth/me`

- `system_router.py`
  - `/api/settings`
  - `/api/user/balance`
  - `/api/user/balance/deduct`

- `admin_backup_router.py`
  - `/api/admin/data-backup`
  - `/api/admin/data-backups`
  - `/api/admin/data-backups/{filename}` (GET/DELETE)
  - `/api/admin/data-import`

- `payment_router.py`
  - `/api/tokens`
  - `/api/quote`
  - `/api/payment/create`
  - `/api/payment/{payment_id}/status`
  - `/api/payments`

- `wallets_router.py`
  - `/api/wallets`
  - `/api/tags` + `/api/tags/{wallet}`
  - `/api/categories` + `/api/categories/{category_id}`
  - `/api/wallets/{wallet}/category`
  - `/api/wallets/{wallet}/hide`
  - `/api/wallets/{wallet}/unhide`

- `profiles_router.py`
  - `/api/report/{wallet}`
  - `/api/profile/{wallet}`
  - `/api/profile/{wallet}/estimate-cost`
  - `/api/profile/{wallet}/generate`

- `analysis_router.py`
  - `/api/tx-counts/{wallet}`
  - `/api/transactions/{wallet}`
  - `/api/estimate-cost/{wallet}`
  - `/api/start-analysis/{wallet}`
  - `/api/cancel-analysis/{wallet}`
  - `/api/refresh-bulk`
  - `/api/refresh-status/{wallet}`
  - `/api/active-tasks`

## Core Modules
- `main.py` - Cielo transaction fetching and merge/save
- `analyze.py` - incremental chunked LLM analysis + report state
- `user_data_store.py` - per-user file storage operations
- `backup_utils.py` - safe backup archive creation/import helpers
- `categories.py` - user category CRUD and wallet-category assignment
- `payment_provider.py` - payment provider integration and token/address validation
- `auth.py` - email/google auth and JWT
- `db.py` - JSON database (`data/users.json`)

## State & Storage Model
- Global shared cache:
  - `data/{wallet}.json`
  - `data/reports/{wallet}.md`
  - `data/reports/{wallet}_state.json`
  - `data/reports/{wallet}_profile.json`

- User-scoped state:
  - `data/users/{id}/wallet_tags.json`
  - `data/users/{id}/hidden_wallets.json`
  - `data/users/{id}/refresh_status.json`
  - `data/users/{id}/analysis_consents.json`
  - `data/users/{id}/balance.json`
  - `data/users/{id}/payments.json`

## Background Jobs
- Analysis is threaded and status-persisted.
- `refresh_tasks` and `active_threads` are in-memory mirrors of active jobs.
- Optional daily auto-refresh runs when `AUTO_REFRESH_ENABLED=true`.

## Key Environment Variables
- `CIELO_API_KEY`, `CIELO_API_KEY_1..N`
- `OPENROUTER_API_KEY`
- `AUTO_REFRESH_ENABLED`, `AUTO_REFRESH_TIME`
- `COST_PER_1000_TX`, `COST_MULTIPLIER`
- `PROFILE_MODEL`, `PROFILE_MAX_TOKENS`, `PROFILE_COST_BASE_USD`, `PROFILE_COST_PER_WORD_USD`
- `DATA_BACKUP_ADMIN_EMAILS`, `DATA_IMPORT_MAX_MB`

## Dev Commands
```bash
python backend/server.py
python -m py_compile backend/server.py backend/routers/auth_router.py backend/routers/system_router.py backend/routers/admin_backup_router.py backend/routers/payment_router.py backend/routers/wallets_router.py backend/routers/profiles_router.py backend/routers/analysis_router.py
```
