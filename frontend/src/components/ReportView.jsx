import { useState, useMemo, memo, useCallback, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import './ReportView.css'

function parseReport(markdown) {
  if (!markdown) return { summary: '', sections: [] }

  const lines = markdown.split('\n')
  let summary = ''
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
    } else if (pastTitle) {
      summary += line + '\n'
    }
  }

  if (currentSection) sections.push(currentSection)

  sections.forEach((s, i) => {
    const m = s.date.match(/\d{4}-\d{2}-\d{2}/)
    s._sortDate = m ? m[0] : ''
    s._originalIndex = i
  })
  sections.sort((a, b) => b._sortDate.localeCompare(a._sortDate))

  return { summary: summary.trim(), sections }
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

const DayCard = memo(function DayCard({ date, content, walletAddress, isNew }) {
  const [expanded, setExpanded] = useState(false)
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

  const { mainContent, daySummary } = useMemo(() => {
    const lines = content.trim().split('\n')
    const summaryIdx = lines.findIndex(l =>
      l.match(/\*\*Суть (дня|периода):\*\*/)
    )

    if (summaryIdx >= 0) {
      return {
        daySummary: lines[summaryIdx],
        mainContent: [...lines.slice(0, summaryIdx), ...lines.slice(summaryIdx + 1)].join('\n').trim(),
      }
    }
    return { mainContent: content.trim(), daySummary: null }
  }, [content])

  const dateMatches = useMemo(() => date.match(/\d{4}-\d{2}-\d{2}/g) || [], [date])

  const renderedBody = useMemo(() => (
    <div className="day-card-body">
      <ReactMarkdown>{mainContent}</ReactMarkdown>
      {daySummary && (
        <div className="day-summary">
          <ReactMarkdown>{daySummary}</ReactMarkdown>
        </div>
      )}
    </div>
  ), [mainContent, daySummary])

  const handleToggle = useCallback(() => {
    if (!expanded && !txs && walletAddress && dateMatches.length > 0) {
      setTxLoading(true)
      const dateFrom = dateMatches[0]
      const dateTo = dateMatches.length > 1 ? dateMatches[1] : dateFrom
      fetch(`/api/transactions/${walletAddress}?date_from=${dateFrom}&date_to=${dateTo}`)
        .then(res => res.ok ? res.json() : null)
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
    if (expanded) {
      setTxVisible(TX_PAGE_SIZE)
    }
    setExpanded(v => !v)
  }, [expanded, txs, walletAddress, dateMatches])

  const remainingTxs = txs ? txs.length - txVisible : 0

  return (
    <div className="day-card" ref={cardRef}>
      <div className="day-card-header">
        <span>
          {date}
          {isNew && <span className="new-badge" title="New data">✨ NEW</span>}
        </span>
        {walletAddress && dateMatches.length > 0 && (
          <button
            className={`tx-toggle-btn ${expanded ? 'tx-toggle-expanded' : ''}`}
            onClick={handleToggle}
          >
            {expanded ? 'Hide' : 'Transactions'}
          </button>
        )}
      </div>
      {expanded && (
        <div className="tx-list">
          {txLoading && (
            <div className="tx-loading">Loading...</div>
          )}
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
      {wasVisible ? renderedBody : <div className="day-card-placeholder" />}
    </div>
  )
})

function ReportView({ report, loading, walletTag, walletAddress, oldSectionCount }) {
  const { summary, sections } = useMemo(
    () => parseReport(report?.markdown),
    [report?.markdown]
  )

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

      {summary && (
        <div className="report-summary">
          <ReactMarkdown>{summary}</ReactMarkdown>
        </div>
      )}

      <div className="report-sections">
        {sections.map((section, i) => (
          <DayCard
            key={i}
            date={section.date}
            content={section.content}
            walletAddress={walletAddress}
            isNew={oldSectionCount !== null && section._originalIndex >= oldSectionCount}
          />
        ))}
      </div>

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
