/**
 * Centralized API client with authentication support.
 *
 * All fetch() calls should use apiCall() instead to automatically
 * include auth headers and handle 401 responses.
 */

const getAuthToken = () => {
  return localStorage.getItem('auth_token')
}

const removeAuthToken = () => {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('user')
}

/**
 * Make an API call with automatic auth header injection.
 *
 * @param {string} endpoint - API endpoint path (e.g., '/api/wallets')
 * @param {object} options - Fetch options (method, headers, body, etc.)
 * @returns {Promise<Response>} Fetch response
 */
export async function apiCall(endpoint, options = {}) {
  const token = getAuthToken()

  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
  })

  // Handle 401 - redirect to login
  if (response.status === 401) {
    removeAuthToken()
    window.location.href = '/'
    return null
  }

  return response
}

/**
 * Save auth token and user data to localStorage.
 */
export function setAuthToken(token, user) {
  localStorage.setItem('auth_token', token)
  localStorage.setItem('user', JSON.stringify(user))
}

/**
 * Get stored user data from localStorage.
 */
export function getUser() {
  try {
    const user = localStorage.getItem('user')
    return user ? JSON.parse(user) : null
  } catch {
    return null
  }
}

/**
 * Logout: clear auth data and redirect to login.
 */
export function logout() {
  removeAuthToken()
  window.location.href = '/'
}
