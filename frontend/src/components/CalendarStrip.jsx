import { useMemo, useRef, useEffect, useCallback, Fragment } from 'react'
import './CalendarStrip.css'

const DOW_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

function extractDatesFromSection(section) {
  const isoMatches = section.date.match(/\d{4}-\d{2}-\d{2}/g) || []
  if (isoMatches.length === 0) return []
  if (isoMatches.length === 1) return [isoMatches[0]]

  // Date range: expand all days between first and last
  const start = new Date(isoMatches[0] + 'T00:00:00')
  const end = new Date(isoMatches[isoMatches.length - 1] + 'T00:00:00')
  const dates = []
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    dates.push(d.toISOString().slice(0, 10))
  }
  return dates
}

function CalendarStrip({ sections }) {
  const stripRef = useRef(null)
  const todayIso = new Date().toISOString().slice(0, 10)

  const { monthGrids, gaps } = useMemo(() => {
    if (!sections || sections.length === 0) return { monthGrids: [], gaps: [] }

    // Build dateIso → { targetId, significance } map
    const dateToInfo = new Map()
    sections.forEach(section => {
      const dates = extractDatesFromSection(section)
      const targetId = `day-${section._sortDate}`
      const significance = section._significance || 3
      dates.forEach(d => {
        if (!dateToInfo.has(d)) {
          dateToInfo.set(d, { targetId, significance })
        }
      })
    })

    if (dateToInfo.size === 0) return { monthGrids: [], gaps: [] }

    // Collect unique months
    const monthKeys = new Set()
    dateToInfo.forEach((_, dateStr) => {
      monthKeys.add(dateStr.slice(0, 7))
    })
    const sortedMonths = [...monthKeys].sort()

    // Build grid for each month
    const grids = sortedMonths.map(monthKey => {
      const [year, month] = monthKey.split('-').map(Number)
      const firstDay = new Date(year, month - 1, 1)
      const daysInMonth = new Date(year, month, 0).getDate()

      // Monday=0, Sunday=6
      let startDow = firstDay.getDay()
      startDow = startDow === 0 ? 6 : startDow - 1

      const cells = []
      for (let i = 0; i < startDow; i++) cells.push(null)
      for (let day = 1; day <= daysInMonth; day++) {
        const iso = `${monthKey}-${String(day).padStart(2, '0')}`
        const info = dateToInfo.get(iso)
        cells.push({
          day,
          iso,
          active: !!info,
          targetId: info?.targetId || null,
          significance: info?.significance || 0,
          isToday: iso === todayIso,
        })
      }

      return {
        key: monthKey,
        label: firstDay.toLocaleDateString('en-US', { month: 'short', year: 'numeric' }),
        cells,
      }
    })

    // Detect gaps between consecutive months
    const gapIndices = []
    for (let i = 0; i < sortedMonths.length - 1; i++) {
      const [y1, m1] = sortedMonths[i].split('-').map(Number)
      const [y2, m2] = sortedMonths[i + 1].split('-').map(Number)
      if ((y2 * 12 + m2) - (y1 * 12 + m1) > 1) {
        gapIndices.push(i)
      }
    }

    return { monthGrids: grids, gaps: gapIndices }
  }, [sections, todayIso])

  // Auto-scroll to newest month (rightmost) on load
  useEffect(() => {
    if (stripRef.current && monthGrids.length > 0) {
      stripRef.current.scrollLeft = stripRef.current.scrollWidth
    }
  }, [monthGrids])

  // Convert vertical wheel to horizontal scroll
  const handleWheel = useCallback((e) => {
    if (stripRef.current && e.deltaY !== 0) {
      e.preventDefault()
      stripRef.current.scrollLeft += e.deltaY
    }
  }, [])

  const handleDayClick = useCallback((targetId) => {
    document.dispatchEvent(new CustomEvent('expand-day', { detail: { targetId } }))
    const el = document.getElementById(targetId)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [])

  if (monthGrids.length === 0) return null

  return (
    <div className="calendar-strip" ref={stripRef} onWheel={handleWheel}>
      <div className="calendar-strip-inner">
        {monthGrids.map((mg, idx) => (
          <Fragment key={mg.key}>
            <div className="cal-month">
              <div className="cal-month-header">{mg.label}</div>
              <div className="cal-dow-row">
                {DOW_LABELS.map((d, i) => (
                  <span key={i} className="cal-dow">{d}</span>
                ))}
              </div>
              <div className="cal-grid">
                {mg.cells.map((cell, ci) =>
                  cell === null ? (
                    <span key={ci} className="cal-cell" />
                  ) : (
                    <span
                      key={ci}
                      className={[
                        'cal-cell',
                        cell.active ? `cal-cell-sig-${cell.significance <= 2 ? 'low' : cell.significance >= 4 ? 'high' : 'mid'}` : 'cal-cell-inactive',
                        cell.isToday ? 'cal-cell-today' : '',
                      ].filter(Boolean).join(' ')}
                      onClick={cell.active ? () => handleDayClick(cell.targetId) : undefined}
                      title={cell.active ? cell.iso : undefined}
                    >
                      {cell.day}
                    </span>
                  )
                )}
              </div>
            </div>
            {gaps.includes(idx) && (
              <div className="cal-gap-separator">
                <span className="cal-gap-dots">&middot;&middot;&middot;</span>
              </div>
            )}
          </Fragment>
        ))}
      </div>
    </div>
  )
}

export default CalendarStrip
