import { useState, useEffect, useCallback } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView from './components/ReportView'
import ProfileView from './components/ProfileView'

function countSections(markdown) {
  if (!markdown) return 0
  return (markdown.match(/^### /gm) || []).length
}

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

  // Track which sections are NEW (by original index in markdown)
  const [oldSectionCount, setOldSectionCount] = useState(null)

  // localStorage helpers for tracking viewed reports
  const getViewedTxCount = useCallback((walletAddr) => {
    try {
      const key = `wallet_viewed_${walletAddr.toLowerCase()}`
      const data = localStorage.getItem(key)
      return data ? JSON.parse(data).tx_count : 0
    } catch {
      return 0
    }
  }, [])

  const checkHasNewData = useCallback((wallet) => {
    const currentTxCount = wallet.tx_count || 0
    const viewedTxCount = getViewedTxCount(wallet.address)
    return currentTxCount > viewedTxCount && wallet.has_report
  }, [getViewedTxCount])

  // Process report data: determine NEW sections, update localStorage, remove green dot
  const processReportData = useCallback((wallet, data) => {
    const key = `wallet_viewed_${wallet.toLowerCase()}`
    const oldRaw = localStorage.getItem(key)
    const oldState = oldRaw ? JSON.parse(oldRaw) : null
    const oldTxCount = oldState?.tx_count || 0
    const storedSectionCount = oldState?.section_count

    // Show NEW badges only if: tx_count increased AND we have stored section_count
    if (data.tx_count > oldTxCount && oldState !== null && storedSectionCount !== undefined) {
      setOldSectionCount(storedSectionCount)
    } else {
      setOldSectionCount(null)
    }

    // Update localStorage with current state
    localStorage.setItem(key, JSON.stringify({
      tx_count: data.tx_count,
      last_viewed: new Date().toISOString(),
      section_count: countSections(data.markdown)
    }))

    // Remove green dot
    setWallets(prev => prev.map(w =>
      w.address.toLowerCase() === wallet.toLowerCase()
        ? { ...w, has_new_data: false }
        : w
    ))
  }, [])


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
        // Initialize localStorage for wallets that don't have it (first time load)
        walletsData.forEach(w => {
          const key = `wallet_viewed_${w.address.toLowerCase()}`
          if (!localStorage.getItem(key) && w.has_report) {
            // First time - mark as already viewed with current tx_count
            localStorage.setItem(key, JSON.stringify({
              tx_count: w.tx_count,
              last_viewed: new Date().toISOString()
            }))
          }
        })

        // Enrich wallets with has_new_data flag
        const enrichedWallets = walletsData.map(w => ({
          ...w,
          has_new_data: checkHasNewData(w)
        }))
        setWallets(enrichedWallets)

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
      const walletsData = await walletsRes.json()
      // Enrich wallets with has_new_data flag
      const enrichedWallets = walletsData.map(w => ({
        ...w,
        has_new_data: checkHasNewData(w)
      }))
      setWallets(enrichedWallets)
    } catch (err) {
      console.error('Failed to refresh wallets:', err)
    }
  }, [checkHasNewData])

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
      processReportData(wallet, data)
    } catch (err) {
      setError(err.message)
      setReport(null)
    } finally {
      setLoading(false)
    }
  }, [processReportData])

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
            await refreshWallets()
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
  }, [loadReport, refreshWallets, pollInterval])

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

  const startBulkRefresh = useCallback(async (categoryId = 'all') => {
    setError(null)

    try {
      const res = await fetch('/api/refresh-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category_id: categoryId })
      })
      const data = await res.json()

      if (data.status === 'started') {
        setRefreshStatus({
          status: 'fetching',
          detail: `Обновление ${data.started.length} кошельков...`
        })

        // Start polling for all wallets status
        const pollBulk = setInterval(async () => {
          try {
            const activeRes = await fetch('/api/active-tasks')
            const activeTasks = await activeRes.json()

            if (Object.keys(activeTasks).length === 0) {
              // All done
              clearInterval(pollBulk)
              setRefreshStatus({ status: 'done', detail: 'Все обновления завершены' })
              await refreshWallets()
              setTimeout(() => setRefreshStatus(null), 3000)
            } else {
              // Still running
              const count = Object.keys(activeTasks).length
              setRefreshStatus({
                status: 'analyzing',
                detail: `Обновляется ${count} кошельков...`
              })
            }
          } catch (err) {
            clearInterval(pollBulk)
            setRefreshStatus(null)
          }
        }, 3000)
      } else if (data.status === 'no_wallets') {
        setRefreshStatus({ status: 'error', detail: 'Нет кошельков для обновления' })
        setTimeout(() => setRefreshStatus(null), 3000)
      }
    } catch (err) {
      setError(err.message)
      setRefreshStatus(null)
    }
  }, [refreshWallets])

  const handleSelect = useCallback(async (wallet) => {
    setSelectedWallet(wallet)
    setReport(null)
    setError(null)
    setRefreshStatus(null)
    setActiveView('report')
    setProfile(null)
    setOldSectionCount(null)

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
          processReportData(wallet, data)
          setLoading(false)
        }
      } catch (err) {
        setError(err.message)
        setReport(null)
        setLoading(false)
      }
    }
  }, [startRefresh, processReportData])

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
          onBulkRefresh={startBulkRefresh}
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
              oldSectionCount={oldSectionCount}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default App
