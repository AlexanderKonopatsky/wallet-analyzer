import { useState, useEffect, useCallback } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView from './components/ReportView'

function App() {
  const [wallets, setWallets] = useState([])
  const [selectedWallet, setSelectedWallet] = useState('')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [refreshStatus, setRefreshStatus] = useState(null)

  // Tag editing
  const [editingTag, setEditingTag] = useState(false)
  const [tagValue, setTagValue] = useState('')

  // Fetch list of tracked wallets
  useEffect(() => {
    fetch('/api/wallets')
      .then(res => res.json())
      .then(setWallets)
      .catch(() => {})
  }, [])

  const currentWallet = wallets.find(w => w.address.toLowerCase() === selectedWallet)
  const currentTag = currentWallet?.tag || ''

  const saveTag = useCallback(async (wallet, tag) => {
    try {
      await fetch(`/api/tags/${wallet}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag })
      })
      setWallets(prev => prev.map(w =>
        w.address.toLowerCase() === wallet.toLowerCase()
          ? { ...w, tag }
          : w
      ))
    } catch {}
  }, [])

  const loadReport = useCallback(async (wallet) => {
    if (!wallet) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/report/${wallet.toLowerCase()}`)
      if (res.status === 404) {
        setReport(null)
        setError('Отчёт не найден. Нажмите «Обновить данные» для загрузки и анализа транзакций.')
        return
      }
      if (!res.ok) throw new Error('Failed to load report')
      const data = await res.json()
      setReport(data)
    } catch (err) {
      setError(err.message)
      setReport(null)
    } finally {
      setLoading(false)
    }
  }, [])

  const startRefresh = useCallback(async (wallet) => {
    if (!wallet) return
    setError(null)

    try {
      const res = await fetch(`/api/refresh/${wallet}`, { method: 'POST' })
      const data = await res.json()

      if (data.status === 'already_running') {
        setRefreshStatus({ status: 'analyzing', detail: 'Already running...' })
      } else {
        setRefreshStatus({ status: 'fetching', detail: 'Starting...' })
      }

      // Poll for status
      const poll = setInterval(async () => {
        try {
          const statusRes = await fetch(`/api/refresh-status/${wallet.toLowerCase()}`)
          const statusData = await statusRes.json()
          setRefreshStatus(statusData)

          if (statusData.status === 'done' || statusData.status === 'error') {
            clearInterval(poll)
            if (statusData.status === 'done') {
              await loadReport(wallet)
              const walletsRes = await fetch('/api/wallets')
              setWallets(await walletsRes.json())
            }
            if (statusData.status === 'error') {
              setError(statusData.detail)
            }
            setTimeout(() => setRefreshStatus(null), 3000)
          }
        } catch {
          clearInterval(poll)
          setRefreshStatus(null)
        }
      }, 2000)
    } catch (err) {
      setError(err.message)
      setRefreshStatus(null)
    }
  }, [loadReport])

  const handleSelect = (wallet) => {
    setSelectedWallet(wallet)
    setReport(null)
    setError(null)
    setRefreshStatus(null)
    setEditingTag(false)
    if (wallet) loadReport(wallet)
  }

  const isRefreshing = refreshStatus &&
    (refreshStatus.status === 'fetching' || refreshStatus.status === 'analyzing')

  const startEditTag = () => {
    setTagValue(currentTag)
    setEditingTag(true)
  }

  const handleSaveTag = () => {
    if (selectedWallet) {
      saveTag(selectedWallet, tagValue.trim())
    }
    setEditingTag(false)
  }

  const handleTagKeyDown = (e) => {
    if (e.key === 'Enter') handleSaveTag()
    if (e.key === 'Escape') setEditingTag(false)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1><span>DeFi</span> Wallet Monitor</h1>
      </header>

      <div className="app-layout">
        <WalletSidebar
          wallets={wallets}
          selectedWallet={selectedWallet}
          onSelect={handleSelect}
          onSaveTag={saveTag}
          onAction={(wallet, actionId) => {
            console.log('wallet action:', actionId, wallet)
          }}
        />

        <div className="app-content">
          {selectedWallet && (
            <div className="wallet-toolbar">
              <div className="toolbar-left">
                {!editingTag ? (
                  currentTag ? (
                    <span className="toolbar-tag" onClick={startEditTag}>{currentTag}</span>
                  ) : (
                    <span className="toolbar-tag-empty" onClick={startEditTag}>+ Добавить имя</span>
                  )
                ) : (
                  <div className="toolbar-tag-edit">
                    <input
                      type="text"
                      className="toolbar-tag-input"
                      value={tagValue}
                      onChange={e => setTagValue(e.target.value)}
                      onKeyDown={handleTagKeyDown}
                      placeholder="Имя кошелька..."
                      autoFocus
                      maxLength={50}
                    />
                    <button className="btn btn-tag-save" onClick={handleSaveTag}>Сохранить</button>
                    <button className="btn btn-tag-cancel" onClick={() => setEditingTag(false)}>Отмена</button>
                  </div>
                )}
              </div>

              <div className="toolbar-right">
                <button
                  className="btn btn-refresh"
                  onClick={() => startRefresh(selectedWallet)}
                  disabled={isRefreshing}
                >
                  {isRefreshing ? 'Обновление...' : 'Обновить данные'}
                </button>
                {refreshStatus && (
                  <span className={`refresh-status status-${refreshStatus.status}`}>
                    {refreshStatus.status === 'fetching' && '● Загрузка транзакций...'}
                    {refreshStatus.status === 'analyzing' && '● AI-анализ...'}
                    {refreshStatus.status === 'done' && '✓ Готово!'}
                    {refreshStatus.status === 'error' && '✗ Ошибка'}
                  </span>
                )}
              </div>
            </div>
          )}

          {error && <div className="error-banner">{error}</div>}

          <ReportView
            report={report}
            loading={loading}
            walletTag={currentTag}
            walletAddress={selectedWallet}
          />
        </div>
      </div>
    </div>
  )
}

export default App
