import ReactMarkdown from 'react-markdown'

function ProfileView({ profile, loading, onRegenerate }) {
  if (loading) {
    return (
      <div className="profile-view">
        <div className="profile-loading">
          <div className="profile-spinner" />
          <span>Generating profile...</span>
        </div>
      </div>
    )
  }

  if (!profile) {
    return null
  }

  return (
    <div className="profile-view">
      <div className="profile-header">
        <h2>Wallet Profile</h2>
        <button className="btn btn-refresh" onClick={onRegenerate}>
          Regenerate Profile
        </button>
      </div>

      <div className="profile-meta">
        Generated: {new Date(profile.generated_at).toLocaleString('en-US')}
      </div>

      <div className="profile-content">
        <ReactMarkdown>{profile.profile_text}</ReactMarkdown>
      </div>
    </div>
  )
}

export default ProfileView
