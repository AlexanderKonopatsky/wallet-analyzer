# Reports Documentation

## Important
Production report artifacts live in:
- `data/reports/`

This `reports/` folder in repository root is documentation-only in the current architecture.

## Artifact Types (in `data/reports/`)

### 1. Markdown report
File: `{wallet}.md`
- Human-readable chronology in Russian
- Day sections with summaries
- Used by `/api/report/{wallet}`

### 2. Incremental analysis state
File: `{wallet}_state.json`
Typical fields:
- `chunk_index`
- `chronology_parts[]`
- `processed_tx_keys[]`
- `pending_tx_keys[]`
- `compression_cache`

Used to resume/update analysis incrementally without full recomputation.

### 3. Profile cache
File: `{wallet}_profile.json`
Typical fields:
- `wallet`
- `profile_text`
- `generated_at`
- `report_hash`
- `generation_cost_usd`

Used by `/api/profile/{wallet}` and regeneration/cost logic.

### 4. Context snapshots (debug/inspection)
File: `{wallet}_context.md`
- Captures the LLM context before chunk analysis calls.

## Regeneration Rules
- Report updates are incremental based on unprocessed tx keys.
- Profile generation is cached by `report_hash` unless `force=true`.
- Deleting `{wallet}_state.json` forces re-analysis from scratch.

## Notes
- Do not treat these files as static content: they are mutable runtime artifacts.
- All writes should preserve UTF-8 and valid JSON/Markdown structure.
