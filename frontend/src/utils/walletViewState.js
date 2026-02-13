function getViewedStorageKey(walletAddr) {
  return `wallet_viewed_${walletAddr.toLowerCase()}`
}

export function countSections(markdown) {
  if (!markdown) return 0
  return (markdown.match(/^### /gm) || []).length
}

export function getViewedState(walletAddr) {
  try {
    const key = getViewedStorageKey(walletAddr)
    const data = localStorage.getItem(key)
    return data ? JSON.parse(data) : null
  } catch {
    return null
  }
}

export function initializeWalletViewedState(wallet) {
  if (!wallet?.address || !wallet.has_report) return
  const key = getViewedStorageKey(wallet.address)
  if (localStorage.getItem(key)) return
  localStorage.setItem(
    key,
    JSON.stringify({
      tx_count: wallet.tx_count,
      last_viewed: new Date().toISOString()
    })
  )
}

export function hasWalletNewData(wallet) {
  if (!wallet?.has_report || !wallet?.address) return false

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
}

function createFingerprints(markdown) {
  const sections = markdown.match(/### \d{4}-\d{2}-\d{2}[^\n]*/g) || []
  return sections.map((section) => {
    const date = section.match(/### (\d{4}-\d{2}-\d{2}(?: \u2014 \d{4}-\d{2}-\d{2})?)/)?.[1] || ''
    const sectionStart = markdown.indexOf(section)
    const nextSectionIdx = markdown.indexOf('### ', sectionStart + 1)
    const content = nextSectionIdx > 0
      ? markdown.slice(sectionStart, nextSectionIdx)
      : markdown.slice(sectionStart)
    return `${date}:${content.length}`
  })
}

export function processViewedReport(walletAddr, data) {
  const key = getViewedStorageKey(walletAddr)
  const oldState = getViewedState(walletAddr)
  const oldTxCount = oldState?.tx_count || 0
  const storedSectionCount = oldState?.section_count

  const currentFingerprints = createFingerprints(data.markdown)
  const oldFingerprints = oldState?.section_fingerprints || []

  // Check if report was updated (new txs OR merged days)
  const hasNewTxs = data.tx_count > oldTxCount
  const reportUpdated = data.last_updated && oldState?.last_viewed &&
    new Date(data.last_updated) > new Date(oldState.last_viewed)

  // Find updated sections (fingerprint changed)
  const updatedSectionIndices = new Set()
  if (oldFingerprints.length > 0) {
    currentFingerprints.forEach((fp, idx) => {
      if (idx < oldFingerprints.length && fp !== oldFingerprints[idx]) {
        updatedSectionIndices.add(idx)
      }
    })
  }

  let oldSectionCount = null
  if ((hasNewTxs || reportUpdated) && oldState !== null && storedSectionCount !== undefined) {
    oldSectionCount = storedSectionCount
  }

  localStorage.setItem(key, JSON.stringify({
    tx_count: data.tx_count,
    last_viewed: new Date().toISOString(),
    section_count: countSections(data.markdown),
    section_fingerprints: currentFingerprints
  }))

  return {
    oldSectionCount,
    updatedSectionIndices
  }
}
