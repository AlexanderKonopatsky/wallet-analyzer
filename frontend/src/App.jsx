import { useState, useEffect, useCallback, useRef } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView, { TxRow } from './components/ReportView'
import ProfileView from './components/ProfileView'
import PortfolioView from './components/PortfolioView'
import PaymentWidget from './components/PaymentWidget'
import LoginPage from './components/LoginPage'
import { apiCall, setAuthToken, getUser, logout } from './utils/api'

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
  const MOBILE_BREAKPOINT = 900

  // Auth state
  const [user, setUser] = useState(null)
  const [authChecking, setAuthChecking] = useState(true)

  // App state
  const [wallets, setWallets] = useState([])
  const [selectedWallet, setSelectedWallet] = useState('')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [activeTasks, setActiveTasks] = useState({}) // All active refresh tasks
  const [balance, setBalance] = useState(0)
  const [isMobileLayout, setIsMobileLayout] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth <= MOBILE_BREAKPOINT
  })
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const pollIntervalRef = useRef(null)

  // Profile
  const [activeView, setActiveView] = useState('report') // 'report' | 'profile' | 'portfolio' | 'payment'
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

  // Check auth on mount
  useEffect(() => {
    const checkAuth = async () => {
      const storedUser = getUser()
      if (!storedUser) {
        setAuthChecking(false)
        return
      }

      // Verify token is still valid
      try {
        const res = await apiCall('/api/auth/me')
        if (res && res.ok) {
          const userData = await res.json()
          setUser(userData)
        } else {
          setUser(null)
        }
      } catch {
        setUser(null)
      } finally {
        setAuthChecking(false)
      }
    }

    checkAuth()
  }, [])

  // Load balance when user is authenticated
  useEffect(() => {
    if (!user) return

    const loadBalance = async () => {
      try {
        const res = await apiCall('/api/user/balance')
        if (res && res.ok) {
          const data = await res.json()
          setBalance(data.balance)
        }
      } catch (err) {
        console.error('Failed to load balance:', err)
      }
    }

    loadBalance()
  }, [user])

  // Save classResults to localStorage whenever it changes
  useEffect(() => {
    try {
      localStorage.setItem('wallet_classification_cache', JSON.stringify(classResults))
    } catch (err) {
      console.error('Failed to save classification cache:', err)
    }
  }, [classResults])

  // Load settings on mount (only if authenticated)
  useEffect(() => {
    if (!user) return

    apiCall('/api/settings')
      .then(res => res && res.ok ? res.json() : null)
      .then(data => {
        if (data?.auto_classify_batch_size) {
          setBatchSize(data.auto_classify_batch_size)
        }
      })
      .catch(() => {})
  }, [user])

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
              const res = await apiCall(`/api/classify-wallet/${rw.address}`, {
                method: 'POST',
                signal: controller.signal
              })
              clearTimeout(timeout)

              if (res && res.ok) {
                const data = await res.json()
                console.log('[Auto-classify] Result for', rw.address, ':', data.label, data.is_excluded)
                return { address: rw.address, data }
              }
              console.warn('[Auto-classify] Failed for', rw.address, ':', res ? res.status : 'no response')
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
            const refreshRes = await apiCall(`/api/related-wallets/${relatedWallet.toLowerCase()}`)
            if (refreshRes && refreshRes.ok) {
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
  const [updatedSectionIndices, setUpdatedSectionIndices] = useState(new Set())

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

  const getViewedState = useCallback((walletAddr) => {
    try {
      const key = `wallet_viewed_${walletAddr.toLowerCase()}`
      const data = localStorage.getItem(key)
      return data ? JSON.parse(data) : null
    } catch {
      return null
    }
  }, [])

  const checkHasNewData = useCallback((wallet) => {
    if (!wallet.has_report) return false

    const viewedState = getViewedState(wallet.address)

    // Never viewed before
    if (!viewedState) return true

    // Check if tx_count increased
    const currentTxCount = wallet.tx_count || 0
    const viewedTxCount = viewedState.tx_count || 0
    if (currentTxCount > viewedTxCount) return true

    // Check if report was updated after last view (for merged days)
    if (wallet.last_updated && viewedState.last_viewed) {
      const reportUpdated = new Date(wallet.last_updated)
      const lastViewed = new Date(viewedState.last_viewed)
      if (reportUpdated > lastViewed) return true
    }

    return false
  }, [getViewedState])

  // Process report data: determine NEW sections, update localStorage, remove green dot
  const processReportData = useCallback((wallet, data) => {
    const key = `wallet_viewed_${wallet.toLowerCase()}`
    const oldRaw = localStorage.getItem(key)
    const oldState = oldRaw ? JSON.parse(oldRaw) : null
    const oldTxCount = oldState?.tx_count || 0
    const storedSectionCount = oldState?.section_count

    // Create fingerprints for sections (date + content length)
    const createFingerprints = (markdown) => {
      const sections = markdown.match(/### \d{4}-\d{2}-\d{2}[^\n]*/g) || []
      return sections.map((section, idx) => {
        const date = section.match(/### (\d{4}-\d{2}-\d{2}(?: — \d{4}-\d{2}-\d{2})?)/)?.[1] || ''
        const nextSectionIdx = markdown.indexOf('### ', markdown.indexOf(section) + 1)
        const content = nextSectionIdx > 0
          ? markdown.slice(markdown.indexOf(section), nextSectionIdx)
          : markdown.slice(markdown.indexOf(section))
        return `${date}:${content.length}`
      })
    }

    const currentFingerprints = createFingerprints(data.markdown)
    const oldFingerprints = oldState?.section_fingerprints || []

    // Check if report was updated (new txs OR merged days)
    const hasNewTxs = data.tx_count > oldTxCount
    const reportUpdated = data.last_updated && oldState?.last_viewed &&
      new Date(data.last_updated) > new Date(oldState.last_viewed)

    // Find updated sections (fingerprint changed)
    const updatedIndices = new Set()
    if (oldFingerprints.length > 0) {
      currentFingerprints.forEach((fp, idx) => {
        if (idx < oldFingerprints.length && fp !== oldFingerprints[idx]) {
          updatedIndices.add(idx)
        }
      })
    }

    // Show NEW badges if: (new txs OR report updated) AND we have stored section_count
    if ((hasNewTxs || reportUpdated) && oldState !== null && storedSectionCount !== undefined) {
      setOldSectionCount(storedSectionCount)
      setUpdatedSectionIndices(updatedIndices)
    } else {
      setOldSectionCount(null)
      setUpdatedSectionIndices(new Set())
    }

    // Update localStorage with current state
    localStorage.setItem(key, JSON.stringify({
      tx_count: data.tx_count,
      last_viewed: new Date().toISOString(),
      section_count: countSections(data.markdown),
      section_fingerprints: currentFingerprints
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
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [])

  // Fetch list of tracked wallets and check for active refresh tasks
  useEffect(() => {
    if (!user) return

    Promise.all([
      apiCall('/api/wallets').then(res => res && res.json()),
      apiCall('/api/active-tasks').then(res => res && res.json())
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

        // If there's an active task, start monitoring
        if (Object.keys(activeTasks).length > 0) {
          setActiveTasks(activeTasks)
          startMonitoring()

          // Auto-select first active wallet if no wallet is selected
          const activeWallets = Object.keys(activeTasks).filter(w =>
            enrichedWallets.some(wallet => wallet.address.toLowerCase() === w.toLowerCase())
          )
          if (activeWallets.length > 0 && !selectedWallet) {
            setSelectedWallet(activeWallets[0])
          }
        }
      })
      .catch(() => {})
  }, [user])

  const currentWallet = wallets.find(w => w.address.toLowerCase() === selectedWallet)
  const currentTag = currentWallet?.tag || ''

  const refreshWallets = useCallback(async () => {
    try {
      const walletsRes = await apiCall('/api/wallets')
      if (!walletsRes) return
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
      await apiCall(`/api/tags/${wallet}`, {
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
      const res = await apiCall(`/api/report/${wallet.toLowerCase()}`)
      if (!res) return { missing: false, error: true }
      if (res.status === 404) {
        setReport(null)
        return { missing: true }
      }
      if (!res.ok) throw new Error('Failed to load report')
      const data = await res.json()
      setReport(data)
      processReportData(wallet, data)
      return { missing: false }
    } catch (err) {
      setError(err.message)
      setReport(null)
      return { missing: false, error: true }
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
        const res = await apiCall(`/api/profile/${wallet.toLowerCase()}`)
        if (res && res.ok) {
          setProfile(await res.json())
          setProfileLoading(false)
          return
        }
      }
      // Generate (or regenerate)
      const genRes = await apiCall(`/api/profile/${wallet.toLowerCase()}/generate`, { method: 'POST' })
      if (!genRes || !genRes.ok) {
        const err = genRes ? await genRes.json() : {}
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
      const res = await apiCall(url, opts)
      if (!res || !res.ok) throw new Error('Failed to load portfolio')
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
      const res = await apiCall(`/api/related-wallets/${wallet.toLowerCase()}`)
      if (!res || !res.ok) throw new Error('Failed to load related wallets')
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
      const res = await apiCall(`/api/excluded-wallets/${address}`, { method: 'DELETE' })
      if (res && res.ok && relatedWallet) {
        fetchRelatedWallets(relatedWallet)
      }
    } catch { /* ignore */ }
  }, [relatedWallet, fetchRelatedWallets])

  // Refresh balance
  const refreshBalance = useCallback(async () => {
    try {
      const res = await apiCall('/api/user/balance')
      if (res && res.ok) {
        const data = await res.json()
        setBalance(data.balance)
      }
    } catch (err) {
      console.error('Failed to refresh balance:', err)
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return

    const updateLayoutMode = () => {
      setIsMobileLayout(window.innerWidth <= MOBILE_BREAKPOINT)
    }

    updateLayoutMode()
    window.addEventListener('resize', updateLayoutMode)
    return () => window.removeEventListener('resize', updateLayoutMode)
  }, [MOBILE_BREAKPOINT])

  useEffect(() => {
    if (!isMobileLayout) {
      setMobileSidebarOpen(false)
    }
  }, [isMobileLayout])

  useEffect(() => {
    if (!mobileSidebarOpen) return

    const prevOverflow = document.body.style.overflow
    const handleEscape = (e) => {
      if (e.key === 'Escape') {
        setMobileSidebarOpen(false)
      }
    }

    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', handleEscape)

    return () => {
      document.body.style.overflow = prevOverflow
      document.removeEventListener('keydown', handleEscape)
    }
  }, [mobileSidebarOpen])

  // Monitor all active tasks (polling)
  const startMonitoring = useCallback(() => {
    // Clear any existing poll interval
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current)
      pollIntervalRef.current = null
    }

    const poll = setInterval(async () => {
      try {
        const res = await apiCall('/api/active-tasks')
        if (!res || !res.ok) {
          clearInterval(poll)
          pollIntervalRef.current = null
          setActiveTasks({})
          return
        }

        const tasks = await res.json()
        setActiveTasks(tasks)

        // Stop polling if no tasks are running
        if (Object.keys(tasks).length === 0) {
          clearInterval(poll)
          pollIntervalRef.current = null
          // Refresh wallet list to update metadata
          await refreshWallets()
          // Reload report if selected wallet was being processed
          if (selectedWallet) {
            await loadReport(selectedWallet)
          }
          // Refresh balance after analysis completes
          await refreshBalance()
        }
      } catch {
        clearInterval(poll)
        pollIntervalRef.current = null
        setActiveTasks({})
      }
    }, 2000)

    pollIntervalRef.current = poll
  }, [loadReport, refreshWallets, refreshBalance, selectedWallet])

  const estimateCost = useCallback(async (wallet) => {
    if (!wallet) return null
    try {
      // Start background fetching
      const res = await apiCall(`/api/estimate-cost/${wallet}`, { method: 'POST' })
      if (!res || !res.ok) {
        const errorData = await res.json().catch(() => ({}))
        throw new Error(errorData.detail || 'Failed to estimate cost')
      }

      // Poll for status updates until we get cost_estimate
      return new Promise((resolve, reject) => {
        const pollInterval = setInterval(async () => {
          try {
            const statusRes = await apiCall(`/api/refresh-status/${wallet}`)
            if (!statusRes || !statusRes.ok) {
              clearInterval(pollInterval)
              reject(new Error('Failed to get status'))
              return
            }

            const status = await statusRes.json()

            // Update active tasks with current status
            setActiveTasks(prev => ({
              ...prev,
              [wallet.toLowerCase()]: status
            }))

            // If we reached cost_estimate, we're done
            if (status.status === 'cost_estimate') {
              clearInterval(pollInterval)
              resolve(status)
            } else if (status.status === 'error') {
              clearInterval(pollInterval)
              reject(new Error(status.detail || 'Failed to estimate cost'))
            }
          } catch (err) {
            clearInterval(pollInterval)
            reject(err)
          }
        }, 500) // Poll every 500ms

        // Timeout after 60 seconds
        setTimeout(() => {
          clearInterval(pollInterval)
          reject(new Error('Cost estimation timed out'))
        }, 60000)
      })
    } catch (err) {
      setError(err.message)
      return null
    }
  }, [])

  const startAnalysis = useCallback(async (wallet) => {
    if (!wallet) return
    setError(null)

    // Remove cost estimate task
    setActiveTasks(prev => {
      const newTasks = { ...prev }
      delete newTasks[wallet.toLowerCase()]
      return newTasks
    })

    try {
      const res = await apiCall(`/api/start-analysis/${wallet}`, { method: 'POST' })
      if (!res) return

      const data = await res.json()

      // Start monitoring all tasks
      startMonitoring()
    } catch (err) {
      setError(err.message)
    }
  }, [startMonitoring])

  const cancelAnalysis = useCallback((wallet) => {
    // Remove cost estimate task and deselect wallet
    setActiveTasks(prev => {
      const newTasks = { ...prev }
      delete newTasks[wallet.toLowerCase()]
      return newTasks
    })
    setSelectedWallet('')
  }, [])

  const startRefresh = useCallback(async (wallet) => {
    if (!wallet) return

    // For refresh (wallet already exists), just start analysis directly
    // Cost was already shown when wallet was first added
    setError(null)

    try {
      const res = await apiCall(`/api/start-analysis/${wallet}`, { method: 'POST' })
      if (!res) return

      const data = await res.json()

      // Start monitoring all tasks
      startMonitoring()
    } catch (err) {
      setError(err.message)
    }
  }, [startMonitoring])

  const startBulkRefresh = useCallback(async (categoryId = 'all') => {
    setError(null)

    try {
      const res = await apiCall('/api/refresh-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category_id: categoryId })
      })
      if (!res) return

      const data = await res.json()

      if (data.status === 'started') {
        // Start monitoring all tasks
        startMonitoring()
      } else if (data.status === 'no_wallets') {
        setError('No wallets to update')
      }
    } catch (err) {
      setError(err.message)
    }
  }, [startMonitoring])

  const handleSelect = useCallback(async (wallet) => {
    setSelectedWallet(wallet)
    if (isMobileLayout) {
      setMobileSidebarOpen(false)
    }
    setReport(null)
    setError(null)
    setActiveView('report')
    setProfile(null)
    setPortfolio(null)
    setOldSectionCount(null)

    if (wallet) {
      // Try to load existing report
      setLoading(true)
      try {
        const res = await apiCall(`/api/report/${wallet.toLowerCase()}`)
        if (!res) {
          setLoading(false)
          return
        }
        if (res.status === 404) {
          // No report exists - this is a new wallet, estimate cost with real-time progress
          setLoading(false)

          // Start cost estimation (this will poll for updates)
          const estimate = await estimateCost(wallet)

          if (!estimate) {
            // Remove task on error
            setActiveTasks(prev => {
              const newTasks = { ...prev }
              delete newTasks[wallet.toLowerCase()]
              return newTasks
            })
          }
          // Note: activeTasks is already updated by estimateCost polling
        } else if (!res.ok) {
          throw new Error('Failed to load report')
        } else {
          const data = await res.json()
          setReport(data)
          processReportData(wallet, data)
          setLoading(false)
          // Refresh wallet list (wallet was auto-added to user's list on backend)
          refreshWallets()
        }
      } catch (err) {
        setError(err.message)
        setReport(null)
        setLoading(false)
      }
    }
  }, [estimateCost, isMobileLayout, processReportData, refreshWallets])

  const handleLogin = (token, userData) => {
    setAuthToken(token, userData)
    setUser(userData)
  }

  const handleLogout = () => {
    logout()
  }

  if (authChecking) {
    return <div className="app-loading">Loading...</div>
  }

  if (!user) {
    return <LoginPage onLogin={handleLogin} />
  }

  const isRefreshing = Object.keys(activeTasks).length > 0
  const hasActiveTasks = Object.keys(activeTasks).length > 0

  // Helper to get wallet label (tag or shortened address)
  const getWalletLabel = (address) => {
    const wallet = wallets.find(w => w.address.toLowerCase() === address.toLowerCase())
    if (wallet?.tag) return wallet.tag
    return `${address.slice(0, 8)}...${address.slice(-6)}`
  }

  const walletSidebar = (
    <WalletSidebar
      wallets={wallets}
      selectedWallet={selectedWallet}
      onSelect={handleSelect}
      onSaveTag={saveTag}
      onRefresh={refreshWallets}
      onBulkRefresh={startBulkRefresh}
      onAction={async (wallet, actionId) => {
        if (isMobileLayout) {
          setMobileSidebarOpen(false)
        }
        if (actionId === 'related') {
          fetchRelatedWallets(wallet)
        } else if (actionId === 'profile') {
          loadProfile(wallet)
        } else if (actionId === 'analysis') {
          loadPortfolio(wallet)
        } else if (actionId === 'report') {
          setActiveView('report')
          setProfile(null)
          const result = await loadReport(wallet)
          // If report is missing, automatically start fetch
          if (result?.missing) {
            setError('Starting fetch and analysis...')
            startRefresh(wallet)
          }
        }
      }}
    />
  )

  return (
    <div className={`app ${isMobileLayout ? 'app-mobile' : ''}`}>
      <header className="app-header">
        <h1><span>DeFi</span> Wallet Monitor</h1>
        <div className="user-menu">
          <div className="balance-display">
            <span className="balance-label">Balance:</span>
            <span className="balance-amount">${balance.toFixed(2)}</span>
          </div>
          <button
            onClick={() => {
              setActiveView('payment')
              if (isMobileLayout) {
                setMobileSidebarOpen(false)
              }
            }}
            className={`btn-deposit ${activeView === 'payment' ? 'btn-deposit-active' : ''}`}
          >
            Deposit
          </button>
          <button
            onClick={handleLogout}
            className={`btn-logout ${isMobileLayout ? 'btn-logout-icon' : ''}`}
            aria-label="Logout"
            title="Logout"
          >
            {isMobileLayout ? (
              <svg className="logout-glyph" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M14 7l5 5-5 5" />
                <path d="M19 12H9" />
                <path d="M11 19H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5" />
              </svg>
            ) : 'Logout'}
          </button>
        </div>
      </header>

      {isMobileLayout && (
        <div className="mobile-drawer-control">
          <button
            className="mobile-drawer-toggle"
            onClick={() => setMobileSidebarOpen(true)}
            aria-expanded={mobileSidebarOpen}
            aria-controls="mobile-wallet-drawer"
          >
            Wallets & Categories
          </button>
        </div>
      )}

      <div className={`app-layout ${isMobileLayout ? 'app-layout-mobile' : ''}`}>
        {!isMobileLayout && walletSidebar}

        {isMobileLayout && (
          <>
            <div
              className={`mobile-drawer-overlay ${mobileSidebarOpen ? 'mobile-drawer-overlay-open' : ''}`}
              onClick={() => setMobileSidebarOpen(false)}
            />
            <aside
              id="mobile-wallet-drawer"
              className={`mobile-drawer ${mobileSidebarOpen ? 'mobile-drawer-open' : ''}`}
              aria-hidden={!mobileSidebarOpen}
            >
              <div className="mobile-drawer-topbar">
                <span>Wallets</span>
                <button
                  className="mobile-drawer-close"
                  onClick={() => setMobileSidebarOpen(false)}
                  aria-label="Close wallets menu"
                >
                  Close
                </button>
              </div>
              <div className="mobile-drawer-body">
                {walletSidebar}
              </div>
            </aside>
          </>
        )}

        <div className="app-content">
          {selectedWallet && activeView !== 'payment' && (
            <div className="wallet-toolbar">
              <button
                className="btn btn-refresh"
                onClick={() => startRefresh(selectedWallet)}
                disabled={isRefreshing || !selectedWallet}
              >
                {isRefreshing ? 'Updating...' : 'Update Data'}
              </button>
            </div>
          )}

          {/* Active tasks panel */}
          {hasActiveTasks && (
            <div className="active-tasks-panel">
              <div className="active-tasks-header">
                Active Tasks ({Object.keys(activeTasks).length})
              </div>
              <div className="active-tasks-list">
                {Object.entries(activeTasks).map(([wallet, task]) => (
                  <div key={wallet} className="active-task-item">
                    <div className="active-task-wallet">
                      {getWalletLabel(wallet)}
                    </div>
                    <div className="active-task-status">
                      {task.status === 'cost_estimate' && (
                        <div className="task-status-cost-estimate">
                          <div className="cost-estimate-info">
                            <div className="cost-estimate-row">
                              <span className="cost-estimate-label">Transactions:</span>
                              <span className="cost-estimate-value">{task.tx_count.toLocaleString()}</span>
                            </div>
                            <div className="cost-estimate-row">
                              <span className="cost-estimate-label">Cost:</span>
                              <span className="cost-estimate-value cost-estimate-price">${task.cost_usd.toFixed(2)}</span>
                            </div>
                            {task.is_cached && (
                              <div className="cost-estimate-note">Transactions cached</div>
                            )}
                          </div>
                          <div className="cost-estimate-actions">
                            <button
                              className="btn-cost-start"
                              onClick={() => startAnalysis(wallet)}
                            >
                              Start
                            </button>
                            <button
                              className="btn-cost-cancel"
                              onClick={() => cancelAnalysis(wallet)}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}
                      {task.status === 'fetching' && (
                        <div className="task-status-fetching">
                          <span className="task-spinner">⟳</span>
                          <span className="task-label">Fetching transactions</span>
                          {task.new_count !== undefined && task.total_count !== undefined && (
                            <span className="task-detail">
                              {task.new_count} new, {task.total_count} total
                            </span>
                          )}
                        </div>
                      )}
                      {task.status === 'analyzing' && (
                        <div className="task-status-analyzing">
                          <span className="task-spinner">⟳</span>
                          <span className="task-label">AI analysis</span>
                          {task.percent !== undefined && (
                            <div className="task-progress">
                              <div className="task-progress-bar">
                                <div
                                  className="task-progress-fill"
                                  style={{ width: `${task.percent}%` }}
                                ></div>
                              </div>
                              <span className="task-percent">{task.percent}%</span>
                            </div>
                          )}
                        </div>
                      )}
                      {task.status === 'classifying' && (
                        <div className="task-status-classifying">
                          <span className="task-spinner">⟳</span>
                          <span className="task-label">Classifying wallets</span>
                          {task.progress && (
                            <span className="task-detail">{task.progress}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {error && <div className="error-banner">{error}</div>}

          {activeView === 'payment' ? (
            <PaymentWidget onPaymentSuccess={refreshBalance} />
          ) : activeView === 'portfolio' ? (
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
              updatedSectionIndices={updatedSectionIndices}
            />
          )}
        </div>
      </div>

    </div>
  )
}

export default App
