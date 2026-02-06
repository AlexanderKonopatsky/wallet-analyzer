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
    // Skip the main title
    if (line.startsWith('# ') && !pastTitle) {
      pastTitle = true
      continue
    }

    // Day header
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

  // Sort by date descending (newest first)
  // Handles dates like "2024-10-13" and ranges like "2024-10-12 — 2024-12-11"
  sections.sort((a, b) => {
    const dateA = a.date.match(/\d{4}-\d{2}-\d{2}/)
    const dateB = b.date.match(/\d{4}-\d{2}-\d{2}/)
    if (!dateA || !dateB) return 0
    return dateB[0].localeCompare(dateA[0])
  })

  return { summary: summary.trim(), sections }
}

function DayCard({ date, content }) {
  // Split content to highlight "Суть дня/периода" line
  const lines = content.trim().split('\n')
  const summaryIdx = lines.findIndex(l =>
    l.match(/\*\*Суть (дня|периода):\*\*/)
  )

  let mainContent, daySummary
  if (summaryIdx >= 0) {
    daySummary = lines[summaryIdx]
    mainContent = [...lines.slice(0, summaryIdx), ...lines.slice(summaryIdx + 1)].join('\n').trim()
  } else {
    mainContent = content.trim()
    daySummary = null
  }

  return (
    <div className="day-card">
      <div className="day-card-header">{date}</div>
      <div className="day-card-body">
        <ReactMarkdown>{mainContent}</ReactMarkdown>
        {daySummary && (
          <div className="day-summary">
            <ReactMarkdown>{daySummary}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

function ReportView({ report, loading, walletTag }) {
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

  const { summary, sections } = parseReport(report.markdown)

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
          <DayCard key={i} date={section.date} content={section.content} />
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

export default ReportView
