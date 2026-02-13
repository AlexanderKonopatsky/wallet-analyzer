export function InsufficientBalanceModal({ modal, onClose, onDeposit }) {
  if (!modal?.open) return null

  return (
    <div
      className="modal-overlay insufficient-balance-overlay"
      onClick={onClose}
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
            onClick={onClose}
            aria-label="Close"
          >
            x
          </button>
        </div>

        <div className="insufficient-balance-body">
          <div className="insufficient-balance-detail">{modal.detail}</div>
          {modal.wallet && (
            <div className="insufficient-balance-meta">
              <div>
                Wallet: {modal.wallet.slice(0, 8)}...{modal.wallet.slice(-6)}
              </div>
              <div>
                Required: ${Number(modal.requiredCostUsd || 0).toFixed(2)}
              </div>
              <div>
                Current balance: ${Number(modal.balanceUsd || 0).toFixed(2)}
              </div>
            </div>
          )}
        </div>

        <div className="insufficient-balance-actions">
          <button
            className="btn-cost-cancel"
            onClick={onClose}
          >
            Close
          </button>
          <button
            className="btn-deposit insufficient-balance-deposit"
            onClick={onDeposit}
          >
            Deposit
          </button>
        </div>
      </div>
    </div>
  )
}

export function ProfileCostModal({ modal, onResolve }) {
  if (!modal?.open) return null

  return (
    <div
      className="modal-overlay profile-cost-overlay"
      onClick={() => onResolve(false)}
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
            onClick={() => onResolve(false)}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="profile-cost-body">
          <div className="profile-cost-value">
            ${Number(modal.estimatedCostUsd || 0).toFixed(4)}
          </div>
          <div className="profile-cost-label">Will be deducted from your balance</div>
          <div className="profile-cost-meta">
            <div>
              Wallet: {modal.wallet.slice(0, 8)}...{modal.wallet.slice(-6)}
            </div>
            <div>Model: {modal.model}</div>
          </div>
        </div>

        <div className="profile-cost-actions">
          <button
            className="btn-cost-cancel"
            onClick={() => onResolve(false)}
          >
            Cancel
          </button>
          <button
            className="btn-cost-start profile-cost-confirm"
            onClick={() => onResolve(true)}
          >
            Generate Profile
          </button>
        </div>
      </div>
    </div>
  )
}
