import { useEffect, useState } from 'react'
import './PaymentWidget.css'
import { apiCall } from '../utils/api'

const PaymentWidget = ({ apiUrl = '', onPaymentSuccess }) => {
  const [tokens, setTokens] = useState({})
  const [selectedChain, setSelectedChain] = useState('')
  const [selectedToken, setSelectedToken] = useState('')
  const [amount, setAmount] = useState('')
  const [refundAddress, setRefundAddress] = useState('')
  const [currentQuote, setCurrentQuote] = useState(null)
  const [currentPaymentId, setCurrentPaymentId] = useState(null)
  const [showQuote, setShowQuote] = useState(false)
  const [showDeposit, setShowDeposit] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [depositInfo, setDepositInfo] = useState(null)
  const [paymentStatus, setPaymentStatus] = useState(null)
  const [notifiedPaymentId, setNotifiedPaymentId] = useState(null)

  const apiBase = (apiUrl || '').replace(/\/+$/, '')
  const useExternalApi = Boolean(apiBase)

  const chainNames = {
    near: 'NEAR',
    base: 'Base',
    eth: 'Ethereum',
    arb: 'Arbitrum',
    sol: 'Solana',
    pol: 'Polygon',
    bsc: 'BNB Chain',
    avax: 'Avalanche',
    op: 'Optimism',
    gnosis: 'Gnosis',
    tron: 'Tron',
    aptos: 'Aptos',
    sui: 'Sui',
    ton: 'TON',
    stellar: 'Stellar',
    xlayer: 'X Layer',
    bera: 'Berachain',
    monad: 'Monad',
    plasma: 'Plasma'
  }

  const nativeTokens = {
    eth: { symbol: 'ETH', name: 'Ethereum', id: 'eth' },
    base: { symbol: 'ETH', name: 'Ethereum', id: 'eth' },
    arb: { symbol: 'ETH', name: 'Ethereum', id: 'eth' },
    pol: { symbol: 'POL', name: 'Polygon', id: 'pol' },
    op: { symbol: 'ETH', name: 'Ethereum', id: 'eth' },
    avax: { symbol: 'AVAX', name: 'Avalanche', id: 'avax' },
    bsc: { symbol: 'BNB', name: 'BNB', id: 'bnb' }
  }

  const placeholderExamples = {
    near: 'e.g. yourname.near',
    eth: 'e.g. 0x...',
    base: 'e.g. 0x...',
    arb: 'e.g. 0x...',
    pol: 'e.g. 0x...',
    bsc: 'e.g. 0x...',
    avax: 'e.g. 0x...',
    op: 'e.g. 0x...',
    gnosis: 'e.g. 0x...',
    sol: 'e.g. base58 address',
    tron: 'e.g. T...',
    aptos: 'e.g. 0x...',
    sui: 'e.g. 0x...',
    ton: 'e.g. EQ...',
    stellar: 'e.g. G...'
  }

  const buildUrl = (path) => (useExternalApi ? `${apiBase}${path}` : path)

  const request = (path, options = {}) => {
    const endpoint = buildUrl(path)
    if (useExternalApi) {
      return fetch(endpoint, options)
    }
    return apiCall(endpoint, options)
  }

  useEffect(() => {
    loadTokens()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!currentPaymentId) return

    const interval = setInterval(async () => {
      try {
        const res = await request(`/api/payment/${currentPaymentId}/status`)
        if (!res) return
        const data = await res.json()
        setPaymentStatus(data)

        if (['SUCCESS', 'FAILED', 'REFUNDED'].includes(data.status)) {
          clearInterval(interval)
        }
      } catch {
        // Ignore polling errors
      }
    }, 3000)

    return () => clearInterval(interval)
  }, [currentPaymentId, useExternalApi, apiBase])

  useEffect(() => {
    if (paymentStatus?.status !== 'SUCCESS') return
    if (!currentPaymentId || notifiedPaymentId === currentPaymentId) return
    if (typeof onPaymentSuccess === 'function') {
      onPaymentSuccess()
    }
    setNotifiedPaymentId(currentPaymentId)
  }, [paymentStatus, currentPaymentId, notifiedPaymentId, onPaymentSuccess])

  const loadTokens = async () => {
    try {
      const res = await request('/api/tokens')
      if (!res) return
      const data = await res.json()
      setTokens(data.tokens || {})
    } catch {
      showError('Failed to load tokens')
    }
  }

  const showError = (message) => {
    setError(message)
    setTimeout(() => setError(''), 5000)
  }

  const getChainsList = () => {
    const popularChains = ['base', 'eth', 'arb', 'sol', 'near', 'pol', 'bsc']
    const allChains = Object.keys(tokens)
    return [
      ...popularChains.filter((chain) => allChains.includes(chain)),
      ...allChains.filter((chain) => !popularChains.includes(chain)).sort()
    ]
  }

  const getTokensList = () => {
    if (!selectedChain) return []

    const chainTokens = tokens[selectedChain] || []
    const tokenItems = []

    if (nativeTokens[selectedChain]) {
      const native = nativeTokens[selectedChain]
      tokenItems.push({
        value: `${selectedChain}:${native.id}`,
        label: native.symbol,
        symbol: native.symbol
      })
    }

    const popular = ['USDC', 'USDT', 'DAI', 'WETH', 'NEAR', 'SOL', 'WNEAR', 'BNB', 'MATIC']
    const sorted = [...chainTokens].sort((left, right) => {
      const leftIndex = popular.indexOf(left.symbol)
      const rightIndex = popular.indexOf(right.symbol)
      if (leftIndex >= 0 && rightIndex >= 0) return leftIndex - rightIndex
      if (leftIndex >= 0) return -1
      if (rightIndex >= 0) return 1
      return left.symbol.localeCompare(right.symbol)
    })

    sorted.slice(0, 30).forEach((token) => {
      tokenItems.push({
        value: `${selectedChain}:${token.contractAddress || token.symbol.toLowerCase()}`,
        label: token.name ? `${token.symbol} - ${token.name}` : token.symbol,
        symbol: token.symbol
      })
    })

    return tokenItems
  }

  const handleGetQuote = async () => {
    setError('')
    setLoading(true)

    try {
      const res = await request('/api/quote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          amount,
          originToken: selectedToken,
          refundAddress
        })
      })

      if (!res) return
      const data = await res.json()

      if (!res.ok) {
        showError(data.error || 'Failed to get quote')
        return
      }

      setCurrentQuote(data)
      setShowQuote(true)
    } catch {
      showError('Network error. Try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleCreatePayment = async () => {
    if (!currentQuote) return

    setLoading(true)
    setError('')

    try {
      const res = await request('/api/payment/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          amount,
          originToken: selectedToken,
          refundAddress,
          originAmount: currentQuote.originAmount
        })
      })

      if (!res) return
      const data = await res.json()

      if (!res.ok) {
        showError(data.error || 'Failed to create payment')
        return
      }

      setCurrentPaymentId(data.id)
      setDepositInfo(data)
      setShowDeposit(true)
    } catch {
      showError('Network error. Try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleCopyAddress = async () => {
    if (!depositInfo?.depositAddress) return
    try {
      await navigator.clipboard.writeText(depositInfo.depositAddress)
    } catch {
      showError('Failed to copy address')
    }
  }

  const handleReset = () => {
    setSelectedChain('')
    setSelectedToken('')
    setAmount('')
    setRefundAddress('')
    setCurrentQuote(null)
    setCurrentPaymentId(null)
    setShowQuote(false)
    setShowDeposit(false)
    setDepositInfo(null)
    setPaymentStatus(null)
    setNotifiedPaymentId(null)
  }

  const isFormValid = Number(amount) > 0 && selectedToken && refundAddress.length > 1

  const getStatusSteps = () => {
    if (!paymentStatus) return []

    const steps = [
      { status: 'PENDING_DEPOSIT', label: 'Waiting for deposit', active: false, done: false },
      { status: 'PROCESSING', label: 'Processing', active: false, done: false },
      { status: 'SUCCESS', label: 'Completed', active: false, done: false }
    ]

    const currentStatus = paymentStatus.status

    if (currentStatus === 'PENDING_DEPOSIT') {
      steps[0].active = true
    } else if (currentStatus === 'KNOWN_DEPOSIT_TX' || currentStatus === 'PROCESSING') {
      steps[0].done = true
      steps[1].active = true
    } else if (currentStatus === 'SUCCESS') {
      steps[0].done = true
      steps[1].done = true
      steps[2].done = true
    } else if (currentStatus === 'FAILED' || currentStatus === 'REFUNDED') {
      steps[0].done = true
      steps[2].active = true
      steps[2].failed = true
    }

    return steps
  }

  if (showDeposit) {
    return (
      <div className="payment-widget">
        <div className="container">
          <h1>Payment</h1>
          <div className="step">
            <div className="deposit-card">
              <h2>Send tokens to this address</h2>
              <div className="deposit-info">
                <div className="deposit-amount">
                  <span className="label">Amount:</span>
                  <span className="value">
                    {depositInfo?.originAmount} {depositInfo?.originSymbol}
                  </span>
                </div>
                <div className="deposit-chain">
                  <span className="label">Chain:</span>
                  <span className="value">
                    {depositInfo?.originChain?.charAt(0).toUpperCase() + depositInfo?.originChain?.slice(1)}
                  </span>
                </div>
                <div className="deposit-address-box">
                  <span className="label">Deposit Address:</span>
                  <div className="address-copy">
                    <code>{depositInfo?.depositAddress}</code>
                    <button className="btn-small" onClick={handleCopyAddress}>
                      Copy
                    </button>
                  </div>
                </div>
              </div>
              <p className="warning">
                Send exactly the amount shown above. The address is valid for ~1 hour.
              </p>
            </div>

            <div className="status-tracker">
              <h3>Status</h3>
              <div className="progress-steps">
                {getStatusSteps().map((step, idx) => (
                  <div
                    key={idx}
                    className={`progress-step ${step.active ? 'active' : ''} ${step.done ? 'done' : ''} ${step.failed ? 'failed' : ''}`}
                  >
                    <div className="step-dot"></div>
                    <span>{step.label}</span>
                  </div>
                ))}
              </div>
              <div className="status-text">
                {paymentStatus?.statusDescription || paymentStatus?.status || 'Waiting for your deposit...'}
              </div>
              {paymentStatus?.status === 'SUCCESS' && paymentStatus?.swapDetails?.destinationChainTxHashes?.length > 0 && (
                <div className="status-details">
                  Payment confirmed! <br />
                  <a
                    href={paymentStatus.swapDetails.destinationChainTxHashes[0].explorerUrl || paymentStatus.swapDetails.destinationChainTxHashes[0].hash}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    View transaction
                  </a>
                </div>
              )}
            </div>

            {(paymentStatus?.status === 'SUCCESS' || paymentStatus?.status === 'FAILED' || paymentStatus?.status === 'REFUNDED') && (
              <button className="btn btn-secondary" onClick={handleReset}>
                New Payment
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="payment-widget">
      <div className="container">
        <h1>Payment</h1>
        <div className="step">
          <div className="form-group">
            <label htmlFor="amount">Amount</label>
            <input
              type="number"
              id="amount"
              placeholder="10.00"
              step="0.01"
              min="0.01"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
            <small className="hint">Amount you will send</small>
          </div>

          <div className="form-group">
            <label htmlFor="chain-select">Blockchain</label>
            <select
              id="chain-select"
              value={selectedChain}
              onChange={(event) => {
                setSelectedChain(event.target.value)
                setSelectedToken('')
                setShowQuote(false)
              }}
            >
              <option value="">Select blockchain...</option>
              {getChainsList().map((chain) => (
                <option key={chain} value={chain}>
                  {chainNames[chain] || chain}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="token-select">Token</label>
            <select
              id="token-select"
              value={selectedToken}
              onChange={(event) => {
                setSelectedToken(event.target.value)
                setShowQuote(false)
              }}
              disabled={!selectedChain}
            >
              <option value="">
                {selectedChain ? 'Select token...' : 'First select blockchain'}
              </option>
              {getTokensList().map((token) => (
                <option key={token.value} value={token.value}>
                  {token.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="refund-address">Your refund address</label>
            <input
              type="text"
              id="refund-address"
              placeholder={placeholderExamples[selectedChain] || 'Address on the selected chain'}
              value={refundAddress}
              onChange={(event) => setRefundAddress(event.target.value)}
            />
            <small className="hint">If the swap fails, tokens will be returned here</small>
          </div>

          <button
            className="btn"
            onClick={handleGetQuote}
            disabled={!isFormValid || loading}
          >
            Get Quote
          </button>

          {showQuote && currentQuote && (
            <div className="quote-box">
              <h3>Quote</h3>
              <div className="quote-row">
                <span>You send:</span>
                <span>{currentQuote.originAmount} {currentQuote.originSymbol}</span>
              </div>
              {currentQuote.feeUsd && (
                <div className="quote-row">
                  <span>Fee:</span>
                  <span>${currentQuote.feeUsd}</span>
                </div>
              )}
              <button
                className="btn btn-primary"
                onClick={handleCreatePayment}
                disabled={loading}
              >
                {loading ? 'Creating...' : 'Create Payment'}
              </button>
            </div>
          )}

          {error && <div className="error">{error}</div>}
          {loading && <div className="loading">Loading...</div>}
        </div>
      </div>
    </div>
  )
}

export default PaymentWidget
