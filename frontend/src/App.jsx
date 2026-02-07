import { useState, useEffect, useCallback } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView from './components/ReportView'
import ProfileView from './components/ProfileView'

function App() {
  const [wallets, setWallets] = useState([])
  const [selectedWallet, setSelectedWallet] = useState('')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [refreshStatus, setRefreshStatus] = useState(null)
  const [pollInterval, setPollInterval] = useState(null)

  // Profile
  const [activeView, setActiveView] = useState('report') // 'report' | 'profile'
  const [profile, setProfile] = useState(null)
  const [profileLoading, setProfileLoading] = useState(false)


  // Cleanup poll interval on unmount
  useEffect(() => {
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval)
      }
    }
  }, [pollInterval])

  // Fetch list of tracked wallets and check for active refresh tasks
  useEffect(() => {
    Promise.all([
      fetch('/api/wallets').then(res => res.json()),
      fetch('/api/active-tasks').then(res => res.json())
    ])
      .then(([walletsData, activeTasks]) => {
        setWallets(walletsData)

        // If there's an active task and no wallet is selected, auto-select it
        const activeWallets = Object.keys(activeTasks)
        if (activeWallets.length > 0 && !selectedWallet) {
          const activeWallet = activeWallets[0]
          setSelectedWallet(activeWallet)
          setRefreshStatus(activeTasks[activeWallet])
          startMonitoring(activeWallet)
        }
      })
      .catch(() => {})
  }, [])

  const currentWallet = wallets.find(w => w.address.toLowerCase() === selectedWallet)
  const currentTag = currentWallet?.tag || ''

  const refreshWallets = useCallback(async () => {
    try {
      const walletsRes = await fetch('/api/wallets')
      setWallets(await walletsRes.json())
    } catch (err) {
      console.error('Failed to refresh wallets:', err)
    }
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

  const loadProfile = useCallback(async (wallet, forceRegenerate = false) => {
    if (!wallet) return
    setProfileLoading(true)
    setError(null)
    setActiveView('profile')
    try {
      if (!forceRegenerate) {
        const res = await fetch(`/api/profile/${wallet.toLowerCase()}`)
        if (res.ok) {
          setProfile(await res.json())
          setProfileLoading(false)
          return
        }
      }
      // Generate (or regenerate)
      const genRes = await fetch(`/api/profile/${wallet.toLowerCase()}/generate`, { method: 'POST' })
      if (!genRes.ok) {
        const err = await genRes.json()
        throw new Error(err.detail || 'Failed to generate profile')
      }
      setProfile(await genRes.json())
    } catch (err) {
      setError(err.message)
      setProfile(null)
    } finally {
      setProfileLoading(false)
    }
  }, [])

  // Monitor refresh status (polling)
  const startMonitoring = useCallback((wallet) => {
    if (!wallet) return

    // Clear any existing poll interval
    if (pollInterval) {
      clearInterval(pollInterval)
    }

    const poll = setInterval(async () => {
      try {
        const statusRes = await fetch(`/api/refresh-status/${wallet.toLowerCase()}`)
        const statusData = await statusRes.json()
        setRefreshStatus(statusData)

        if (statusData.status === 'done' || statusData.status === 'error') {
          clearInterval(poll)
          setPollInterval(null)
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
        setPollInterval(null)
        setRefreshStatus(null)
      }
    }, 2000)

    setPollInterval(poll)
  }, [loadReport, pollInterval])

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

      // Start monitoring
      startMonitoring(wallet)
    } catch (err) {
      setError(err.message)
      setRefreshStatus(null)
    }
  }, [startMonitoring])

  const handleSelect = useCallback(async (wallet) => {
    setSelectedWallet(wallet)
    setReport(null)
    setError(null)
    setRefreshStatus(null)
    setActiveView('report')
    setProfile(null)

    if (wallet) {
      // Try to load existing report
      setLoading(true)
      try {
        const res = await fetch(`/api/report/${wallet.toLowerCase()}`)
        if (res.status === 404) {
          // No report exists - this is a new wallet, start refresh automatically
          setLoading(false)
          setError('Новый кошелёк. Запускаем загрузку и анализ...')
          await startRefresh(wallet)
        } else if (!res.ok) {
          throw new Error('Failed to load report')
        } else {
          const data = await res.json()
          setReport(data)
          setLoading(false)
        }
      } catch (err) {
        setError(err.message)
        setReport(null)
        setLoading(false)
      }
    }
  }, [startRefresh])

  const isRefreshing = refreshStatus &&
    (refreshStatus.status === 'fetching' || refreshStatus.status === 'analyzing')

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
          onRefresh={refreshWallets}
          onAction={(wallet, actionId) => {
            if (actionId === 'profile') {
              loadProfile(wallet)
            } else if (actionId === 'report') {
              setActiveView('report')
              setProfile(null)
              loadReport(wallet)
            }
          }}
        />

        <div className="app-content">
          {selectedWallet && (
            <div className="wallet-toolbar">
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
          )}

          {error && <div className="error-banner">{error}</div>}

          {activeView === 'profile' ? (
            <ProfileView
              profile={profile}
              loading={profileLoading}
              onRegenerate={() => loadProfile(selectedWallet, true)}
            />
          ) : (
            <ReportView
              report={report}
              loading={loading}
              walletTag={currentTag}
              walletAddress={selectedWallet}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default App
