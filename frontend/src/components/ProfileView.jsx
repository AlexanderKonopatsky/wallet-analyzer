import ReactMarkdown from 'react-markdown'

function ProfileView({ profile, loading, onRegenerate }) {
  if (loading) {
    return (
      <div className="profile-view">
        <div className="profile-loading">
          <div className="profile-spinner" />
          <span>Генерация профиля...</span>
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
        <h2>Профиль кошелька</h2>
        <button className="btn btn-refresh" onClick={onRegenerate}>
          Обновить профиль
        </button>
      </div>

      <div className="profile-meta">
        Сгенерировано: {new Date(profile.generated_at).toLocaleString('ru-RU')}
      </div>

      <div className="profile-content">
        <ReactMarkdown>{profile.profile_text}</ReactMarkdown>
      </div>
    </div>
  )
}

export default ProfileView
