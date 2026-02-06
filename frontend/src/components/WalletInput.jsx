import { useState } from 'react'
import './WalletInput.css'

function WalletInput({ wallets, selectedWallet, onSelect, onRefresh, refreshStatus, onSaveTag }) {
  const [inputValue, setInputValue] = useState('')
  const [editingTag, setEditingTag] = useState(false)
  const [tagValue, setTagValue] = useState('')

  const isValidAddress = (addr) => /^0x[a-fA-F0-9]{40}$/.test(addr)

  const handleLoad = () => {
    const addr = inputValue.trim()
    if (isValidAddress(addr)) {
      onSelect(addr)
    }
  }

  const handleDropdownChange = (e) => {
    const addr = e.target.value
    if (addr) {
      setInputValue(addr)
      onSelect(addr)
      setEditingTag(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleLoad()
  }

  const handleRefresh = () => {
    const wallet = selectedWallet || inputValue.trim()
    if (isValidAddress(wallet)) {
      if (!selectedWallet) onSelect(wallet)
      onRefresh(wallet)
    }
  }

  const isRefreshing = refreshStatus &&
    (refreshStatus.status === 'fetching' || refreshStatus.status === 'analyzing')

  const shortAddr = (addr) => `${addr.slice(0, 6)}...${addr.slice(-4)}`

  const currentWallet = wallets.find(w => w.address.toLowerCase() === selectedWallet)
  const currentTag = currentWallet?.tag || ''

  const startEditTag = () => {
    setTagValue(currentTag)
    setEditingTag(true)
  }

  const saveTag = () => {
    if (selectedWallet) {
      onSaveTag(selectedWallet, tagValue.trim())
    }
    setEditingTag(false)
  }

  const handleTagKeyDown = (e) => {
    if (e.key === 'Enter') saveTag()
    if (e.key === 'Escape') setEditingTag(false)
  }

  return (
    <div className="wallet-input">
      <div className="wallet-input-row">
        <select
          className="wallet-select"
          value={selectedWallet}
          onChange={handleDropdownChange}
        >
          <option value="">Select tracked wallet...</option>
          {wallets.map(w => (
            <option key={w.address} value={w.address.toLowerCase()}>
              {w.tag ? `${w.tag} (${shortAddr(w.address)})` : shortAddr(w.address)} — {w.tx_count} txs
            </option>
          ))}
        </select>

        <input
          type="text"
          className="wallet-address-input"
          placeholder="Or paste EVM address (0x...)"
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          spellCheck={false}
        />

        {selectedWallet && !editingTag && (
          currentTag ? (
            <span className="tag-name" onClick={startEditTag}>{currentTag}</span>
          ) : (
            <span className="tag-empty" onClick={startEditTag}>+ Add name</span>
          )
        )}

        {selectedWallet && editingTag && (
          <div className="tag-edit">
            <input
              type="text"
              className="tag-input"
              value={tagValue}
              onChange={e => setTagValue(e.target.value)}
              onKeyDown={handleTagKeyDown}
              placeholder="Enter wallet name..."
              autoFocus
              maxLength={50}
            />
            <button className="btn btn-tag-save" onClick={saveTag}>Save</button>
            <button className="btn btn-tag-cancel" onClick={() => setEditingTag(false)}>Cancel</button>
          </div>
        )}
      </div>

      <div className="wallet-actions">
        <button
          className="btn btn-secondary"
          onClick={handleRefresh}
          disabled={isRefreshing || !isValidAddress(selectedWallet || inputValue.trim())}
        >
          {isRefreshing ? 'Refreshing...' : 'Refresh Data'}
        </button>

        {refreshStatus && (
          <span className={`refresh-status status-${refreshStatus.status}`}>
            {refreshStatus.status === 'fetching' && '● Fetching transactions...'}
            {refreshStatus.status === 'analyzing' && '● Analyzing with AI...'}
            {refreshStatus.status === 'done' && '✓ Done!'}
            {refreshStatus.status === 'error' && '✗ Error'}
          </span>
        )}
      </div>
    </div>
  )
}

export default WalletInput
