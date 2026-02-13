# Data Directory (`data/`)

Runtime persistence for backend state.

## Top-Level Layout
- `data/{wallet}.json` - shared transaction cache per wallet
- `data/users.json` - user/auth database (users + verification codes)
- `data/users/{id}/` - per-user runtime state
- `data/reports/` - generated reports/profile artifacts
- `data/backups/` - backup zip archives

## User-Scoped Files (`data/users/{id}/`)
- `wallet_tags.json` - custom wallet labels
- `hidden_wallets.json` - hidden wallet addresses
- `refresh_status.json` - persisted task statuses for UI polling
- `analysis_consents.json` - wallets explicitly allowed for paid analysis
- `balance.json` - USD balance + transaction ledger
- `payments.json` - payment provider order/history records

## Shared Wallet Cache (`data/{wallet}.json`)
```json
{
  "wallet": "0x...",
  "last_updated": "2026-02-13T12:00:00+00:00",
  "transactions": []
}
```

## Refresh Status Model (per user)
Typical statuses:
- `idle`
- `cost_estimate`
- `fetching`
- `analyzing`
- `done`
- `error`

Status entries may include fields like:
- `detail`
- `tx_count`
- `cost_usd`
- `percent`
- `new_count`
- `total_count`
- `insufficient_balance`

## Balance Ledger (per user)
`balance.json` stores:
- `balance` (USD)
- `transactions[]` with types such as:
  - `signup_bonus`
  - `analysis`
  - `profile`
  - `payment_topup`
  - `deduction`

## Notes
- Files in `data/` are part of runtime state; treat edits carefully.
- Backup/import endpoints preserve `data/backups/` during replace-mode imports.
- Cleanup logic removes orphaned per-user task statuses for wallets no longer owned.
