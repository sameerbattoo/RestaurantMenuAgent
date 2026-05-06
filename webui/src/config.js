// ─── Cognito & AgentCore Configuration ───────────────────────────────────────

const config = {
  // Cognito
  cognito: {
    userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID || 'us-west-2_5cqnrBvAg',
    clientId: import.meta.env.VITE_COGNITO_CLIENT_ID || '',
    domain: import.meta.env.VITE_COGNITO_DOMAIN || '',
    redirectUri: import.meta.env.VITE_REDIRECT_URI || `${window.location.origin}/callback`,
    logoutUri: import.meta.env.VITE_LOGOUT_URI || window.location.origin,
    scopes: 'openid email profile',
  },

  // AgentCore Runtime
  region: import.meta.env.VITE_AWS_REGION || 'us-west-2',
  agentcoreArn: import.meta.env.VITE_AGENTCORE_ARN || '',
}

export default config
