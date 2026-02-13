import { useState, useEffect, useCallback, useRef } from 'react'
import './App.css'
import WalletSidebar from './components/WalletSidebar'
import ReportView from './components/ReportView'
import ProfileView from './components/ProfileView'
import PaymentWidget from './components/PaymentWidget'
import LoginPage from './components/LoginPage'
import AdminBackupView from './components/AdminBackupView'
import ActiveTasksPanel from './components/ActiveTasksPanel'
import { InsufficientBalanceModal, ProfileCostModal } from './components/CostModals'
import { apiCall, setAuthToken, getUser, logout } from './utils/api'
import {
  hasWalletNewData,
  initializeWalletViewedState,
  processViewedReport
} from './utils/walletViewState'

const RUNNING_TASK_STATUSES = new Set(['fetching', 'analyzing'])
const REPORT_DAYS_PAGE_SIZE = 30

function buildReportEndpoint(wallet) {
  const walletLower = wallet.toLowerCase()
  return `/api/report/${walletLower}?days_limit=${REPORT_DAYS_PAGE_SIZE}&days_offset=0`
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
  const [canManageDataBackup, setCanManageDataBackup] = useState(null)
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
    if (!user) {
      setCanManageDataBackup(null)
      return
    }

    if (typeof user.can_manage_data_backup === 'boolean') {
      setCanManageDataBackup(user.can_manage_data_backup)
    } else {
      setCanManageDataBackup(null)
    }
  }, [user])

  useEffect(() => {
    if (!user || canManageDataBackup !== null) return

    let cancelled = false

    const detectBackupAccess = async () => {
      try {
        const res = await apiCall('/api/admin/data-backups')
        if (!res || cancelled) return

        if (res.status === 403) {
          setCanManageDataBackup(false)
          setBackupAccessDenied(true)
          setBackups([])
          return
        }

        setCanManageDataBackup(true)
        if (res.ok) {
          const payload = await res.json().catch(() => ({}))
          if (!cancelled) {
            setBackupAccessDenied(false)
            setBackups(Array.isArray(payload.backups) ? payload.backups : [])
          }
        }
      } catch {
        if (!cancelled) {
          setCanManageDataBackup(true)
        }
      }
    }

    detectBackupAccess()

    return () => {
      cancelled = true
    }
  }, [user, canManageDataBackup])

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

  // Process report data: determine NEW sections, update localStorage, remove green dot
  const processReportData = useCallback((wallet, data) => {
    const { oldSectionCount: previousSectionCount, updatedSectionIndices: nextUpdatedSections } =
      processViewedReport(wallet, data)
    setOldSectionCount(previousSectionCount)
    setUpdatedSectionIndices(nextUpdatedSections)

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
        walletsData.forEach(initializeWalletViewedState)

        // Enrich wallets with has_new_data flag
        const enrichedWallets = walletsData.map(w => ({
          ...w,
          has_new_data: hasWalletNewData(w)
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
        has_new_data: hasWalletNewData(w)
      }))
      setWallets(enrichedWallets)
    } catch (err) {
      console.error('Failed to refresh wallets:', err)
    }
  }, [])

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
      const res = await apiCall(buildReportEndpoint(wallet))
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
          setCanManageDataBackup(false)
          setBackupAccessDenied(true)
          setBackups([])
          return
        }
        setCanManageDataBackup(true)
        throw new Error(payload.detail || 'Failed to load backup history')
      }

      const payload = await res.json().catch(() => ({}))
      setCanManageDataBackup(true)
      setBackupAccessDenied(false)
      setBackups(Array.isArray(payload.backups) ? payload.backups : [])
    } catch (err) {
      setError(err.message || 'Failed to load backup history')
    } finally {
      setBackupsLoading(false)
    }
  }, [])

  const openBackupAdminView = useCallback(async () => {
    if (canManageDataBackup !== true) return
    setActiveView('admin-backup')
    await loadBackupHistory()
  }, [loadBackupHistory, canManageDataBackup])

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
      const walletLower = wallet.toLowerCase()
      const isWalletVisible = wallets.some(w => w.address.toLowerCase() === walletLower)

      // Wallet may be in hidden list after "delete"; unhide it on explicit re-add.
      if (!isWalletVisible) {
        try {
          const unhideRes = await apiCall(`/api/wallets/${walletLower}/unhide`, { method: 'POST' })
          if (unhideRes?.ok) {
            await refreshWallets()
          }
        } catch (err) {
          console.error('Failed to unhide wallet:', err)
        }
      }

      // Try to load existing report
      setLoading(true)
      try {
        const res = await apiCall(buildReportEndpoint(wallet))
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
  }, [estimateCost, isMobileLayout, processReportData, refreshWallets, wallets])

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
  const hasBackupAccess = canManageDataBackup === true
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
          {hasBackupAccess && (
            <button
              onClick={openBackupAdminView}
              className={`btn-admin ${activeView === 'admin-backup' ? 'btn-admin-active' : ''}`}
              disabled={backupBusy || importBusy}
              title="Open backup management"
            >
              Backups
            </button>
          )}
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

          {hasActiveTasks && (
            <ActiveTasksPanel
              taskEntries={taskEntries}
              getWalletLabel={getWalletLabel}
              onStartAnalysis={startAnalysis}
              onCancelAnalysis={cancelAnalysis}
            />
          )}

          {error && <div className="error-banner">{error}</div>}

          {activeView === 'payment' ? (
            <PaymentWidget onPaymentSuccess={refreshBalance} />
          ) : activeView === 'admin-backup' && hasBackupAccess ? (
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

      <InsufficientBalanceModal
        modal={insufficientBalanceModal}
        onClose={closeInsufficientBalanceModal}
        onDeposit={openDepositFromInsufficientModal}
      />
      <ProfileCostModal
        modal={profileCostModal}
        onResolve={resolveProfileCostModal}
      />

    </div>
  )
}

export default App
