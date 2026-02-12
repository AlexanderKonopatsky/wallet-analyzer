# Payment Widget - React Component

## Установка

Скопируйте файлы `PaymentWidget.jsx` и `PaymentWidget.css` в ваш React проект.

## Использование

### Базовый пример

```jsx
import React from 'react';
import PaymentWidget from './PaymentWidget';

function App() {
  return (
    <div>
      <PaymentWidget apiUrl="http://localhost:3000" />
    </div>
  );
}

export default App;
```

### Встраивание в существующую страницу

```jsx
import React from 'react';
import PaymentWidget from './PaymentWidget';

function PaymentPage() {
  return (
    <div className="payment-page">
      <header>
        <h1>Мой сайт</h1>
      </header>

      {/* Payment Widget */}
      <PaymentWidget apiUrl="https://your-api-domain.com" />

      <footer>
        <p>© 2024 My Company</p>
      </footer>
    </div>
  );
}

export default PaymentPage;
```

### Использование без фонового градиента

Если вы хотите использовать виджет без фонового градиента (например, встроить его в существующий дизайн), измените CSS:

```css
/* В PaymentWidget.css замените: */
.payment-widget {
  /* Удалите или закомментируйте эти строки: */
  /* background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); */
  /* min-height: 100vh; */

  /* И замените на: */
  background: transparent;
  min-height: auto;
}
```

### Кастомизация стилей

Вы можете переопределить стили компонента, создав свой CSS файл:

```css
/* custom-payment-widget.css */

/* Изменить цвета кнопок */
.payment-widget .btn-primary {
  background: linear-gradient(135deg, #your-color-1 0%, #your-color-2 100%);
}

/* Изменить цвет фокуса */
.payment-widget input:focus,
.payment-widget select:focus {
  border-color: #your-brand-color;
  box-shadow: 0 0 0 3px rgba(your-rgb-color, 0.1);
}

/* Изменить шрифт */
.payment-widget {
  font-family: 'Your Custom Font', sans-serif;
}
```

## Props

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| apiUrl | string | '' | URL вашего API сервера (например, 'http://localhost:3000') |

## API Requirements

Компонент требует следующие API endpoints:

### GET /api/tokens
Возвращает список доступных токенов по блокчейнам.

**Response:**
```json
{
  "tokens": {
    "eth": [
      {
        "symbol": "USDC",
        "name": "USD Coin",
        "contractAddress": "0x..."
      }
    ]
  }
}
```

### POST /api/quote
Получает котировку для платежа.

**Request:**
```json
{
  "amount": "10",
  "originToken": "eth:usdc",
  "refundAddress": "0x..."
}
```

**Response:**
```json
{
  "originAmount": "10",
  "originSymbol": "USDC",
  "destinationAmount": "9.95",
  "destinationSymbol": "USD",
  "feeUsd": "0.05"
}
```

### POST /api/payment/create
Создает новый платеж.

**Request:**
```json
{
  "amount": "10",
  "originToken": "eth:usdc",
  "refundAddress": "0x...",
  "originAmount": "10"
}
```

**Response:**
```json
{
  "id": "payment_123",
  "depositAddress": "0x...",
  "originAmount": "10",
  "originSymbol": "USDC",
  "originChain": "eth"
}
```

### GET /api/payment/:id/status
Получает статус платежа.

**Response:**
```json
{
  "status": "SUCCESS",
  "statusDescription": "Payment completed",
  "swapDetails": {
    "destinationChainTxHashes": [
      {
        "hash": "0x...",
        "explorerUrl": "https://etherscan.io/tx/0x..."
      }
    ]
  }
}
```

## Особенности

- ✅ Полностью responsive дизайн
- ✅ Поддержка множества блокчейнов
- ✅ Автоматическое обновление статуса платежа
- ✅ Нативные токены (ETH, BNB, AVAX и др.)
- ✅ Копирование адреса в буфер обмена
- ✅ Обработка ошибок
- ✅ Loading состояния

## Зависимости

- React 16.8+ (для хуков)
- Современный браузер с поддержкой Clipboard API

## Браузерная поддержка

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Opera 76+

## License

MIT
