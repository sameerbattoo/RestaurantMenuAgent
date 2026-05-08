/**
 * AgentCore Runtime streaming client.
 *
 * Calls the deployed agent via the AgentCore invocation endpoint,
 * passing the Cognito access token as a Bearer header.
 * Parses the SSE response stream into structured events.
 */

import config from '../config'

/**
 * Convert a File object to base64 string.
 */
async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = reader.result.split(',')[1]
      resolve(base64)
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

/**
 * Invoke the agent with streaming SSE response.
 *
 * @param {string}   prompt      - The user's message
 * @param {File[]}   files       - Optional array of File objects to upload
 * @param {string}   sessionId   - Conversation session ID
 * @param {function} onEvent     - Called with { type, data } for each event
 * @param {AbortSignal} signal   - Optional abort signal
 */
export async function invokeAgent(prompt, files = [], sessionId = 'default', onEvent, signal) {
  const token = sessionStorage.getItem('access_token')
  if (!token) {
    onEvent({ type: 'error', data: 'Not authenticated. Please sign in again.' })
    return
  }

  // Build the invocation URL
  const escapedArn = encodeURIComponent(config.agentcoreArn)
  const url = `https://bedrock-agentcore.${config.region}.amazonaws.com/runtimes/${escapedArn}/invocations?qualifier=DEFAULT`

  // Build request body
  const body = { prompt, session_id: sessionId }

  if (files.length > 0) {
    body.files = await Promise.all(
      files.map(async (file) => ({
        name: file.name,
        data: await fileToBase64(file),
        type: file.type,
      }))
    )
  }

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
    'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
  }

  // Add actor ID from access token username
  try {
    const payload = token.split('.')[1]
    const claims = JSON.parse(atob(payload))
    const actorId = claims.username || claims.email || claims['cognito:username'] || ''
    if (actorId) {
      headers['X-Amzn-Bedrock-AgentCore-Runtime-Custom-ActorId'] = actorId
    }
  } catch { /* ignore — fallback handled server-side */ }

  let resp
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (err.name === 'AbortError') return
    onEvent({ type: 'error', data: `Network error: ${err.message}` })
    return
  }

  if (resp.status === 401) {
    onEvent({ type: 'error', data: 'Session expired. Please sign in again.' })
    return
  }

  if (!resp.ok) {
    const text = await resp.text()
    onEvent({ type: 'error', data: `Error ${resp.status}: ${text}` })
    return
  }

  // Parse as SSE stream (AgentCore returns text/event-stream for async generators)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let gotContent = false

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() // keep incomplete line in buffer

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue

      let data = line.slice(6).trim()
      if (!data) continue

      // Unescape JSON-encoded string
      if (data.startsWith('"') && data.endsWith('"')) {
        try { data = JSON.parse(data) } catch { data = data.slice(1, -1) }
      }

      if (!data) continue
      gotContent = true

      const stripped = data.replace(/^[\r\n]+/, '')

      if (stripped.startsWith('[TOOL USE]')) {
        onEvent({ type: 'tool_use', data: stripped.slice(10) })
      } else if (stripped.startsWith('[THINKING]')) {
        onEvent({ type: 'thinking', data: stripped.slice(10) })
      } else if (stripped.startsWith('[METRICS]')) {
        try {
          onEvent({ type: 'metrics', data: JSON.parse(stripped.slice(9)) })
        } catch { /* ignore malformed metrics */ }
      } else {
        onEvent({ type: 'content', data })
      }
    }
  }

  // If we didn't get any SSE content, try parsing the raw buffer as JSON (fallback)
  if (!gotContent && buffer.trim()) {
    try {
      const data = JSON.parse(buffer.trim())
      const responseText = data.response || JSON.stringify(data)
      onEvent({ type: 'content', data: responseText })
      if (data.metrics) {
        onEvent({ type: 'metrics', data: data.metrics })
      }
    } catch {
      onEvent({ type: 'content', data: buffer.trim() })
    }
  }

  onEvent({ type: 'done' })
}
