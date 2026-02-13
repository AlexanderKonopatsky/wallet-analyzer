const KNOWN_TASK_STATUSES = new Set(['cost_estimate', 'fetching', 'analyzing'])

function ActiveTasksPanel({ taskEntries, getWalletLabel, onStartAnalysis, onCancelAnalysis }) {
  if (!taskEntries.length) return null

  return (
    <div className="active-tasks-panel">
      <div className="active-tasks-header">
        {`Active Tasks (${taskEntries.length})`}
      </div>
      <div className="active-tasks-list">
        {taskEntries.map(([wallet, task]) => (
          <div key={wallet} className="active-task-item">
            <div className="active-task-wallet">
              {getWalletLabel(wallet)}
            </div>
            <div className="active-task-status">
              {task.status === 'cost_estimate' && (
                <div className="task-status-cost-estimate">
                  <div className="cost-estimate-info">
                    <div className="cost-estimate-row">
                      <span className="cost-estimate-label">Transactions:</span>
                      <span className="cost-estimate-value">{Number(task.tx_count || 0).toLocaleString()}</span>
                    </div>
                    <div className="cost-estimate-row">
                      <span className="cost-estimate-label">Cost:</span>
                      <span className="cost-estimate-value cost-estimate-price">${Number(task.cost_usd || 0).toFixed(2)}</span>
                    </div>
                    {task.is_cached && (
                      <div className="cost-estimate-note">Transactions cached</div>
                    )}
                    {task.insufficient_balance && (
                      <div className="cost-estimate-warning">
                        Not enough balance: need ${Number(task.required_cost_usd ?? task.cost_usd ?? 0).toFixed(2)}, available ${Number(task.balance_usd ?? 0).toFixed(2)}.
                      </div>
                    )}
                  </div>
                  <div className="cost-estimate-actions">
                    <button
                      className="btn-cost-start"
                      onClick={() => onStartAnalysis(wallet)}
                    >
                      Start
                    </button>
                    <button
                      className="btn-cost-cancel"
                      onClick={() => onCancelAnalysis(wallet)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
              {task.status === 'fetching' && (
                <div className="task-status-fetching">
                  <span className="task-spinner" aria-hidden="true"></span>
                  <span className="task-label">Fetching transactions</span>
                  {task.new_count !== undefined && task.total_count !== undefined && (
                    <span className="task-detail">
                      {task.new_count} new, {task.total_count} total
                    </span>
                  )}
                </div>
              )}
              {task.status === 'analyzing' && (
                <div className="task-status-analyzing">
                  <span className="task-spinner" aria-hidden="true"></span>
                  <span className="task-label">AI analysis</span>
                  {task.percent !== undefined && (
                    <div className="task-progress">
                      <div className="task-progress-bar">
                        <div
                          className="task-progress-fill"
                          style={{ width: `${task.percent}%` }}
                        ></div>
                      </div>
                      <span className="task-percent">{task.percent}%</span>
                    </div>
                  )}
                </div>
              )}
              {!KNOWN_TASK_STATUSES.has(task.status) && (
                <div className="task-status-unknown">
                  <span className="task-label">Task in progress</span>
                  {task.detail && <span className="task-detail">{task.detail}</span>}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default ActiveTasksPanel
