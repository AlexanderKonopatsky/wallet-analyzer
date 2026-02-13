# DeFi Wallet Analyzer

## Project Overview
Full-stack app for wallet transaction analysis:
- Fetches onchain transactions from Cielo API
- Runs incremental AI analysis (OpenRouter)
- Stores reports and user-scoped state on disk
- Provides authenticated FastAPI API + React UI

## Runtime Source Of Truth
- Backend runtime entrypoint: `backend/server.py`
- Frontend runtime entrypoint: `frontend/src/main.jsx`
- Production reports directory: `data/reports/` (not root `reports/`)

## Current Architecture

### Backend
- `backend/server.py` - app composition, startup, scheduler, shared helpers
- `backend/routers/` - API split by domain:
  - `auth_router.py`
  - `system_router.py`
  - `admin_backup_router.py`
  - `payment_router.py`
  - `wallets_router.py`
  - `profiles_router.py`
  - `analysis_router.py`
- `backend/user_data_store.py` - user-scoped JSON storage helpers
- `backend/backup_utils.py` - backup/import filesystem utilities
- `backend/main.py` - transaction fetching
- `backend/analyze.py` - incremental LLM analysis pipeline
- `backend/db.py` - JSON database for users/auth codes

### Frontend
- `frontend/src/App.jsx` - orchestration/state container
- `frontend/src/components/` - presentational and view components
  - includes `ActiveTasksPanel.jsx`, `CostModals.jsx`, `PaymentWidget.jsx`, `AdminBackupView.jsx`
- `frontend/src/utils/api.js` - centralized auth-aware API client
- `frontend/src/utils/walletViewState.js` - viewed-report localStorage state

### Data Layout
- `data/{wallet}.json` - global cached transaction data
- `data/reports/` - markdown reports, analysis state, profile cache
- `data/users/{id}/` - per-user tags, hidden wallets, refresh status, balance, payments, consents
- `data/backups/` - backup archives

## Documentation Map
- `backend/CLAUDE.md` - backend modules, routers, background jobs
- `frontend/CLAUDE.md` - frontend data flow and components
- `data/CLAUDE.md` - runtime data formats and ownership
- `reports/CLAUDE.md` - report/state/profile file formats

## Main API Groups
- Auth: `/api/auth/*`
- Wallet management: `/api/wallets*`, `/api/tags*`, `/api/categories*`
- Reports/profile: `/api/report/*`, `/api/profile/*`
- Analysis tasks: `/api/estimate-cost/*`, `/api/start-analysis/*`, `/api/refresh-bulk`, `/api/active-tasks`
- Payments: `/api/tokens`, `/api/quote`, `/api/payment/*`, `/api/payments`
- Admin backup/import: `/api/admin/data-backup*`, `/api/admin/data-import`

## Commands

### Backend
```bash
pip install -r requirements.txt
python backend/server.py
```

### Frontend
```bash
cd frontend
npm install
npm run dev
npm run build
```

## Notes
- Interface language: English; AI report/profile language: Russian.
- `data/` is persistent state and should not be treated as disposable temp files.
- Background jobs use threads and persist status to user-scoped files.
