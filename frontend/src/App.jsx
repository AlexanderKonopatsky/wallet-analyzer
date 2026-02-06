import { useState, useEffect, useCallback } from 'react'
import './App.css'
import WalletInput from './components/WalletInput'
import ReportView from './components/ReportView'

function App() {
  const [wallets, setWallets] = useState([])
  const [selectedWallet, setSelectedWallet] = useState('')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [refreshStatus, setRefreshStatus] = useState(null)

  // Fetch list of tracked wallets
  useEffect(() => {
    fetch('/api/wallets')
      .then(res => res.json())
      .then(setWallets)
      .catch(() => {})
  }, [])

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
        setError('No report found. Click "Refresh Data" to fetch and analyze transactions.')
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
              // Reload report and wallet list
              await loadReport(wallet)
              const walletsRes = await fetch('/api/wallets')
              setWallets(await walletsRes.json())
            }
            if (statusData.status === 'error') {
              setError(statusData.detail)
            }
            // Clear status after a delay
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
    if (wallet) loadReport(wallet)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1><span>DeFi</span> Wallet Monitor</h1>
      </header>

      <WalletInput
        wallets={wallets}
        selectedWallet={selectedWallet}
        onSelect={handleSelect}
        onRefresh={startRefresh}
        refreshStatus={refreshStatus}
        onSaveTag={saveTag}
      />

      {error && <div className="error-banner">{error}</div>}

      <ReportView
        report={report}
        loading={loading}
        walletTag={wallets.find(w => w.address.toLowerCase() === selectedWallet)?.tag || ''}
      />
    </div>
  )
}

export default App
