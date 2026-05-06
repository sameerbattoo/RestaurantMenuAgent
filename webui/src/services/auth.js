import config from '../config'

const { cognito } = config

/**
 * Redirect user to Cognito Hosted UI for login.
 */
export function login() {
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: cognito.clientId,
    redirect_uri: cognito.redirectUri,
    scope: cognito.scopes,
  })
  window.location.href = `${cognito.domain}/login?${params.toString()}`
}

/**
 * Exchange authorization code for tokens.
 */
export async function exchangeCode(code) {
  const response = await fetch(`${cognito.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: cognito.clientId,
      redirect_uri: cognito.redirectUri,
      code,
    }),
  })

  if (!response.ok) {
    throw new Error('Token exchange failed')
  }

  const data = await response.json()
  sessionStorage.setItem('id_token', data.id_token)
  sessionStorage.setItem('access_token', data.access_token)
  if (data.refresh_token) {
    sessionStorage.setItem('refresh_token', data.refresh_token)
  }
  return data
}

/**
 * Get the current access token (for API calls).
 */
export function getIdToken() {
  return sessionStorage.getItem('access_token')
}

/**
 * Get the current user's display name from the ID token.
 */
export function getUserName() {
  const idToken = sessionStorage.getItem('id_token')
  if (!idToken) return null
  try {
    const payload = idToken.split('.')[1]
    const claims = JSON.parse(atob(payload))
    return claims.name || claims.email || claims['cognito:username'] || claims.username || 'User'
  } catch {
    return null
  }
}
