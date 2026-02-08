import { useState, useEffect, useCallback } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView, { TxRow } from './components/ReportView'
import ProfileView from './components/ProfileView'
import PortfolioView from './components/PortfolioView'

function countSections(markdown) {
  if (!markdown) return 0
  return (markdown.match(/^### /gm) || []).length
}

function RelatedCard({ rw, mainWallet, classificationOverride, classifyingNow }) {
  const [expandedDir, setExpandedDir] = useState(null) // 'sent' | 'received' | null
  const [txs, setTxs] = useState(null)
  const [txLoading, setTxLoading] = useState(false)

  // Use override from parent (auto-classify queue) or server-cached data
  const classification = classificationOverride || rw.classification || null

  const toggleTxs = async (direction) => {
    if (expandedDir === direction) {
      setExpandedDir(null)
      return
    }
    setExpandedDir(direction)
    setTxLoading(true)
    setTxs(null)
    try {
      const res = await fetch(
        `/api/related-transactions/${mainWallet}?counterparty=${rw.address}&direction=${direction}`
      )
      if (res.ok) {
        setTxs(await res.json())
      } else {
        setTxs([])
      }
    } catch {
      setTxs([])
    } finally {
      setTxLoading(false)
    }
  }

  return (
    <div className="related-card">
      <div className="related-card-top">
        <span className="related-card-address">
          {rw.address.slice(0, 10)}...{rw.address.slice(-6)}
        </span>
        <div className="related-card-top-right">
          <span className="related-card-total">
            {rw.total_transfers} transfers
          </span>
          {classifyingNow && (
            <span className="classification-badge classification-loading">...</span>
          )}
        </div>
      </div>

      {classification && (
        <div className="related-classification">
          <span className={`classification-badge classification-${classification.label}`}>
            {classification.label}
          </span>
          {classification.name && <span className="classification-name">{classification.name}</span>}
        </div>
      )}

      <div className="related-card-stats">
        <div className="related-stat">
          <span className="related-stat-label">Sent</span>
          <span
            className={`related-stat-value related-stat-sent related-stat-clickable ${expandedDir === 'sent' ? 'related-stat-active' : ''}`}
            onClick={() => toggleTxs('sent')}
          >
            {rw.sent_count}x &middot; ${rw.total_usd_sent.toLocaleString()}
          </span>
          <span className="related-stat-tokens">
            {rw.tokens_sent.join(', ')}
          </span>
        </div>
        <div className="related-stat">
          <span className="related-stat-label">Received</span>
          <span
            className={`related-stat-value related-stat-recv related-stat-clickable ${expandedDir === 'received' ? 'related-stat-active' : ''}`}
            onClick={() => toggleTxs('received')}
          >
            {rw.received_count}x &middot; ${rw.total_usd_received.toLocaleString()}
          </span>
          <span className="related-stat-tokens">
            {rw.tokens_received.join(', ')}
          </span>
        </div>
      </div>

      {expandedDir && (
        <div className="related-tx-list">
          {txLoading && <div className="related-tx-loading">Loading...</div>}
          {txs && txs.length === 0 && (
            <div className="related-tx-empty">No transactions found</div>
          )}
          {txs && txs.map((tx, i) => (
            <TxRow key={tx.tx_hash || i} tx={tx} />
          ))}
        </div>
      )}

      <div className="related-card-dates">
        {new Date(rw.first_interaction * 1000).toLocaleDateString('en-US')}
        {' — '}
        {new Date(rw.last_interaction * 1000).toLocaleDateString('en-US')}
      </div>
    </div>
  )
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
  const [activeView, setActiveView] = useState('report') // 'report' | 'profile' | 'portfolio'
  const [profile, setProfile] = useState(null)
  const [profileLoading, setProfileLoading] = useState(false)

  // Portfolio
  const [portfolio, setPortfolio] = useState(null)
  const [portfolioLoading, setPortfolioLoading] = useState(false)

  // Related wallets modal
  const [relatedData, setRelatedData] = useState(null)
  const [relatedLoading, setRelatedLoading] = useState(false)
  const [relatedWallet, setRelatedWallet] = useState('')
  const [showExcluded, setShowExcluded] = useState(false)

  // Auto-classification queue for related wallets
  const [classResults, setClassResults] = useState(() => {
    // Load from localStorage on mount
    try {
      const saved = localStorage.getItem('wallet_classification_cache')
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  }) // address → classification
  const [classifyingAddrs, setClassifyingAddrs] = useState([]) // currently classifying (batch)
  const [batchSize, setBatchSize] = useState(3) // default, will be loaded from settings

  // Save classResults to localStorage whenever it changes
  useEffect(() => {
    try {
      localStorage.setItem('wallet_classification_cache', JSON.stringify(classResults))
    } catch (err) {
      console.error('Failed to save classification cache:', err)
    }
  }, [classResults])

  // Load settings on mount
  useEffect(() => {
    fetch('/api/settings')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data?.auto_classify_batch_size) {
          setBatchSize(data.auto_classify_batch_size)
        }
      })
      .catch(() => {})
  }, [])

  // Batch auto-classify: when relatedData loads, classify unclassified wallets in batches
  useEffect(() => {
    if (!relatedData?.related_wallets) return

    let cancelled = false
    const classify = async () => {
      const unclassified = relatedData.related_wallets.filter(
        rw => !rw.classification && !classResults[rw.address]
      )

      console.log('[Auto-classify] Total wallets:', relatedData.related_wallets.length)
      console.log('[Auto-classify] Already classified:', relatedData.related_wallets.filter(rw => rw.classification).length)
      console.log('[Auto-classify] Cached results:', Object.keys(classResults).length)
      console.log('[Auto-classify] To classify:', unclassified.length)

      if (unclassified.length === 0) {
        console.log('[Auto-classify] Nothing to classify, skipping')
        return
      }

      // Process in batches
      for (let i = 0; i < unclassified.length; i += batchSize) {
        if (cancelled) break

        const batch = unclassified.slice(i, i + batchSize)
        const batchAddrs = batch.map(rw => rw.address)
        console.log(`[Auto-classify] Processing batch ${Math.floor(i / batchSize) + 1}:`, batchAddrs)
        setClassifyingAddrs(batchAddrs)

        // Process batch in parallel with timeout
        const results = await Promise.allSettled(
          batch.map(async (rw) => {
            const controller = new AbortController()
            const timeout = setTimeout(() => controller.abort(), 30000) // 30s timeout

            try {
              console.log('[Auto-classify] Classifying:', rw.address)
              const res = await fetch(`/api/classify-wallet/${rw.address}`, {
                method: 'POST',
                signal: controller.signal
              })
              clearTimeout(timeout)

              if (res.ok) {
                const data = await res.json()
                console.log('[Auto-classify] Result for', rw.address, ':', data.label, data.is_excluded)
                return { address: rw.address, data }
              }
              console.warn('[Auto-classify] Failed for', rw.address, ':', res.status)
              return null
            } catch (err) {
              clearTimeout(timeout)
              console.error('[Auto-classify] Error for', rw.address, ':', err.message)
              return null
            }
          })
        )

        if (cancelled) break

        // Update results and check if any were excluded
        let hasExcluded = false
        results.forEach(result => {
          if (result.status === 'fulfilled' && result.value) {
            const { address, data } = result.value
            setClassResults(prev => ({ ...prev, [address]: data }))
            if (data.is_excluded) hasExcluded = true
          }
        })

        // If any were excluded, refresh the list
        if (hasExcluded && !cancelled) {
          console.log('[Auto-classify] Some wallets excluded, refreshing list')
          setClassifyingAddrs([])
          await new Promise(r => setTimeout(r, 300))
          if (!cancelled) {
            const refreshRes = await fetch(`/api/related-wallets/${relatedWallet.toLowerCase()}`)
            if (refreshRes.ok) {
              const refreshed = await refreshRes.json()
              setRelatedData(refreshed)
            }
          }
          return // restart with refreshed data
        }
      }
      if (!cancelled) {
        console.log('[Auto-classify] Batch processing complete')
        setClassifyingAddrs([])
      }
    }

    classify()
    return () => { cancelled = true }
  }, [relatedData, batchSize]) // eslint-disable-line react-hooks/exhaustive-deps

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
        setError("Report not found. Click 'Update Data' to fetch and analyze transactions.")
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

  const loadPortfolio = useCallback(async (wallet, forceRefresh = false) => {
    if (!wallet) return
    setPortfolioLoading(true)
    setError(null)
    setActiveView('portfolio')
    try {
      const url = forceRefresh
        ? `/api/portfolio/${wallet.toLowerCase()}/refresh`
        : `/api/portfolio/${wallet.toLowerCase()}`
      const opts = forceRefresh ? { method: 'POST' } : {}
      const res = await fetch(url, opts)
      if (!res.ok) throw new Error('Failed to load portfolio')
      setPortfolio(await res.json())
    } catch (err) {
      setError(err.message)
      setPortfolio(null)
    } finally {
      setPortfolioLoading(false)
    }
  }, [])

  const fetchRelatedWallets = useCallback(async (wallet) => {
    if (!wallet) return
    setRelatedWallet(wallet)
    setRelatedLoading(true)
    setRelatedData(null)
    try {
      const res = await fetch(`/api/related-wallets/${wallet.toLowerCase()}`)
      if (!res.ok) throw new Error('Failed to load related wallets')
      const data = await res.json()
      setRelatedData(data)
    } catch (err) {
      setRelatedData({ error: err.message })
    } finally {
      setRelatedLoading(false)
    }
  }, [])

  const closeRelatedModal = () => {
    setRelatedData(null)
    setRelatedWallet('')
    setShowExcluded(false)
    // Keep classResults to avoid re-classifying on modal reopen
    setClassifyingAddrs([])
  }

  const handleRestoreWallet = useCallback(async (address) => {
    try {
      const res = await fetch(`/api/excluded-wallets/${address}`, { method: 'DELETE' })
      if (res.ok && relatedWallet) {
        fetchRelatedWallets(relatedWallet)
      }
    } catch { /* ignore */ }
  }, [relatedWallet, fetchRelatedWallets])

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
          detail: `Updating ${data.started.length} wallets...`
        })

        // Start polling for all wallets status
        const pollBulk = setInterval(async () => {
          try {
            const activeRes = await fetch('/api/active-tasks')
            const activeTasks = await activeRes.json()

            if (Object.keys(activeTasks).length === 0) {
              // All done
              clearInterval(pollBulk)
              setRefreshStatus({ status: 'done', detail: 'All updates completed' })
              await refreshWallets()
              setTimeout(() => setRefreshStatus(null), 3000)
            } else {
              // Still running
              const count = Object.keys(activeTasks).length
              setRefreshStatus({
                status: 'analyzing',
                detail: `Updating ${count} wallets...`
              })
            }
          } catch (err) {
            clearInterval(pollBulk)
            setRefreshStatus(null)
          }
        }, 3000)
      } else if (data.status === 'no_wallets') {
        setRefreshStatus({ status: 'error', detail: 'No wallets to update' })
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
    setPortfolio(null)
    setOldSectionCount(null)

    if (wallet) {
      // Try to load existing report
      setLoading(true)
      try {
        const res = await fetch(`/api/report/${wallet.toLowerCase()}`)
        if (res.status === 404) {
          // No report exists - this is a new wallet, start refresh automatically
          setLoading(false)
          setError('New wallet. Starting fetch and analysis...')
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
            if (actionId === 'related') {
              fetchRelatedWallets(wallet)
            } else if (actionId === 'profile') {
              loadProfile(wallet)
            } else if (actionId === 'analysis') {
              loadPortfolio(wallet)
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
                {isRefreshing ? 'Updating...' : 'Update Data'}
              </button>
              {refreshStatus && (
                <span className={`refresh-status status-${refreshStatus.status}`}>
                  {refreshStatus.status === 'fetching' && '● Fetching transactions...'}
                  {refreshStatus.status === 'analyzing' && '● AI analysis...'}
                  {refreshStatus.status === 'done' && '✓ Done!'}
                  {refreshStatus.status === 'error' && '✗ Error'}
                </span>
              )}
            </div>
          )}

          {error && <div className="error-banner">{error}</div>}

          {activeView === 'portfolio' ? (
            <PortfolioView
              portfolio={portfolio}
              loading={portfolioLoading}
              onRefresh={() => loadPortfolio(selectedWallet, true)}
            />
          ) : activeView === 'profile' ? (
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

      {/* Related wallets modal */}
      {(relatedData || relatedLoading) && (
        <div className="modal-overlay" onClick={closeRelatedModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Related Wallets</h2>
              <button className="modal-close" onClick={closeRelatedModal}>&times;</button>
            </div>
            <div className="modal-subheader">
              {relatedWallet.slice(0, 6)}...{relatedWallet.slice(-4)}
            </div>

            {relatedLoading && (
              <div className="modal-loading">Analyzing transactions...</div>
            )}

            {relatedData?.error && (
              <div className="error-banner">{relatedData.error}</div>
            )}

            {relatedData && !relatedData.error && (
              <>
                <div className="related-summary">
                  Found <strong>{relatedData.related_count}</strong> related wallets
                  <span className="related-summary-hint">
                    (bidirectional transfers: sent + received)
                  </span>
                  {classifyingAddrs.length > 0 && (
                    <span className="related-classifying-hint">
                      Classifying {classifyingAddrs.length} wallet{classifyingAddrs.length > 1 ? 's' : ''}...
                    </span>
                  )}
                </div>

                {relatedData.excluded_count > 0 && (
                  <div className="related-excluded-bar">
                    <span>{relatedData.excluded_count} wallet{relatedData.excluded_count > 1 ? 's' : ''} excluded</span>
                    <button onClick={() => setShowExcluded(v => !v)}>
                      {showExcluded ? 'Hide' : 'Show'}
                    </button>
                  </div>
                )}

                {relatedData.related_count === 0 && !relatedData.excluded_count && (
                  <div className="related-empty">
                    No wallets with bidirectional transfers found.
                  </div>
                )}

                <div className="related-list">
                  {relatedData.related_wallets.map(rw => (
                    <RelatedCard
                      key={rw.address}
                      rw={rw}
                      mainWallet={relatedWallet}
                      classificationOverride={classResults[rw.address]}
                      classifyingNow={classifyingAddrs.includes(rw.address)}
                    />
                  ))}
                </div>

                {showExcluded && relatedData.excluded_wallets?.length > 0 && (
                  <div className="related-excluded-section">
                    <div className="related-excluded-header">Excluded Wallets</div>
                    {relatedData.excluded_wallets.map(rw => (
                      <div key={rw.address} className="related-card related-card-excluded">
                        <div className="related-card-top">
                          <span className="related-card-address">
                            {rw.address.slice(0, 10)}...{rw.address.slice(-6)}
                          </span>
                          <div className="related-card-top-right">
                            <span className={`classification-badge classification-${rw.exclusion?.label}`}>
                              {rw.exclusion?.label}
                            </span>
                            {rw.exclusion?.name && (
                              <span className="classification-name">{rw.exclusion.name}</span>
                            )}
                            <button
                              className="related-restore-btn"
                              onClick={() => handleRestoreWallet(rw.address)}
                            >
                              Restore
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default App
