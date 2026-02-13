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
  const [txCountDates, setTxCountDates] = useState(null)
  const [sections, setSections] = useState(initialSections)
  const [hasMoreDays, setHasMoreDays] = useState(Boolean(report?.has_more))
  const [loadingMoreDays, setLoadingMoreDays] = useState(false)
  const loadMoreRef = useRef(null)
  const loadingMoreRef = useRef(false)

  useEffect(() => {
    setSections(initialSections)
    setHasMoreDays(Boolean(report?.has_more))
    setLoadingMoreDays(false)
    loadingMoreRef.current = false
  }, [initialSections, report?.has_more, walletAddress])

  useEffect(() => {
    let cancelled = false

    if (!walletAddress) {
      setTxCountDates(null)
      return () => {
        cancelled = true
      }
    }

    apiCall(`/api/tx-counts/${encodeURIComponent(walletAddress)}`)
      .then(res => (res && res.ok ? res.json() : null))
      .then(data => {
        if (cancelled) return
        if (!data || typeof data !== 'object') {
          setTxCountDates(null)
          return
        }

        const dates = Object.entries(data)
          .filter(([day, count]) => /^\d{4}-\d{2}-\d{2}$/.test(day) && Number(count) > 0)
          .map(([day]) => day)
        setTxCountDates(dates)
      })
      .catch(() => {
        if (!cancelled) setTxCountDates(null)
      })

    return () => {
      cancelled = true
    }
  }, [walletAddress])

  const loadMoreDays = useCallback(async () => {
    if (!walletAddress || !hasMoreDays || loadingMoreRef.current) return

    loadingMoreRef.current = true
    setLoadingMoreDays(true)

    try {
      const offset = sections.length
      const endpoint = `/api/report/${encodeURIComponent(walletAddress)}?days_offset=${offset}&days_limit=${REPORT_DAYS_PAGE_SIZE}`
      const res = await apiCall(endpoint)
      if (!res || !res.ok) {
        return
      }

      const data = await res.json()
      const nextSections = normalizeApiSections(data?.sections)

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
          return merged
        })
      }

      setHasMoreDays(Boolean(data?.has_more))
    } catch {
      // Ignore load-more failures and keep already loaded days visible.
    } finally {
      loadingMoreRef.current = false
      setLoadingMoreDays(false)
    }
  }, [hasMoreDays, sections.length, walletAddress])

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
  }, [hasMoreDays, loadMoreDays, sections.length])

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

      {sections.length > 0 && <CalendarStrip sections={sections} activeDates={txCountDates} />}

      <div className="report-sections">
        {sections.map((section, i) => (
          <DayCard
            key={i}
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
    </div>
  )
}

export { TxRow }
export default ReportView
