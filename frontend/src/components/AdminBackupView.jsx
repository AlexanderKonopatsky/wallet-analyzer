function AdminBackupView({
  backups,
  loading,
  accessDenied,
  hasRunningTasks,
  backupBusy,
  importBusy,
  downloadingFilename,
  deletingFilename,
  onRefresh,
  onDownload,
  onImportClick,
  onDownloadArchive,
  onDelete,
}) {
  const formatBytes = (bytes) => {
    const num = Number(bytes || 0)
    if (num >= 1024 * 1024 * 1024) return `${(num / (1024 * 1024 * 1024)).toFixed(2)} GB`
    if (num >= 1024 * 1024) return `${(num / (1024 * 1024)).toFixed(2)} MB`
    if (num >= 1024) return `${(num / 1024).toFixed(1)} KB`
    return `${num} B`
  }

  const formatDate = (iso) => {
    if (!iso) return '-'
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  return (
    <div className="backup-admin-view">
      <div className="backup-admin-header">
        <h2>Admin Backup</h2>
        <div className="backup-admin-actions">
          <button
            className="btn btn-refresh"
            onClick={onRefresh}
            disabled={loading || backupBusy || importBusy}
          >
            Refresh
          </button>
          <button
            className="btn btn-refresh"
            onClick={onDownload}
            disabled={loading || backupBusy || importBusy || hasRunningTasks || accessDenied}
          >
            {backupBusy ? 'Creating backup...' : 'Create & Download Backup'}
          </button>
          <button
            className="btn btn-refresh"
            onClick={onImportClick}
            disabled={loading || backupBusy || importBusy || hasRunningTasks || accessDenied}
          >
            {importBusy ? 'Importing...' : 'Import ZIP'}
          </button>
        </div>
      </div>

      <div className="backup-admin-note">
        {hasRunningTasks
          ? 'Backup/import is disabled while refresh or analysis tasks are running.'
          : 'Full backup and restore of server data folder.'}
      </div>

      {accessDenied ? (
        <div className="backup-admin-empty">Access denied for backup management.</div>
      ) : loading ? (
        <div className="backup-admin-empty">Loading backups...</div>
      ) : backups.length === 0 ? (
        <div className="backup-admin-empty">No backup archives yet.</div>
      ) : (
        <div className="backup-table-wrap">
          <table className="backup-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Size</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((item) => (
                <tr key={item.filename}>
                  <td className="backup-file">{item.filename}</td>
                  <td>{formatBytes(item.size_bytes)}</td>
                  <td>{formatDate(item.updated_at)}</td>
                  <td>
                    <div className="backup-row-actions">
                      <button
                        className="btn-backup-download"
                        onClick={() => onDownloadArchive(item.filename)}
                        disabled={Boolean(downloadingFilename) || Boolean(deletingFilename) || backupBusy || importBusy}
                      >
                        {downloadingFilename === item.filename ? 'Downloading...' : 'Download'}
                      </button>
                      <button
                        className="btn-backup-delete"
                        onClick={() => onDelete(item.filename)}
                        disabled={Boolean(downloadingFilename) || Boolean(deletingFilename) || backupBusy || importBusy}
                      >
                        {deletingFilename === item.filename ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default AdminBackupView
