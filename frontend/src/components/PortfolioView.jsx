import { useState, Fragment } from 'react'
import './PortfolioView.css'

const GRADE_COLORS = {
  A: '#3fb950',
  B: '#58a6ff',
  C: '#d29922',
  D: '#f97316',
  F: '#f85149',
}

function fmtUsd(val) {
  if (val == null) return '$0'
  const abs = Math.abs(val)
  if (abs >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `$${(val / 1_000).toFixed(2)}K`
  return `$${val.toFixed(2)}`
}

function fmtPnl(val) {
  if (val == null) return '$0'
  const prefix = val >= 0 ? '+' : ''
  return prefix + fmtUsd(val)
}

function fmtPct(val) {
  if (val == null) return '0%'
  const prefix = val >= 0 ? '+' : ''
  if (Math.abs(val) >= 10000) return `${prefix}${(val / 1000).toFixed(0)}K%`
  return `${prefix}${val.toFixed(1)}%`
}

function fmtHoldTime(seconds) {
  if (!seconds || seconds <= 0) return '-'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  if (days > 0) return `${days}d ${hours}h`
  const mins = Math.floor((seconds % 3600) / 60)
  return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`
}

function SummaryCard({ label, value, sub, color }) {
  return (
    <div className="pf-card">
      <div className="pf-card-label">{label}</div>
      <div className="pf-card-value" style={color ? { color } : undefined}>
        {value}
      </div>
      {sub && <div className="pf-card-sub">{sub}</div>}
    </div>
  )
}

function TokenTable({ tokens }) {
  const [sortKey, setSortKey] = useState('realized_pnl_usd')
  const [sortAsc, setSortAsc] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [expandedIdx, setExpandedIdx] = useState(null)

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const sorted = [...tokens].sort((a, b) => {
    const av = a[sortKey] ?? 0
    const bv = b[sortKey] ?? 0
    return sortAsc ? av - bv : bv - av
  })

  const displayed = showAll ? sorted : sorted.slice(0, 20)
  const arrow = (key) => sortKey === key ? (sortAsc ? ' \u25b2' : ' \u25bc') : ''

  return (
    <div className="pf-section">
      <h3>Token Performance</h3>
      <div className="pf-table-wrap">
        <table className="pf-table">
          <thead>
            <tr>
              <th></th>
              <th>Token</th>
              <th>Chain</th>
              <th className="pf-th-sort" onClick={() => handleSort('total_bought_usd')}>
                Bought{arrow('total_bought_usd')}
              </th>
              <th className="pf-th-sort" onClick={() => handleSort('total_sold_usd')}>
                Sold{arrow('total_sold_usd')}
              </th>
              <th className="pf-th-sort" onClick={() => handleSort('realized_pnl_usd')}>
                P&L{arrow('realized_pnl_usd')}
              </th>
              <th className="pf-th-sort" onClick={() => handleSort('roi_pct')}>
                ROI{arrow('roi_pct')}
              </th>
              <th>Trades</th>
              <th>Holding</th>
            </tr>
          </thead>
          <tbody>
            {displayed.map((t, i) => {
              const isExpanded = expandedIdx === i
              const hasTrades = t.trades?.length > 0
              return (
                <Fragment key={`${t.chain}-${t.address}-${i}`}>
                  <tr
                    className={hasTrades ? 'pf-expandable-row' : ''}
                    onClick={() => hasTrades && setExpandedIdx(isExpanded ? null : i)}
                  >
                    <td className="pf-chevron-cell">
                      {hasTrades && <span className={`pf-chevron ${isExpanded ? 'pf-chevron-open' : ''}`}>{'\u25b8'}</span>}
                    </td>
                    <td className="pf-token-cell">
                      <span className="pf-token-symbol">{t.symbol}</span>
                    </td>
                    <td className="pf-chain">{t.chain}</td>
                    <td>{fmtUsd(t.total_bought_usd)}</td>
                    <td>{fmtUsd(t.total_sold_usd)}</td>
                    <td className={t.realized_pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                      {fmtPnl(t.realized_pnl_usd)}
                    </td>
                    <td className={t.roi_pct >= 0 ? 'pf-positive' : 'pf-negative'}>
                      {t.roi_pct !== 0 ? fmtPct(t.roi_pct) : '-'}
                    </td>
                    <td>{t.buy_count + t.sell_count}</td>
                    <td className="pf-holding">
                      {t.current_holding > 0 ? t.current_holding.toFixed(4) : '-'}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="pf-detail-expand-row">
                      <td colSpan={9} className="pf-detail-expand-cell">
                        <table className="pf-detail-table">
                          <thead>
                            <tr>
                              <th>Date</th>
                              <th>Cost</th>
                              <th>Proceeds</th>
                              <th>P&L</th>
                              <th>ROI</th>
                              <th>Hold</th>
                              <th>DEX</th>
                            </tr>
                          </thead>
                          <tbody>
                            {t.trades.map((tr, j) => (
                              <tr key={j}>
                                <td>{tr.sell_date || tr.buy_date || '-'}</td>
                                <td>{fmtUsd(tr.cost_basis_usd)}</td>
                                <td>{fmtUsd(tr.proceeds_usd)}</td>
                                <td className={tr.pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                                  {fmtPnl(tr.pnl_usd)}
                                </td>
                                <td className={tr.roi_pct >= 0 ? 'pf-positive' : 'pf-negative'}>
                                  {tr.roi_pct !== 0 ? fmtPct(tr.roi_pct) : '-'}
                                </td>
                                <td>{fmtHoldTime(tr.hold_seconds)}</td>
                                <td className="pf-chain">{tr.dex || '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {tokens.length > 20 && !showAll && (
        <button className="pf-show-all" onClick={() => setShowAll(true)}>
          Show all {tokens.length} tokens
        </button>
      )}
    </div>
  )
}

function ProtocolTable({ protocols }) {
  const [expandedIdx, setExpandedIdx] = useState(null)

  if (!protocols?.length) return null
  return (
    <div className="pf-section">
      <h3>Protocol Usage</h3>
      <div className="pf-table-wrap">
        <table className="pf-table">
          <thead>
            <tr>
              <th></th>
              <th>Protocol</th>
              <th>Volume</th>
              <th>P&L</th>
              <th>TXs</th>
              <th>Period</th>
            </tr>
          </thead>
          <tbody>
            {protocols.map((p, i) => {
              const isExpanded = expandedIdx === i
              const hasTrades = p.trades?.length > 0
              return (
                <Fragment key={`${p.name}-${i}`}>
                  <tr
                    className={hasTrades ? 'pf-expandable-row' : ''}
                    onClick={() => hasTrades && setExpandedIdx(isExpanded ? null : i)}
                  >
                    <td className="pf-chevron-cell">
                      {hasTrades && <span className={`pf-chevron ${isExpanded ? 'pf-chevron-open' : ''}`}>{'\u25b8'}</span>}
                    </td>
                    <td className="pf-protocol-name">{p.name}</td>
                    <td>{fmtUsd(p.volume_usd)}</td>
                    <td className={p.realized_pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                      {fmtPnl(p.realized_pnl_usd)}
                    </td>
                    <td>{p.tx_count}</td>
                    <td className="pf-chain">
                      {p.first_used}{p.last_used !== p.first_used ? ` \u2014 ${p.last_used}` : ''}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="pf-detail-expand-row">
                      <td colSpan={6} className="pf-detail-expand-cell">
                        <table className="pf-detail-table">
                          <thead>
                            <tr>
                              <th>Token</th>
                              <th>Chain</th>
                              <th>Date</th>
                              <th>Cost</th>
                              <th>Proceeds</th>
                              <th>P&L</th>
                              <th>ROI</th>
                              <th>Hold</th>
                            </tr>
                          </thead>
                          <tbody>
                            {p.trades.map((tr, j) => (
                              <tr key={j}>
                                <td className="pf-token-symbol">{tr.token}</td>
                                <td className="pf-chain">{tr.chain}</td>
                                <td>{tr.sell_date || '-'}</td>
                                <td>{fmtUsd(tr.cost_basis_usd)}</td>
                                <td>{fmtUsd(tr.proceeds_usd)}</td>
                                <td className={tr.pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                                  {fmtPnl(tr.pnl_usd)}
                                </td>
                                <td className={tr.roi_pct >= 0 ? 'pf-positive' : 'pf-negative'}>
                                  {tr.roi_pct !== 0 ? fmtPct(tr.roi_pct) : '-'}
                                </td>
                                <td>{fmtHoldTime(tr.hold_seconds)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function TopTrades({ trades }) {
  if (!trades) return null
  const { best, worst } = trades
  if (!best?.length && !worst?.length) return null

  const TradeList = ({ items, label }) => (
    <div className="pf-trades-col">
      <h4>{label}</h4>
      {items.map((t, i) => (
        <div key={i} className="pf-trade-card">
          <div className="pf-trade-header">
            <span className="pf-token-symbol">{t.token}</span>
            <span className="pf-chain">{t.chain}</span>
            <span className={t.pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
              {fmtPnl(t.pnl_usd)}
            </span>
          </div>
          <div className="pf-trade-details">
            <span>Cost: {fmtUsd(t.cost_basis_usd)}</span>
            <span>Proceeds: {fmtUsd(t.proceeds_usd)}</span>
            {t.roi_pct !== 0 && <span>ROI: {fmtPct(t.roi_pct)}</span>}
            <span>Hold: {fmtHoldTime(t.hold_seconds)}</span>
          </div>
          <div className="pf-trade-meta">
            {t.dex && <span>{t.dex}</span>}
            <span>{t.sell_date}</span>
          </div>
        </div>
      ))}
    </div>
  )

  return (
    <div className="pf-section">
      <h3>Top Trades</h3>
      <div className="pf-trades-grid">
        {best?.length > 0 && <TradeList items={best} label="Best Trades" />}
        {worst?.length > 0 && <TradeList items={worst} label="Worst Trades" />}
      </div>
    </div>
  )
}

function PortfolioView({ portfolio, loading, onRefresh }) {
  if (loading) {
    return (
      <div className="portfolio-view">
        <div className="pf-loading">
          <div className="profile-spinner" />
          <span>Computing portfolio analysis...</span>
        </div>
      </div>
    )
  }

  if (!portfolio) return null

  if (portfolio.error) {
    return (
      <div className="portfolio-view">
        <div className="error-banner">{portfolio.error}</div>
      </div>
    )
  }

  const s = portfolio.summary
  const d = portfolio.defi
  const gradeColor = GRADE_COLORS[s.grade] || '#8b949e'

  return (
    <div className="portfolio-view">
      <div className="pf-header">
        <h2>Portfolio Analysis</h2>
        <div className="pf-header-right">
          <span className="pf-meta">
            {portfolio.tx_count_analyzed} txs analyzed
            {s.first_activity && ` \u00b7 ${s.first_activity} \u2014 ${s.last_activity}`}
          </span>
          <button className="btn btn-refresh" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </div>

      {/* Grade + Summary */}
      <div className="pf-summary-row">
        <div className="pf-grade" style={{ borderColor: gradeColor }}>
          <span className="pf-grade-letter" style={{ color: gradeColor }}>{s.grade}</span>
          <span className="pf-grade-label">Grade</span>
        </div>

        <SummaryCard
          label="Realized P&L"
          value={fmtPnl(s.total_realized_pnl_usd)}
          sub={`${s.winning_trades}W / ${s.losing_trades}L`}
          color={s.total_realized_pnl_usd >= 0 ? '#3fb950' : '#f85149'}
        />
        <SummaryCard
          label="Win Rate"
          value={`${s.win_rate_pct}%`}
          sub={`${s.total_trades} trades`}
          color={s.win_rate_pct >= 50 ? '#3fb950' : s.win_rate_pct >= 40 ? '#d29922' : '#f85149'}
        />
        <SummaryCard
          label="Avg Trade P&L"
          value={fmtPnl(s.avg_trade_pnl_usd)}
          sub={`Avg ROI: ${fmtPct(s.avg_trade_roi_pct)}`}
          color={s.avg_trade_pnl_usd >= 0 ? '#3fb950' : '#f85149'}
        />
        <SummaryCard
          label="Active Days"
          value={s.active_days}
          sub={`${s.avg_txs_per_day} txs/day`}
        />
      </div>

      {/* Detailed metrics */}
      <div className="pf-details-grid">
        <div className="pf-detail-card">
          <h4>Trading</h4>
          <div className="pf-detail-rows">
            <div className="pf-detail-row">
              <span>Total Volume</span>
              <span>{fmtUsd(s.total_volume_usd)}</span>
            </div>
            <div className="pf-detail-row">
              <span>Avg Position Size</span>
              <span>{fmtUsd(s.avg_position_size_usd)}</span>
            </div>
          </div>
        </div>

        <div className="pf-detail-card">
          <h4>Fund Flow</h4>
          <div className="pf-detail-rows">
            <div className="pf-detail-row">
              <span>Total Deposited</span>
              <span>{fmtUsd(s.total_deposited_usd)}</span>
            </div>
            <div className="pf-detail-row">
              <span>Total Withdrawn</span>
              <span>{fmtUsd(s.total_withdrawn_usd)}</span>
            </div>
            <div className="pf-detail-row">
              <span>Net Flow</span>
              <span className={s.net_flow_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                {fmtPnl(s.net_flow_usd)}
              </span>
            </div>
          </div>
        </div>

        <div className="pf-detail-card">
          <h4>DeFi</h4>
          <div className="pf-detail-rows">
            {(d.lp_deposited_usd > 0 || d.lp_withdrawn_usd > 0) && (
              <>
                <div className="pf-detail-row">
                  <span>LP Net P&L</span>
                  <span className={d.lp_net_pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                    {fmtPnl(d.lp_net_pnl_usd)}
                  </span>
                </div>
              </>
            )}
            {(d.lending_supplied_usd > 0 || d.lending_withdrawn_usd > 0) && (
              <div className="pf-detail-row">
                <span>Lending Net</span>
                <span className={d.lending_net_pnl_usd >= 0 ? 'pf-positive' : 'pf-negative'}>
                  {fmtPnl(d.lending_net_pnl_usd)}
                </span>
              </div>
            )}
            {d.lending_borrowed_usd > 0 && (
              <div className="pf-detail-row">
                <span>Borrowed</span>
                <span>{fmtUsd(d.lending_borrowed_usd)}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Warnings */}
      {portfolio.warnings?.length > 0 && (
        <div className="pf-warnings">
          {portfolio.warnings.map((w, i) => (
            <div key={i} className="pf-warning">{w}</div>
          ))}
        </div>
      )}

      {/* Token table */}
      {portfolio.tokens?.length > 0 && <TokenTable tokens={portfolio.tokens} />}

      {/* Protocol table */}
      <ProtocolTable protocols={portfolio.protocols} />

      {/* Top trades */}
      <TopTrades trades={portfolio.top_trades} />
    </div>
  )
}

export default PortfolioView
