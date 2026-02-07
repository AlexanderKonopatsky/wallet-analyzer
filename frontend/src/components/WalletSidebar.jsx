import { useState } from 'react'
import './WalletSidebar.css'

const WALLET_ACTIONS = [
  { id: 'report', label: 'Репорт' },
  { id: 'analysis', label: 'Анализ' },
  { id: 'profile', label: 'Профиль' },
]

function WalletSidebar({ wallets, selectedWallet, onSelect, onAction, onSaveTag }) {
  const [inputValue, setInputValue] = useState('')
  const [editingAddr, setEditingAddr] = useState(null)
  const [tagValue, setTagValue] = useState('')

  const isValidAddress = (addr) => /^0x[a-fA-F0-9]{40}$/.test(addr)

  const shortAddr = (addr) => `${addr.slice(0, 6)}...${addr.slice(-4)}`

  const handleAdd = () => {
    const addr = inputValue.trim().toLowerCase()
    if (isValidAddress(addr)) {
      onSelect(addr)
      setInputValue('')
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleAdd()
  }

  return (
    <div className="wallet-sidebar">
      <div className="sidebar-header">
        <div className="sidebar-add">
          <input
            type="text"
            className="sidebar-input"
            placeholder="0x address..."
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
          />
          <button
            className="sidebar-add-btn"
            onClick={handleAdd}
            disabled={!isValidAddress(inputValue.trim())}
          >
            Load
          </button>
        </div>
      </div>

      <div className="wallet-list">
        {wallets.length === 0 && (
          <div className="wallet-list-empty">
            Нет отслеживаемых кошельков.<br />
            Вставьте адрес выше.
          </div>
        )}
        {wallets.map(w => {
          const addr = w.address.toLowerCase()
          const isActive = addr === selectedWallet
          return (
            <div
              key={addr}
              className={`wallet-card${isActive ? ' active' : ''}`}
              onClick={() => onSelect(addr)}
            >
              <div className="wallet-card-top">
                {editingAddr === addr ? (
                  <div className="wallet-card-tag-edit" onClick={e => e.stopPropagation()}>
                    <input
                      type="text"
                      className="wallet-card-tag-input"
                      value={tagValue}
                      onChange={e => setTagValue(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') {
                          onSaveTag?.(addr, tagValue.trim())
                          setEditingAddr(null)
                        }
                        if (e.key === 'Escape') setEditingAddr(null)
                      }}
                      placeholder="Имя кошелька..."
                      autoFocus
                      maxLength={50}
                    />
                    <button
                      className="wallet-card-tag-save"
                      onClick={() => {
                        onSaveTag?.(addr, tagValue.trim())
                        setEditingAddr(null)
                      }}
                    >✓</button>
                  </div>
                ) : w.tag ? (
                  <span
                    className="wallet-card-tag"
                    onClick={e => {
                      e.stopPropagation()
                      setTagValue(w.tag)
                      setEditingAddr(addr)
                    }}
                  >{w.tag}</span>
                ) : (
                  <span
                    className="wallet-card-notag wallet-card-notag--clickable"
                    onClick={e => {
                      e.stopPropagation()
                      setTagValue('')
                      setEditingAddr(addr)
                    }}
                  >+ Добавить имя</span>
                )}
                {w.has_report && <span className="wallet-card-badge" title="Есть отчёт" />}
              </div>
              <div className="wallet-card-bottom">
                <span className="wallet-card-address">{shortAddr(w.address)}</span>
                <span className="wallet-card-txcount">{w.tx_count} txs</span>
              </div>
              <div className="wallet-card-actions">
                {WALLET_ACTIONS.map(action => (
                  <button
                    key={action.id}
                    className="wallet-action-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      onAction?.(addr, action.id)
                    }}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default WalletSidebar
