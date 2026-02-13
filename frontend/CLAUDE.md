# Frontend (React + Vite)

React 19 application with Vite 7 for visualizing cryptocurrency wallet analysis.

## Tech Stack
- **React 19** вЂ” UI framework
- **Vite 7** вЂ” dev server & bundler
- **react-markdown** вЂ” markdown report rendering
- **CSS Modules** вЂ” component styling

## Project Structure
```
src/
в”њв”Ђв”Ђ main.jsx          # Entry point
в”њв”Ђв”Ђ App.jsx           # Main app component (routing, state)
в”њв”Ђв”Ђ App.css           # Global styles
в”њв”Ђв”Ђ index.css         # Base styles
в””в”Ђв”Ђ components/
    в”њв”Ђв”Ђ WalletSidebar.jsx     # Wallet list + tags
    в”њв”Ђв”Ђ WalletSidebar.css
    в”њв”Ђв”Ђ ReportView.jsx        # Markdown report display
    в”њв”Ђв”Ђ ReportView.css
    в”њв”Ђв”Ђ ProfileView.jsx       # Wallet profile (AI-generated)
```

## Components

### App.jsx
**Responsibilities**:
- Manage selected wallet (`selectedWallet` state)
- Switch tabs: Report / Profile / Related
- Load wallet list (`/api/wallets`)
- Pass data to child components

**Key State**:
```jsx
const [wallets, setWallets] = useState([])           // List of wallets with metadata
const [selectedWallet, setSelectedWallet] = useState(null)  // Selected wallet
const [activeTab, setActiveTab] = useState('report')       // Current tab
```

**API Endpoints Used**:
- `GET /api/wallets` вЂ” get wallet list + metadata

### WalletSidebar.jsx
**Responsibilities**:
- Display wallet list with tags
- Show update status indicator (processing / completed / error)
- Refresh button for each wallet
- Add new wallet
- Edit tags (inline edit)

**Key Features**:
- Poll refresh status (`/api/refresh-status/{wallet}`) every 2 sec while task is active
- Color indicators: рџ”„ processing, вњ… completed, вќЊ error
- Inline tag editing (double click)

**API Endpoints Used**:
- `PUT /api/tags/{wallet}` вЂ” update tag
- `POST /api/refresh/{wallet}` вЂ” start refresh (fetch + analyze)
- `GET /api/refresh-status/{wallet}` вЂ” get refresh status

### ReportView.jsx
**Responsibilities**:
- Load and display markdown report
- Render via `react-markdown`
- Show "related wallets" (addresses with highest activity)
- Exclude/include buttons for related wallets

**Key Features**:
- **Related Wallets**: cards with addresses that had the most activity
  - Display: address, amounts sent/received (USD), transaction count
  - Buttons: "Show transactions", "Exclude", "Include", "Classify" (LLM)
- **Batch Auto-Classification**: parallel classification of multiple related wallets
  - UI shows progress for each request
- **Transaction Details**: expandable lists of transactions for each related wallet
  - Show: date, type, amount, token, chain

**API Endpoints Used**:
- `GET /api/report/{wallet}` вЂ” get markdown + related wallets
- `GET /api/settings` вЂ” get settings (batch size, etc.)

**Related Wallet Card Structure**:
```jsx
<div className="related-card">
  <div className="address">{addr}</div>
  <div className="stats">
    Sent: ${sent} | Received: ${received}
  </div>
  <div className="classification">
    {classification ? (
      <span className={confidence >= 0.8 ? 'high' : 'medium'}>
        {category} ({confidence}%)
      </span>
    ) : (
      <button onClick={classify}>Classify</button>
    )}
  </div>
  <div className="actions">
    <button onClick={toggleTxs}>Show Txs</button>
    <button onClick={exclude}>Exclude</button>
  </div>
</div>
```

### ProfileView.jsx
**Responsibilities**:
- Display AI-generated wallet user profile
- Show behavior patterns, risk level, main activities

**Data Source**: `reports/{wallet}_profile.json`

**API Endpoints Used**:
- `GET /api/profile/{wallet}` вЂ” get profile (if implemented)
- Or load directly from `reports/` (static file)

## Styling Conventions

- **CSS Variables** (`:root`):
  - `--primary-color`, `--bg-color`, `--text-color`, etc.
  - Allow easy theme switching
- **Component-specific CSS**:
  - Each component has its own `.css` file
  - Use BEM-like notation for class names

## Data Flow

1. **App.jsx** loads wallet list on mount
2. User selects wallet в†’ `setSelectedWallet(addr)`
3. **ReportView** loads report for `selectedWallet`
4. User clicks "Refresh" in **WalletSidebar**
   - POST `/api/refresh/{wallet}` is sent
   - Background task starts (fetch в†’ analyze)
   - Frontend polls `/api/refresh-status/{wallet}` every 2 sec
   - On "completed" status, UI updates

## Backend API Proxy

Vite proxy configured in `vite.config.js`:
```js
server: {
  proxy: {
    '/api': 'http://localhost:8000'
  }
}
```

All `/api/*` requests are forwarded to FastAPI server (port 8000).

## Development

```bash
npm install         # Install dependencies
npm run dev         # Dev server (port 5173)
npm run build       # Production build
npm run preview     # Preview production build
```

## Important Notes

- **CORS**: Backend (server.py) configured for localhost:5173 and localhost:5174
- **Polling**: WalletSidebar polls refresh status only while tasks are active
- **Error Handling**: All fetch requests wrapped in try-catch with fallback UI
- **Language**: Interface in English, reports in Russian
- **React 19 Features**: Use new hooks (useTransition, useDeferredValue) for optimization
