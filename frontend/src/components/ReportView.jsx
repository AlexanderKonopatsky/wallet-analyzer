import { useState, useMemo, memo, useCallback, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import CalendarStrip from './CalendarStrip'
import './ReportView.css'
import { apiCall } from '../utils/api'

const REPORT_DAYS_PAGE_SIZE = 30

function parseReport(markdown) {
  if (!markdown) return { sections: [] }

  const lines = markdown.split('\n')
  const sections = []
  let currentSection = null
  let pastTitle = false

  for (const line of lines) {
    if (line.startsWith('# ') && !pastTitle) {
      pastTitle = true
      continue
    }

    const dateMatch = line.match(/^### (.+)/)
    if (dateMatch) {
      if (currentSection) sections.push(currentSection)
      currentSection = { date: dateMatch[1], content: '' }
      continue
    }

    if (currentSection) {
      currentSection.content += line + '\n'
    }
  }

  if (currentSection) sections.push(currentSection)

  sections.forEach((s, i) => {
    const m = s.date.match(/\d{4}-\d{2}-\d{2}/)
    s._sortDate = m ? m[0] : ''
    s._originalIndex = i
    // Extract significance level (default 3 for backward compatibility)
    const sigMatch = s.content.match(/\*\*Важность:\s*(\d)\s*\*\*/)
    s._significance = sigMatch ? Math.min(5, Math.max(1, parseInt(sigMatch[1], 10))) : 3
  })
  sections.sort((a, b) => b._sortDate.localeCompare(a._sortDate))

  return { sections }
}

function normalizeApiSections(rawSections) {
  if (!Array.isArray(rawSections)) return []

  const normalized = rawSections.map((section, idx) => {
    const date = typeof section?.date === 'string' ? section.date : ''
    const content = typeof section?.content === 'string' ? section.content : ''
    const sortDate = typeof section?.sort_date === 'string'
      ? section.sort_date
      : (date.match(/\d{4}-\d{2}-\d{2}/)?.[0] || '')
    const originalIndex = Number.isInteger(section?.original_index)
      ? section.original_index
      : idx
    const significance = Number.isFinite(section?.significance)
      ? Math.min(5, Math.max(1, Number(section.significance)))
      : 3

    return {
      date,
      content,
      _sortDate: sortDate,
      _originalIndex: originalIndex,
      _significance: significance,
    }
  })

  normalized.sort((a, b) => b._sortDate.localeCompare(a._sortDate))
  return normalized
}

function getReportSections(report) {
  if (Array.isArray(report?.sections)) {
    return normalizeApiSections(report.sections)
  }
  return parseReport(report?.markdown).sections
}

function getCalendarSections(report, fallbackSections) {
  if (Array.isArray(report?.calendar_sections)) {
    return normalizeApiSections(report.calendar_sections)
  }
  return fallbackSections
}

function extractSectionRange(section) {
  const isoMatches = section?.date?.match(/\d{4}-\d{2}-\d{2}/g) || []
  if (isoMatches.length === 0) return null

  const start = isoMatches[0]
  const end = isoMatches[isoMatches.length - 1]
  return start <= end ? { start, end } : { start: end, end: start }
}

function sectionHasMatchingDate(section, matchingDateSet) {
  if (!matchingDateSet || matchingDateSet.size === 0) return false
  const range = extractSectionRange(section)
  if (!range) return false

  for (const dateIso of matchingDateSet) {
    if (dateIso >= range.start && dateIso <= range.end) {
      return true
    }
  }
  return false
}

function parseUsdAmount(rawValue) {
  if (typeof rawValue === 'number') {
    return Number.isFinite(rawValue) ? rawValue : null
  }
  if (typeof rawValue !== 'string') return null

  const normalized = rawValue.trim().replace(/,/g, '').toUpperCase()
  const match = normalized.match(/^\$?\s*(-?\d+(?:\.\d+)?)\s*([KMB])?$/)
  if (!match) return null

  const amount = Number(match[1])
  if (!Number.isFinite(amount)) return null

  const suffix = match[2] || ''
  if (suffix === 'K') return amount * 1_000
  if (suffix === 'M') return amount * 1_000_000
  if (suffix === 'B') return amount * 1_000_000_000
  return amount
}

function getTxVolumeUsd(tx) {
  const numericVolume = Number(tx?.volume_usd)
  if (Number.isFinite(numericVolume)) return numericVolume

  const parsedFromUsd = parseUsdAmount(tx?.usd)
  if (Number.isFinite(parsedFromUsd)) return parsedFromUsd

  return 0
}

const TX_PAGE_SIZE = 50

const TX_TYPE_LABELS = {
  swap: 'Swap',
  lending: 'Lending',
  transfer: 'Transfer',
  lp: 'LP',
  bridge: 'Bridge',
  wrap: 'Wrap',
  nft_transfer: 'NFT',
}

const TxRow = memo(function TxRow({ tx }) {
  const typeLabel = TX_TYPE_LABELS[tx.tx_type] || tx.tx_type

  return (
    <div className="tx-row">
      <div className="tx-row-main">
        <span className={`tx-type tx-type-${tx.tx_type}`}>{typeLabel}</span>
        <span className="tx-chain">{tx.chain}</span>
        <span className="tx-desc">{tx.description}</span>
        {tx.usd && <span className="tx-usd">{tx.usd}</span>}
        {tx.platform && <span className="tx-platform">{tx.platform}</span>}
      </div>
      <div className="tx-row-meta">
        <span className="tx-time">{tx.time}</span>
        {tx.explorer_url && (
          <a
            className="tx-link"
            href={tx.explorer_url}
            target="_blank"
            rel="noopener noreferrer"
            title={tx.tx_hash}
          >
            {tx.tx_hash.slice(0, 10)}…
          </a>
        )}
      </div>
    </div>
  )
})

const DayCard = memo(function DayCard({ id, date, content, walletAddress, isNew, significance = 3, defaultOpen = false }) {
  const [contentOpen, setContentOpen] = useState(defaultOpen)
  const [txExpanded, setTxExpanded] = useState(false)
  const [txs, setTxs] = useState(null)
  const [txLoading, setTxLoading] = useState(false)
  const [txVisible, setTxVisible] = useState(TX_PAGE_SIZE)
  const [wasVisible, setWasVisible] = useState(false)
  const cardRef = useRef(null)

  useEffect(() => {
    const el = cardRef.current
    if (!el || wasVisible) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setWasVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '200px' }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [wasVisible])

  // Listen for expand-day events from CalendarStrip
  useEffect(() => {
    const handler = (e) => {
      if (e.detail?.targetId === id) {
        setContentOpen(true)
      }
    }
    document.addEventListener('expand-day', handler)
    return () => document.removeEventListener('expand-day', handler)
  }, [id])

  const { mainContent, summaryText } = useMemo(() => {
    const lines = content.trim().split('\n')
    const summaryIdx = lines.findIndex(l =>
      l.match(/\*\*Суть (дня|периода):\*\*/)
    )
    const sigIdx = lines.findIndex(l =>
      l.match(/^\*\*Важность:\s*\d\s*\*\*$/)
    )

    const excludeIndices = new Set()
    if (sigIdx >= 0) excludeIndices.add(sigIdx)

    let summaryText = ''
    if (summaryIdx >= 0) {
      excludeIndices.add(summaryIdx)
      summaryText = lines[summaryIdx]
        .replace(/^\*\*Суть (дня|периода):\*\*\s*/, '')
        .trim()
    }

    return {
      summaryText,
      mainContent: lines.filter((_, i) => !excludeIndices.has(i)).join('\n').trim(),
    }
  }, [content])

  const dateMatches = useMemo(() => date.match(/\d{4}-\d{2}-\d{2}/g) || [], [date])

  const handleTxToggle = useCallback(() => {
    if (!txExpanded && !txs && walletAddress && dateMatches.length > 0) {
      setTxLoading(true)
      const dateFrom = dateMatches[0]
      const dateTo = dateMatches.length > 1 ? dateMatches[1] : dateFrom
      apiCall(`/api/transactions/${walletAddress}?date_from=${dateFrom}&date_to=${dateTo}`)
        .then(res => res && res.ok ? res.json() : null)
        .then(data => {
          if (data) {
            const all = Object.values(data).flat()
            all.sort((a, b) => a.timestamp - b.timestamp)
            setTxs(all)
          } else {
            setTxs([])
          }
        })
        .catch(() => setTxs([]))
        .finally(() => setTxLoading(false))
    }
    if (txExpanded) {
      setTxVisible(TX_PAGE_SIZE)
    }
    setTxExpanded(v => !v)
  }, [txExpanded, txs, walletAddress, dateMatches])

  const remainingTxs = txs ? txs.length - txVisible : 0
  const sigClass = significance <= 2 ? 'sig-low' : significance >= 4 ? 'sig-high' : 'sig-mid'

  return (
    <div
      className={`day-card ${contentOpen ? 'day-card-open' : 'day-card-collapsed'} day-card-${sigClass}`}
      ref={cardRef}
      id={id}
    >
      <div className="day-card-header" onClick={() => setContentOpen(v => !v)}>
        <span className={`day-card-chevron ${contentOpen ? 'day-card-chevron-open' : ''}`}>
          &#9656;
        </span>
        <span className="day-card-date">
          {date}
          {isNew && <span className="new-badge" title="New data">NEW</span>}
        </span>
        {!contentOpen && summaryText && (
          <span className="day-card-summary-preview" title={summaryText}>
            {summaryText}
          </span>
        )}
        {contentOpen && walletAddress && dateMatches.length > 0 && (
          <button
            className={`tx-toggle-btn ${txExpanded ? 'tx-toggle-expanded' : ''}`}
            onClick={(e) => { e.stopPropagation(); handleTxToggle(); }}
          >
            {txExpanded ? 'Hide' : 'Transactions'}
          </button>
        )}
      </div>

      <div className={`day-card-collapsible ${contentOpen ? 'day-card-collapsible-open' : ''}`}>
        <div className="day-card-collapsible-inner">
          {txExpanded && (
            <div className="tx-list">
              {txLoading && <div className="tx-loading">Loading...</div>}
              {txs && txs.slice(0, txVisible).map((tx, i) => (
                <TxRow key={tx.tx_hash || i} tx={tx} />
              ))}
              {remainingTxs > 0 && (
                <button
                  className="tx-show-more"
                  onClick={() => setTxVisible(v => v + TX_PAGE_SIZE)}
                >
                  Show {Math.min(remainingTxs, TX_PAGE_SIZE)} more of {remainingTxs}
                </button>
              )}
            </div>
          )}

          {wasVisible && (
            <div className="day-card-body">
              <ReactMarkdown>{mainContent}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  )
})

function ReportView({ report, loading, walletTag, walletAddress, oldSectionCount, updatedSectionIndices = new Set() }) {
  const initialSections = useMemo(() => getReportSections(report), [report])
  const calendarSections = useMemo(
    () => getCalendarSections(report, initialSections),
    [report, initialSections]
  )
  const [activityByDay, setActivityByDay] = useState(null)
  const [activityLoading, setActivityLoading] = useState(false)
  const [activityLoaded, setActivityLoaded] = useState(false)
  const [isFiltersOpen, setIsFiltersOpen] = useState(false)
  const [walletChains, setWalletChains] = useState([])
  const [selectedChains, setSelectedChains] = useState([])
  const [volumeThresholdInput, setVolumeThresholdInput] = useState('')
  const [sections, setSections] = useState(initialSections)
  const [hasMoreDays, setHasMoreDays] = useState(Boolean(report?.has_more))
  const [loadingMoreDays, setLoadingMoreDays] = useState(false)
  const loadMoreRef = useRef(null)
  const loadingMoreRef = useRef(false)
  const sectionsRef = useRef(initialSections)
  const hasMoreDaysRef = useRef(Boolean(report?.has_more))

  const dayActivityMap = useMemo(() => {
    if (!activityByDay || typeof activityByDay !== 'object') return new Map()

    const byDay = activityByDay?.by_day && typeof activityByDay.by_day === 'object'
      ? activityByDay.by_day
      : activityByDay

    const map = new Map()
    Object.entries(byDay).forEach(([day, dayData]) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return

      const chainsInDay = new Set()
      let maxTxUsdInDay = 0

      if (Array.isArray(dayData)) {
        // Backward compatibility: legacy shape from /api/transactions/{wallet}.
        dayData.forEach((tx) => {
          const chainValues = [tx?.chain, tx?.from_chain, tx?.to_chain]
          chainValues.forEach((chainValue) => {
            const chain = typeof chainValue === 'string' ? chainValue.trim().toLowerCase() : ''
            if (chain && chain !== '?') {
              chainsInDay.add(chain)
            }
          })

          const volumeUsd = getTxVolumeUsd(tx)
          if (volumeUsd > maxTxUsdInDay) {
            maxTxUsdInDay = volumeUsd
          }
        })
      } else if (dayData && typeof dayData === 'object') {
        const rawChains = Array.isArray(dayData.chains) ? dayData.chains : []
        rawChains.forEach((chainValue) => {
          const chain = typeof chainValue === 'string' ? chainValue.trim().toLowerCase() : ''
          if (chain && chain !== '?') {
            chainsInDay.add(chain)
          }
        })

        const rawMaxUsd = Number(dayData.max_volume_usd)
        maxTxUsdInDay = Number.isFinite(rawMaxUsd) && rawMaxUsd > 0 ? rawMaxUsd : 0
      }

      map.set(day, { chainsInDay, maxTxUsdInDay })
    })

    return map
  }, [activityByDay])

  const availableChains = useMemo(() => {
    if (Array.isArray(walletChains) && walletChains.length > 0) {
      const normalized = [...new Set(
        walletChains
          .map((chain) => (typeof chain === 'string' ? chain.trim().toLowerCase() : ''))
          .filter(Boolean)
      )]

      return normalized
        .sort((left, right) => left.localeCompare(right))
        .map((value) => ({ value, label: value }))
    }

    const normalized = new Set()
    dayActivityMap.forEach(({ chainsInDay }) => {
      chainsInDay.forEach((chain) => {
        const key = typeof chain === 'string' ? chain.trim().toLowerCase() : ''
        if (key) {
          normalized.add(key)
        }
      })
    })

    return [...normalized]
      .sort((left, right) => left.localeCompare(right))
      .map((value) => ({ value, label: value }))
  }, [dayActivityMap, walletChains])

  const volumeThreshold = Number(volumeThresholdInput)
  const isChainFilterActive = selectedChains.length > 0
  const isVolumeFilterActive =
    volumeThresholdInput.trim() !== '' &&
    Number.isFinite(volumeThreshold) &&
    volumeThreshold > 0
  const isAnyDayFilterActive = isChainFilterActive || isVolumeFilterActive

  const matchingDates = useMemo(() => {
    if (dayActivityMap.size === 0) return []

    const selectedChainSet = new Set(selectedChains)
    const dates = []

    dayActivityMap.forEach((stats, dateIso) => {
      const chainMatch =
        !isChainFilterActive ||
        [...stats.chainsInDay].some((chain) => {
          const key = typeof chain === 'string' ? chain.trim().toLowerCase() : ''
          return key && selectedChainSet.has(key)
        })
      const volumeMatch =
        !isVolumeFilterActive ||
        stats.maxTxUsdInDay >= volumeThreshold

      if (chainMatch && volumeMatch) {
        dates.push(dateIso)
      }
    })

    dates.sort()
    return dates
  }, [
    dayActivityMap,
    isChainFilterActive,
    isVolumeFilterActive,
    selectedChains,
    volumeThreshold,
  ])

  const matchingDateSet = useMemo(() => new Set(matchingDates), [matchingDates])

  const calendarActiveDates = useMemo(() => {
    if (dayActivityMap.size === 0) return null
    if (!isAnyDayFilterActive) return [...dayActivityMap.keys()].sort()
    return matchingDates
  }, [dayActivityMap, isAnyDayFilterActive, matchingDates])

  const calendarSourceSections = useMemo(() => {
    const byDate = new Map()
    ;[...calendarSections, ...sections].forEach((section) => {
      const sortDate = section?._sortDate
      if (!sortDate) return

      const existing = byDate.get(sortDate)
      if (!existing || (section?._significance || 0) > (existing?._significance || 0)) {
        byDate.set(sortDate, section)
      }
    })

    const merged = [...byDate.values()]
    merged.sort((a, b) => b._sortDate.localeCompare(a._sortDate))
    return merged
  }, [calendarSections, sections])

  useEffect(() => {
    setSections(initialSections)
    setHasMoreDays(Boolean(report?.has_more))
    setLoadingMoreDays(false)
    loadingMoreRef.current = false
    sectionsRef.current = initialSections
    hasMoreDaysRef.current = Boolean(report?.has_more)
  }, [initialSections, report?.has_more, walletAddress])

  useEffect(() => {
    sectionsRef.current = sections
  }, [sections])

  useEffect(() => {
    hasMoreDaysRef.current = hasMoreDays
  }, [hasMoreDays])

  useEffect(() => {
    setSelectedChains([])
    setVolumeThresholdInput('')
    setIsFiltersOpen(false)
    setWalletChains([])
    setActivityByDay(null)
    setActivityLoading(false)
    setActivityLoaded(false)
  }, [walletAddress])

  useEffect(() => {
    let cancelled = false
    const chainsController = new AbortController()
    const activityController = new AbortController()

    if (!walletAddress || !isFiltersOpen || activityLoaded || activityLoading) {
      return () => {
        cancelled = true
        chainsController.abort()
        activityController.abort()
      }
    }

    setActivityLoading(true)
    setActivityLoaded(false)

    const loadFiltersData = async () => {
      try {
        const [chainsRes, activityRes] = await Promise.all([
          apiCall(`/api/wallet-chains/${encodeURIComponent(walletAddress)}`, {
            signal: chainsController.signal,
          }),
          apiCall(`/api/day-activity/${encodeURIComponent(walletAddress)}`, {
            signal: activityController.signal,
          }),
        ])

        if (cancelled) return

        const chainsData = chainsRes && chainsRes.ok ? await chainsRes.json() : null
        const activityData = activityRes && activityRes.ok ? await activityRes.json() : null
        const chains = Array.isArray(chainsData?.chains) ? chainsData.chains : []
        setWalletChains(chains)
        setActivityByDay(activityData && typeof activityData === 'object' ? activityData : {})
      } catch (err) {
        if (cancelled || err?.name === 'AbortError') return
        setWalletChains([])
        setActivityByDay({})
      } finally {
        if (!cancelled) {
          setActivityLoading(false)
          setActivityLoaded(true)
        }
      }
    }

    loadFiltersData()

    return () => {
      cancelled = true
      chainsController.abort()
      activityController.abort()
    }
  }, [walletAddress, isFiltersOpen, activityLoaded, activityLoading])

  useEffect(() => {
    if (availableChains.length === 0) {
      setSelectedChains([])
      return
    }
    const availableSet = new Set(availableChains.map((item) => item.value))
    setSelectedChains(prev => prev.filter(chain => availableSet.has(chain)))
  }, [availableChains])

  const loadMoreDays = useCallback(async () => {
    if (!walletAddress || !hasMoreDaysRef.current || loadingMoreRef.current) return false

    loadingMoreRef.current = true
    setLoadingMoreDays(true)

    try {
      const offset = sectionsRef.current.length
      const endpoint = `/api/report/${encodeURIComponent(walletAddress)}?days_offset=${offset}&days_limit=${REPORT_DAYS_PAGE_SIZE}`
      const res = await apiCall(endpoint)
      if (!res || !res.ok) {
        return false
      }

      const data = await res.json()
      const nextSections = normalizeApiSections(data?.sections)
      const nextHasMore = Boolean(data?.has_more)

      if (nextSections.length > 0) {
        setSections(prev => {
          const seen = new Set(prev.map(item => item._originalIndex))
          const merged = [...prev]
          nextSections.forEach((item) => {
            if (!seen.has(item._originalIndex)) {
              merged.push(item)
            }
          })
          merged.sort((a, b) => b._sortDate.localeCompare(a._sortDate))
          sectionsRef.current = merged
          return merged
        })
      }

      setHasMoreDays(nextHasMore)
      hasMoreDaysRef.current = nextHasMore
      return nextSections.length > 0
    } catch {
      // Ignore load-more failures and keep already loaded days visible.
      return false
    } finally {
      loadingMoreRef.current = false
      setLoadingMoreDays(false)
    }
  }, [walletAddress])

  useEffect(() => {
    const target = loadMoreRef.current
    if (!target || !hasMoreDays) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          loadMoreDays()
        }
      },
      { rootMargin: '300px' }
    )

    observer.observe(target)
    return () => observer.disconnect()
  }, [hasMoreDays, loadMoreDays])

  useEffect(() => {
    if (!walletAddress) return

    const handleExpandDay = async (e) => {
      const targetId = e.detail?.targetId
      if (!targetId || !targetId.startsWith('day-')) return
      if (document.getElementById(targetId)) return

      let guard = 0
      while (!document.getElementById(targetId) && hasMoreDaysRef.current && guard < 50) {
        guard += 1
        const loaded = await loadMoreDays()
        if (!loaded) break
      }

      const targetEl = document.getElementById(targetId)
      if (targetEl) {
        document.dispatchEvent(new CustomEvent('expand-day', { detail: { targetId } }))
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }

    document.addEventListener('expand-day', handleExpandDay)
    return () => document.removeEventListener('expand-day', handleExpandDay)
  }, [loadMoreDays, walletAddress])

  const shouldApplyDayFilters = isAnyDayFilterActive && activityLoaded && !activityLoading

  useEffect(() => {
    if (!walletAddress || !shouldApplyDayFilters || !hasMoreDaysRef.current) return

    let cancelled = false

    const preloadAllSections = async () => {
      let guard = 0
      while (!cancelled && hasMoreDaysRef.current && guard < 200) {
        guard += 1
        const loaded = await loadMoreDays()
        if (!loaded) break
      }
    }

    preloadAllSections()
    return () => {
      cancelled = true
    }
  }, [loadMoreDays, shouldApplyDayFilters, walletAddress])

  const filteredCalendarSections = useMemo(() => {
    if (!shouldApplyDayFilters) return calendarSourceSections
    return calendarSourceSections.filter(section => sectionHasMatchingDate(section, matchingDateSet))
  }, [calendarSourceSections, shouldApplyDayFilters, matchingDateSet])

  const visibleSections = useMemo(() => {
    if (!shouldApplyDayFilters) return sections
    return sections.filter(section => sectionHasMatchingDate(section, matchingDateSet))
  }, [sections, shouldApplyDayFilters, matchingDateSet])

  const toggleChainSelection = useCallback((chain) => {
    setSelectedChains((prev) => {
      if (prev.includes(chain)) {
        return prev.filter((item) => item !== chain)
      }
      return [...prev, chain]
    })
  }, [])

  if (loading) {
    return (
      <div className="report-loading">
        <div className="spinner" />
        <span>Loading report...</span>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="report-empty">
        Select a wallet or enter an address to view its analysis report.
      </div>
    )
  }

  const formatDate = (iso) => {
    if (!iso) return 'N/A'
    const d = new Date(iso)
    return d.toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    })
  }

  return (
    <div className="report-view">
      <div className="report-meta">
        {walletTag && <span className="meta-tag">{walletTag}</span>}
        <span className="meta-address" title={report.address}>
          {report.address}
        </span>
        <span className="meta-item">
          Updated: {formatDate(report.last_updated)}
        </span>
        <span className="meta-item">
          {report.tx_count} transactions
        </span>
      </div>

      {filteredCalendarSections.length > 0 && (
        <CalendarStrip sections={filteredCalendarSections} activeDates={calendarActiveDates} />
      )}

      <div className="day-filters-toggle-row">
        <button
          type="button"
          className={`day-filters-toggle ${isFiltersOpen ? 'day-filters-toggle-open' : ''}`}
          onClick={() => setIsFiltersOpen((prev) => !prev)}
        >
          {isFiltersOpen ? 'Hide filters' : 'Show filters'}
        </button>
        {isFiltersOpen && activityLoading && (
          <span className="day-filters-loading">Loading filter data...</span>
        )}
      </div>

      {isFiltersOpen && (
        <div className="day-filters">
          <div className="day-filters-row">
            <div className="day-filter-chains">
              <button
                type="button"
                className={`day-chain-chip ${selectedChains.length === 0 ? 'day-chain-chip-active' : ''}`}
                onClick={() => setSelectedChains([])}
              >
                all
              </button>
              {availableChains.map((chain) => (
                <button
                  key={chain.value}
                  type="button"
                  className={`day-chain-chip ${selectedChains.includes(chain.value) ? 'day-chain-chip-active' : ''}`}
                  onClick={() => toggleChainSelection(chain.value)}
                >
                  {chain.label}
                </button>
              ))}
            </div>

            <div className="day-filters-controls">
              <input
                className="day-filter-volume-input"
                type="number"
                min="0"
                step="0.01"
                placeholder="min USD"
                value={volumeThresholdInput}
                onChange={(event) => setVolumeThresholdInput(event.target.value)}
              />
              {shouldApplyDayFilters && (
                <span className="day-filters-count">{matchingDates.length}</span>
              )}
              <button
                type="button"
                className="day-filter-clear"
                onClick={() => {
                  setSelectedChains([])
                  setVolumeThresholdInput('')
                }}
                disabled={!isAnyDayFilterActive}
              >
                clear
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="report-sections">
        {visibleSections.map((section, i) => (
          <DayCard
            key={`${section._sortDate}-${section._originalIndex ?? i}`}
            id={`day-${section._sortDate}`}
            date={section.date}
            content={section.content}
            walletAddress={walletAddress}
            isNew={
              (oldSectionCount !== null && section._originalIndex >= oldSectionCount) ||
              updatedSectionIndices.has(section._originalIndex)
            }
            significance={section._significance}
            defaultOpen={false}
          />
        ))}
      </div>

      {hasMoreDays && (
        <div className="report-load-more-anchor" ref={loadMoreRef}>
          <span className="report-load-more-text">
            {loadingMoreDays ? 'Loading more days...' : 'Scroll to load more days'}
          </span>
        </div>
      )}

      {sections.length === 0 && (
        <div className="report-empty">
          Report exists but has no day sections yet.
        </div>
      )}

      {sections.length > 0 && shouldApplyDayFilters && visibleSections.length === 0 && (
        <div className="report-empty">
          No activity days match current filters.
        </div>
      )}
    </div>
  )
}

export { TxRow }
export default ReportView
