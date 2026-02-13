# Frontend (React + Vite)

## Purpose
Single-page app for authenticated users to:
- manage tracked wallets/categories/tags
- view markdown reports and generated profiles
- start/monitor analysis tasks
- manage balance and deposits
- run admin backup/import operations (if allowed)

## Current Structure
```
src/
  main.jsx
  App.jsx
  App.css
  index.css
  components/
    WalletSidebar.jsx
    ReportView.jsx
    ProfileView.jsx
    PaymentWidget.jsx
    AdminBackupView.jsx
    LoginPage.jsx
    ActiveTasksPanel.jsx
    CostModals.jsx
    CalendarStrip.jsx
  utils/
    api.js
    walletViewState.js
```

## App Composition
- `App.jsx` is the stateful orchestrator.
- `ActiveTasksPanel.jsx` renders running/cost-estimate tasks.
- `CostModals.jsx` contains profile-cost + insufficient-balance modals.
- `walletViewState.js` tracks viewed report state in `localStorage` and computes "new data" markers.

## API Client
- `utils/api.js` is the only fetch wrapper.
- Automatically attaches bearer token from `localStorage`.
- On `401`, clears auth and redirects to `/`.

## Main API Calls Used by App
- Auth: `/api/auth/me`
- Balance/settings: `/api/user/balance`, `/api/settings`
- Wallets/tags/categories: `/api/wallets`, `/api/tags/*`, `/api/categories*`
- Reports/profile: `/api/report/{wallet}`, `/api/profile/{wallet}*`
- Tasks: `/api/active-tasks`, `/api/estimate-cost/{wallet}`, `/api/start-analysis/{wallet}`, `/api/cancel-analysis/{wallet}`, `/api/refresh-bulk`, `/api/refresh-status/{wallet}`
- Payments: `/api/tokens`, `/api/quote`, `/api/payment/*`, `/api/payments`
- Admin backup/import: `/api/admin/data-backup*`, `/api/admin/data-import`

## Data Flow (Simplified)
1. Authenticate -> load user and balance.
2. Load wallets + active tasks.
3. Select wallet -> load report/profile.
4. Start estimate/analysis -> poll active/refresh status.
5. Update UI state from backend statuses and persisted local viewed markers.

## Dev Commands
```bash
cd frontend
npm install
npm run dev
npm run build
npm run preview
```

## Notes
- UI language is English.
- Markdown/profile content is generated in Russian by backend prompts.
- Vite proxies `/api` to backend (see `vite.config.js`).
