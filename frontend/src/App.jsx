import { useState, useEffect, useCallback, useRef } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView from './components/ReportView'
import ProfileView from './components/ProfileView'
import PaymentWidget from './components/PaymentWidget'
import LoginPage from './components/LoginPage'
import { apiCall, setAuthToken, getUser, logout } from './utils/api'

const RUNNING_TASK_STATUSES = new Set(['fetching', 'analyzing'])
const KNOWN_TASK_STATUSES = new Set(['cost_estimate', 'fetching', 'analyzing'])

function countSections(markdown) {
  if (!markdown) return 0
  return (markdown.match(/^### /gm) || []).length
}

function AdminBackupView({
  backups,
  loading,
  accessDenied,
  hasRunningTasks,
  backupBusy,
  importBusy,
  downloadingFilename,
  deletingFilename,
  onRefresh,
  onDownload,
  onImportClick,
  onDownloadArchive,
  onDelete,
}) {
  const formatBytes = (bytes) => {
    const num = Number(bytes || 0)
    if (num >= 1024 * 1024 * 1024) return `${(num / (1024 * 1024 * 1024)).toFixed(2)} GB`
    if (num >= 1024 * 1024) return `${(num / (1024 * 1024)).toFixed(2)} MB`
    if (num >= 1024) return `${(num / 1024).toFixed(1)} KB`
    return `${num} B`
  }

  const formatDate = (iso) => {
    if (!iso) return '-'
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  return (
    <div className="backup-admin-view">
      <div className="backup-admin-header">
        <h2>Admin Backup</h2>
        <div className="backup-admin-actions">
          <button
            className="btn btn-refresh"
            onClick={onRefresh}
            disabled={loading || backupBusy || importBusy}
          >
            Refresh
          </button>
          <button
            className="btn btn-refresh"
            onClick={onDownload}
            disabled={loading || backupBusy || importBusy || hasRunningTasks || accessDenied}
          >
            {backupBusy ? 'Creating backup...' : 'Create & Download Backup'}
          </button>
          <button
            className="btn btn-refresh"
            onClick={onImportClick}
            disabled={loading || backupBusy || importBusy || hasRunningTasks || accessDenied}
          >
            {importBusy ? 'Importing...' : 'Import ZIP'}
          </button>
        </div>
      </div>

      <div className="backup-admin-note">
        {hasRunningTasks
          ? 'Backup/import is disabled while refresh or analysis tasks are running.'
          : 'Full backup and restore of server data folder.'}
      </div>

      {accessDenied ? (
        <div className="backup-admin-empty">Access denied for backup management.</div>
      ) : loading ? (
        <div className="backup-admin-empty">Loading backups...</div>
      ) : backups.length === 0 ? (
        <div className="backup-admin-empty">No backup archives yet.</div>
      ) : (
        <div className="backup-table-wrap">
          <table className="backup-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Size</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((item) => (
                <tr key={item.filename}>
                  <td className="backup-file">{item.filename}</td>
                  <td>{formatBytes(item.size_bytes)}</td>
                  <td>{formatDate(item.updated_at)}</td>
                  <td>
                    <div className="backup-row-actions">
                      <button
                        className="btn-backup-download"
                        onClick={() => onDownloadArchive(item.filename)}
                        disabled={Boolean(downloadingFilename) || Boolean(deletingFilename) || backupBusy || importBusy}
                      >
                        {downloadingFilename === item.filename ? 'Downloading...' : 'Download'}
                      </button>
                      <button
                        className="btn-backup-delete"
                        onClick={() => onDelete(item.filename)}
                        disabled={Boolean(downloadingFilename) || Boolean(deletingFilename) || backupBusy || importBusy}
                      >
                        {deletingFilename === item.filename ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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
  const [backupBusy, setBackupBusy] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [backups, setBackups] = useState([])
  const [backupsLoading, setBackupsLoading] = useState(false)
  const [backupAccessDenied, setBackupAccessDenied] = useState(false)
  const [downloadingBackupFilename, setDownloadingBackupFilename] = useState('')
  const [deletingBackupFilename, setDeletingBackupFilename] = useState('')
  const [isMobileLayout, setIsMobileLayout] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth <= MOBILE_BREAKPOINT
  })
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const pollIntervalRef = useRef(null)
  const activeTasksRef = useRef({})
  const importInputRef = useRef(null)

  // Profile
  const [activeView, setActiveView] = useState('report') // 'report' | 'profile' | 'payment' | 'admin-backup'
  const [profile, setProfile] = useState(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [profileCostModal, setProfileCostModal] = useState({
    open: false,
    wallet: '',
    model: '',
    estimatedCostUsd: 0
  })
  const profileCostModalResolveRef = useRef(null)
  const [insufficientBalanceModal, setInsufficientBalanceModal] = useState({
    open: false,
    wallet: '',
    requiredCostUsd: 0,
    balanceUsd: 0,
    detail: ''
  })
  const insufficientBalanceNotifiedRef = useRef(new Set())

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

  useEffect(() => {
    activeTasksRef.current = activeTasks
  }, [activeTasks])

  const requestProfileCostConfirmation = useCallback(({ wallet, model, estimatedCostUsd }) => {
    return new Promise((resolve) => {
      profileCostModalResolveRef.current = resolve
      setProfileCostModal({
        open: true,
        wallet,
        model,
        estimatedCostUsd
      })
    })
  }, [])

  const resolveProfileCostModal = useCallback((confirmed) => {
    setProfileCostModal(prev => ({ ...prev, open: false }))
    if (profileCostModalResolveRef.current) {
      profileCostModalResolveRef.current(confirmed)
      profileCostModalResolveRef.current = null
    }
  }, [])

  const closeInsufficientBalanceModal = useCallback(() => {
    setInsufficientBalanceModal(prev => ({ ...prev, open: false }))
  }, [])

  const openDepositFromInsufficientModal = useCallback(() => {
    setInsufficientBalanceModal(prev => ({ ...prev, open: false }))
    setActiveView('payment')
    if (isMobileLayout) {
      setMobileSidebarOpen(false)
    }
  }, [isMobileLayout])

  useEffect(() => {
    if (!profileCostModal.open) return

    const previousOverflow = document.body.style.overflow
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        resolveProfileCostModal(false)
      }
    }

    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [profileCostModal.open, resolveProfileCostModal])

  useEffect(() => {
    if (!insufficientBalanceModal.open) return

    const previousOverflow = document.body.style.overflow
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        closeInsufficientBalanceModal()
      }
    }

    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [insufficientBalanceModal.open, closeInsufficientBalanceModal])

  useEffect(() => {
    return () => {
      if (profileCostModalResolveRef.current) {
        profileCostModalResolveRef.current(false)
        profileCostModalResolveRef.current = null
      }
    }
  }, [])

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
        const date = section.match(/### (\d{4}-\d{2}-\d{2}(?: вЂ” \d{4}-\d{2}-\d{2})?)/)?.[1] || ''
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
    setError(null)
    setActiveView('profile')
    setProfileLoading(false)
    try {
      const walletLower = wallet.toLowerCase()

      if (!forceRegenerate) {
        const res = await apiCall(`/api/profile/${walletLower}`)
        if (res && res.ok) {
          setProfile(await res.json())
          return
        }
      }

      // Show cost to user before generation
      setProfileLoading(false)
      const estimateQuery = forceRegenerate ? '?force=true' : ''
      const estimateRes = await apiCall(`/api/profile/${walletLower}/estimate-cost${estimateQuery}`)
      if (!estimateRes || !estimateRes.ok) {
        const err = estimateRes ? await estimateRes.json().catch(() => ({})) : {}
        throw new Error(err.detail || 'Failed to estimate profile cost')
      }
      const estimate = await estimateRes.json()

      if (estimate.charge_required) {
        const estimatedCost = Number(estimate.estimated_cost_usd || 0)
        const confirmed = await requestProfileCostConfirmation({
          wallet: walletLower,
          model: estimate.model,
          estimatedCostUsd: estimatedCost
        })
        if (!confirmed) {
          return
        }
      }

      // Generate (or regenerate)
      setProfileLoading(true)
      const genQuery = forceRegenerate ? '?force=true' : ''
      const genRes = await apiCall(`/api/profile/${walletLower}/generate${genQuery}`, { method: 'POST' })
      if (!genRes || !genRes.ok) {
        const err = genRes ? await genRes.json() : {}
        throw new Error(err.detail || 'Failed to generate profile')
      }
      setProfile(await genRes.json())

      // Refresh balance after profile generation charge
      const balanceRes = await apiCall('/api/user/balance')
      if (balanceRes && balanceRes.ok) {
        const balanceData = await balanceRes.json()
        setBalance(balanceData.balance)
      }
    } catch (err) {
      setError(err.message)
      setProfile(null)
    } finally {
      setProfileLoading(false)
    }
  }, [requestProfileCostConfirmation])

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

  const loadBackupHistory = useCallback(async () => {
    setBackupsLoading(true)
    setError(null)
    try {
      const res = await apiCall('/api/admin/data-backups')
      if (!res) return

      if (!res.ok) {
        const payload = await res.json().catch(() => ({}))
        if (res.status === 403) {
          setBackupAccessDenied(true)
          setBackups([])
          return
        }
        throw new Error(payload.detail || 'Failed to load backup history')
      }

      const payload = await res.json().catch(() => ({}))
      setBackupAccessDenied(false)
      setBackups(Array.isArray(payload.backups) ? payload.backups : [])
    } catch (err) {
      setError(err.message || 'Failed to load backup history')
    } finally {
      setBackupsLoading(false)
    }
  }, [])

  const openBackupAdminView = useCallback(async () => {
    setActiveView('admin-backup')
    await loadBackupHistory()
  }, [loadBackupHistory])

  const downloadDataBackup = useCallback(async () => {
    setError(null)
    setBackupBusy(true)

    try {
      const res = await apiCall('/api/admin/data-backup')
      if (!res) return

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}))
        throw new Error(errorData.detail || 'Failed to create data backup')
      }

      const blob = await res.blob()
      const contentDisposition = res.headers.get('content-disposition') || ''
      const filenameMatch = contentDisposition.match(/filename=\"?([^\";]+)\"?/i)
      const fallback = `data_backup_${new Date().toISOString().replace(/[:.]/g, '-')}.zip`
      const filename = filenameMatch?.[1] || fallback

      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      await loadBackupHistory()
    } catch (err) {
      setError(err.message || 'Failed to create data backup')
    } finally {
      setBackupBusy(false)
    }
  }, [loadBackupHistory])

  const openDataImportPicker = useCallback(() => {
    if (!importInputRef.current) return
    importInputRef.current.value = ''
    importInputRef.current.click()
  }, [])

  const handleDataImport = useCallback(async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''

    if (!file) return
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError('Please select a .zip archive')
      return
    }

    const confirmed = window.confirm(
      'Import will replace current server data. Continue?'
    )
    if (!confirmed) return

    setError(null)
    setImportBusy(true)

    try {
      const res = await apiCall('/api/admin/data-import?mode=replace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/zip' },
        body: file
      })
      if (!res) return

      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(payload.detail || 'Failed to import data backup')
      }

      setActiveTasks({})
      setSelectedWallet('')
      setReport(null)
      setProfile(null)
      setBackups([])
      setActiveView('admin-backup')
      await refreshWallets()
      await refreshBalance()
      await loadBackupHistory()
      window.alert(`Data import completed: ${payload.imported_files ?? 0} files restored`)
    } catch (err) {
      setError(err.message || 'Failed to import data backup')
    } finally {
      setImportBusy(false)
    }
  }, [loadBackupHistory, refreshBalance, refreshWallets])

  const downloadBackupArchive = useCallback(async (filename) => {
    if (!filename) return
    setDownloadingBackupFilename(filename)
    setError(null)

    try {
      const encoded = encodeURIComponent(filename)
      const res = await apiCall(`/api/admin/data-backups/${encoded}`)
      if (!res) return

      if (!res.ok) {
        const payload = await res.json().catch(() => ({}))
        throw new Error(payload.detail || 'Failed to download backup archive')
      }

      const blob = await res.blob()
      const contentDisposition = res.headers.get('content-disposition') || ''
      const filenameMatch = contentDisposition.match(/filename=\"?([^\";]+)\"?/i)
      const outFilename = filenameMatch?.[1] || filename

      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = outFilename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (err) {
      setError(err.message || 'Failed to download backup archive')
    } finally {
      setDownloadingBackupFilename('')
    }
  }, [])

  const deleteBackupArchive = useCallback(async (filename) => {
    if (!filename) return
    const confirmed = window.confirm(`Delete backup ${filename}?`)
    if (!confirmed) return

    setDeletingBackupFilename(filename)
    setError(null)
    try {
      const encoded = encodeURIComponent(filename)
      const res = await apiCall(`/api/admin/data-backups/${encoded}`, {
        method: 'DELETE'
      })
      if (!res) return

      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(payload.detail || 'Failed to delete backup archive')
      }

      await loadBackupHistory()
    } catch (err) {
      setError(err.message || 'Failed to delete backup archive')
    } finally {
      setDeletingBackupFilename('')
    }
  }, [loadBackupHistory])

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

        // Preserve local cost_estimate rows that are not returned by /api/active-tasks.
        const mergedTasks = { ...tasks }
        for (const [wallet, status] of Object.entries(activeTasksRef.current || {})) {
          if (status?.status === 'cost_estimate' && !mergedTasks[wallet]) {
            mergedTasks[wallet] = status
          }
        }

        setActiveTasks(mergedTasks)

        for (const [wallet, status] of Object.entries(mergedTasks)) {
          if (status?.status !== 'cost_estimate' || !status?.insufficient_balance) {
            continue
          }
          if (insufficientBalanceNotifiedRef.current.has(wallet)) {
            continue
          }

          insufficientBalanceNotifiedRef.current.add(wallet)
          const requiredCostUsd = Number(status.required_cost_usd ?? status.cost_usd ?? 0)
          const balanceUsd = Number(status.balance_usd ?? 0)
          const detail = status.detail || 'Insufficient balance to start analysis'

          setInsufficientBalanceModal(prev => {
            if (prev.open) return prev
            return {
              open: true,
              wallet,
              requiredCostUsd,
              balanceUsd,
              detail
            }
          })
          break
        }

        // Stop polling if no tasks are running
        const hasRunningTasks = Object.values(mergedTasks).some((task) =>
          RUNNING_TASK_STATUSES.has(task?.status)
        )

        if (!hasRunningTasks) {
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
    const walletLower = wallet.toLowerCase()
    insufficientBalanceNotifiedRef.current.delete(walletLower)

    try {
      const res = await apiCall(`/api/start-analysis/${wallet}`, { method: 'POST' })
      if (!res) return
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to start analysis')
      }

      await res.json().catch(() => ({}))

      // Start monitoring all tasks
      startMonitoring()
    } catch (err) {
      setError(err.message)
    }
  }, [startMonitoring])

  const cancelAnalysis = useCallback(async (wallet) => {
    if (!wallet) return
    const walletLower = wallet.toLowerCase()
    setError(null)

    try {
      const res = await apiCall(`/api/cancel-analysis/${wallet}`, { method: 'POST' })
      if (!res) return
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to cancel analysis')
      }

      // Remove task locally after backend confirms cancellation persisted.
      setActiveTasks(prev => {
        const newTasks = { ...prev }
        delete newTasks[walletLower]
        return newTasks
      })
      insufficientBalanceNotifiedRef.current.delete(walletLower)
      setInsufficientBalanceModal(prev =>
        prev.wallet === walletLower ? { ...prev, open: false } : prev
      )
      setSelectedWallet('')
    } catch (err) {
      setError(err.message || 'Failed to cancel analysis')
    }
  }, [])

  const startRefresh = useCallback(async (wallet) => {
    if (!wallet) return

    // For refresh (wallet already exists), just start analysis directly
    // Cost was already shown when wallet was first added
    setError(null)
    insufficientBalanceNotifiedRef.current.delete(wallet.toLowerCase())

    try {
      const res = await apiCall(`/api/start-analysis/${wallet}`, { method: 'POST' })
      if (!res) return
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to start analysis')
      }

      await res.json().catch(() => ({}))

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
        const startedCount = Array.isArray(data.started) ? data.started.length : 0
        const runningCount = Array.isArray(data.already_running) ? data.already_running.length : 0
        const skippedHidden = Array.isArray(data.skipped_hidden) ? data.skipped_hidden.length : 0
        const skippedNoConsent = Array.isArray(data.skipped_no_consent) ? data.skipped_no_consent.length : 0

        // Start monitoring when something is running or has just been started.
        if (startedCount > 0 || runningCount > 0) {
          startMonitoring()
        }

        // Show an explicit reason when nothing was started.
        if (startedCount === 0 && runningCount === 0) {
          if (skippedNoConsent > 0 || skippedHidden > 0) {
            setError(
              `No eligible wallets to update (no consent: ${skippedNoConsent}, hidden: ${skippedHidden})`
            )
          } else {
            setError('No wallets to update')
          }
        }
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

  const taskEntries = Object.entries(activeTasks).filter(([, task]) =>
    task && typeof task === 'object'
  )
  const hasRunningTasks = taskEntries.some(([, task]) =>
    RUNNING_TASK_STATUSES.has(task.status)
  )
  const isRefreshing = hasRunningTasks
  const hasActiveTasks = taskEntries.length > 0

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
        if (actionId === 'profile') {
          loadProfile(wallet)
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
          <input
            ref={importInputRef}
            type="file"
            accept=".zip,application/zip"
            className="data-import-input"
            onChange={handleDataImport}
          />
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
            onClick={openBackupAdminView}
            className={`btn-admin ${activeView === 'admin-backup' ? 'btn-admin-active' : ''}`}
            disabled={backupBusy || importBusy}
            title="Open backup management"
          >
            Backups
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
          {selectedWallet && (activeView === 'report' || activeView === 'profile') && (
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
                {`Active Tasks (${taskEntries.length})`}
              </div>
              <div className="active-tasks-list">
                {taskEntries.map(([wallet, task]) => (
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
                              <span className="cost-estimate-value">{Number(task.tx_count || 0).toLocaleString()}</span>
                            </div>
                            <div className="cost-estimate-row">
                              <span className="cost-estimate-label">Cost:</span>
                              <span className="cost-estimate-value cost-estimate-price">${Number(task.cost_usd || 0).toFixed(2)}</span>
                            </div>
                            {task.is_cached && (
                              <div className="cost-estimate-note">Transactions cached</div>
                            )}
                            {task.insufficient_balance && (
                              <div className="cost-estimate-warning">
                                Not enough balance: need ${Number(task.required_cost_usd ?? task.cost_usd ?? 0).toFixed(2)}, available ${Number(task.balance_usd ?? 0).toFixed(2)}.
                              </div>
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
                          <span className="task-spinner" aria-hidden="true"></span>
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
                          <span className="task-spinner" aria-hidden="true"></span>
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
                      {!KNOWN_TASK_STATUSES.has(task.status) && (
                        <div className="task-status-unknown">
                          <span className="task-label">Task in progress</span>
                          {task.detail && <span className="task-detail">{task.detail}</span>}
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
          ) : activeView === 'admin-backup' ? (
            <AdminBackupView
              backups={backups}
              loading={backupsLoading}
              accessDenied={backupAccessDenied}
              hasRunningTasks={hasRunningTasks}
              backupBusy={backupBusy}
              importBusy={importBusy}
              downloadingFilename={downloadingBackupFilename}
              deletingFilename={deletingBackupFilename}
              onRefresh={loadBackupHistory}
              onDownload={downloadDataBackup}
              onImportClick={openDataImportPicker}
              onDownloadArchive={downloadBackupArchive}
              onDelete={deleteBackupArchive}
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

      {insufficientBalanceModal.open && (
        <div
          className="modal-overlay insufficient-balance-overlay"
          onClick={closeInsufficientBalanceModal}
        >
          <div
            className="modal-content insufficient-balance-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="insufficient-balance-title"
          >
            <div className="modal-header">
              <h2 id="insufficient-balance-title">Insufficient Balance</h2>
              <button
                className="modal-close"
                onClick={closeInsufficientBalanceModal}
                aria-label="Close"
              >
                x
              </button>
            </div>

            <div className="insufficient-balance-body">
              <div className="insufficient-balance-detail">{insufficientBalanceModal.detail}</div>
              {insufficientBalanceModal.wallet && (
                <div className="insufficient-balance-meta">
                  <div>
                    Wallet: {insufficientBalanceModal.wallet.slice(0, 8)}...{insufficientBalanceModal.wallet.slice(-6)}
                  </div>
                  <div>
                    Required: ${Number(insufficientBalanceModal.requiredCostUsd || 0).toFixed(2)}
                  </div>
                  <div>
                    Current balance: ${Number(insufficientBalanceModal.balanceUsd || 0).toFixed(2)}
                  </div>
                </div>
              )}
            </div>

            <div className="insufficient-balance-actions">
              <button
                className="btn-cost-cancel"
                onClick={closeInsufficientBalanceModal}
              >
                Close
              </button>
              <button
                className="btn-deposit insufficient-balance-deposit"
                onClick={openDepositFromInsufficientModal}
              >
                Deposit
              </button>
            </div>
          </div>
        </div>
      )}

      {profileCostModal.open && (
        <div
          className="modal-overlay profile-cost-overlay"
          onClick={() => resolveProfileCostModal(false)}
        >
          <div
            className="modal-content profile-cost-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="profile-cost-title"
          >
            <div className="modal-header">
              <h2 id="profile-cost-title">Generate Profile</h2>
              <button
                className="modal-close"
                onClick={() => resolveProfileCostModal(false)}
                aria-label="Close"
              >
                Г—
              </button>
            </div>

            <div className="profile-cost-body">
              <div className="profile-cost-value">
                ${Number(profileCostModal.estimatedCostUsd || 0).toFixed(4)}
              </div>
              <div className="profile-cost-label">Will be deducted from your balance</div>
              <div className="profile-cost-meta">
                <div>
                  Wallet: {profileCostModal.wallet.slice(0, 8)}...{profileCostModal.wallet.slice(-6)}
                </div>
                <div>Model: {profileCostModal.model}</div>
              </div>
            </div>

            <div className="profile-cost-actions">
              <button
                className="btn-cost-cancel"
                onClick={() => resolveProfileCostModal(false)}
              >
                Cancel
              </button>
              <button
                className="btn-cost-start profile-cost-confirm"
                onClick={() => resolveProfileCostModal(true)}
              >
                Generate Profile
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}

export default App


