# Frontend (React + Vite)

React 19 приложение с Vite 7 для визуализации анализа криптокошельков.

## Tech Stack
- **React 19** — UI framework
- **Vite 7** — dev server & bundler
- **react-markdown** — рендеринг markdown-отчётов
- **CSS Modules** — стилизация компонентов

## Project Structure
```
src/
├── main.jsx          # Entry point
├── App.jsx           # Main app component (routing, state)
├── App.css           # Global styles
├── index.css         # Base styles
└── components/
    ├── WalletSidebar.jsx     # Список кошельков + теги
    ├── WalletSidebar.css
    ├── ReportView.jsx        # Отображение markdown-отчёта
    ├── ReportView.css
    ├── ProfileView.jsx       # Профиль кошелька (AI-generated)
    ├── PortfolioView.jsx     # Агрегированная статистика
    └── PortfolioView.css
```

## Components

### App.jsx
**Responsibilities**:
- Управление выбранным кошельком (`selectedWallet` state)
- Переключение вкладок: Report / Profile / Portfolio / Related
- Загрузка списка кошельков (`/api/wallets`)
- Передача данных в дочерние компоненты

**Key State**:
```jsx
const [wallets, setWallets] = useState([])           // Список кошельков с метаданными
const [selectedWallet, setSelectedWallet] = useState(null)  // Выбранный кошелёк
const [activeTab, setActiveTab] = useState('report')       // Текущая вкладка
```

**API Endpoints Used**:
- `GET /api/wallets` — получить список кошельков + метаданные

### WalletSidebar.jsx
**Responsibilities**:
- Отображение списка кошельков с тегами
- Индикация статуса обновления (processing / completed / error)
- Кнопка обновления (refresh) для каждого кошелька
- Добавление нового кошелька
- Редактирование тегов (inline edit)

**Key Features**:
- Поллинг статуса обновления (`/api/refresh-status/{wallet}`) каждые 2 сек при активной задаче
- Цветовая индикация: 🔄 processing, ✅ completed, ❌ error
- Inline редактирование тега (двойной клик)

**API Endpoints Used**:
- `PUT /api/tags/{wallet}` — обновить тег
- `POST /api/refresh/{wallet}` — запустить обновление (fetch + analyze)
- `GET /api/refresh-status/{wallet}` — получить статус обновления

### ReportView.jsx
**Responsibilities**:
- Загрузка и отображение markdown-отчёта
- Рендеринг через `react-markdown`
- Показ "related wallets" (адреса с наибольшей активностью)
- Кнопки exclude/include для related wallets
- Автоклассификация related wallets через LLM

**Key Features**:
- **Related Wallets**: карточки с адресами, с которыми был наибольший оборот
  - Показываются: адрес, суммы sent/received (USD), количество транзакций
  - Кнопки: "Show transactions", "Exclude", "Include", "Classify" (LLM)
- **Batch Auto-Classification**: параллельная классификация нескольких related wallets
  - Управляется через `AUTO_CLASSIFY_BATCH_SIZE` (default: 3)
  - UI показывает прогресс каждого запроса
- **Transaction Details**: раскрывающиеся списки транзакций для каждого related wallet
  - Показывают: дату, тип, сумму, токен, чейн

**API Endpoints Used**:
- `GET /api/report/{wallet}` — получить markdown + related wallets
- `GET /api/related-transactions/{wallet}?counterparty={addr}&direction={sent|received}` — транзакции
- `POST /api/classify-wallet/{address}` — классифицировать через LLM
- `POST /api/excluded-wallets` — добавить в исключения
- `DELETE /api/excluded-wallets/{address}` — убрать из исключений
- `GET /api/settings` — получить настройки (batch size и др.)

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
- Отображение AI-сгенерированного профиля пользователя кошелька
- Показ поведенческих паттернов, уровня риска, основных активностей

**Data Source**: `reports/{wallet}_profile.json`

**API Endpoints Used**:
- `GET /api/profile/{wallet}` — получить профиль (если реализовано)
- Или загружается напрямую из `reports/` (статический файл)

### PortfolioView.jsx
**Responsibilities**:
- Агрегированная статистика по токенам, протоколам, чейнам
- Графики и таблицы активности

**Data Source**: `reports/{wallet}_portfolio.json`

**API Endpoints Used**:
- `GET /api/portfolio/{wallet}` — получить статистику (если реализовано)

## Styling Conventions

- **CSS Variables** (`:root`):
  - `--primary-color`, `--bg-color`, `--text-color`, etc.
  - Позволяют легко менять тему
- **Component-specific CSS**:
  - Каждый компонент имеет свой `.css` файл
  - Используйте BEM-подобную нотацию для классов

## Data Flow

1. **App.jsx** загружает список кошельков при mount
2. Пользователь выбирает кошелёк → `setSelectedWallet(addr)`
3. **ReportView** загружает отчёт для `selectedWallet`
4. Пользователь нажимает "Refresh" в **WalletSidebar**
   - Отправляется POST `/api/refresh/{wallet}`
   - Запускается фоновая задача (fetch → analyze)
   - Frontend поллит `/api/refresh-status/{wallet}` каждые 2 сек
   - При статусе "completed" обновляет UI

## Backend API Proxy

Vite прокси настроен в `vite.config.js`:
```js
server: {
  proxy: {
    '/api': 'http://localhost:8000'
  }
}
```

Все запросы `/api/*` перенаправляются на FastAPI сервер (порт 8000).

## Development

```bash
npm install         # Установка зависимостей
npm run dev         # Dev server (порт 5173)
npm run build       # Production build
npm run preview     # Preview production build
```

## Important Notes

- **CORS**: Backend (server.py) настроен для localhost:5173 и localhost:5174
- **Polling**: WalletSidebar поллит статус обновления только при активных задачах
- **Error Handling**: Все fetch-запросы обёрнуты в try-catch с fallback UI
- **Language**: Interface на английском, отчёты на русском
- **React 19 Features**: Используйте новые хуки (useTransition, useDeferredValue) для оптимизации
