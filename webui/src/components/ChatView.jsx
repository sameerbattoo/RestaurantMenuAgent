import { useState, useRef, useEffect } from 'react'
import { invokeAgent } from '../services/agent'
import { getUserName } from '../services/auth'
import { useSpeechToText } from '../hooks/useSpeechToText'
import ThemeToggle from './ThemeToggle'
import MarkdownMessage from './MarkdownMessage'
import AudioWaveform from './AudioWaveform'

const ACCEPTED_TYPES = '.pdf,.jpg,.jpeg,.png,.heic,.heif,.tiff,.bmp,.webp'

function MetricsBar({ metrics }) {
  const input = metrics.input_tokens || 0
  const output = metrics.output_tokens || 0
  const total = metrics.total_tokens || input + output
  const cacheRead = metrics.cache_read_tokens || 0
  const cost = metrics.cost_usd
  const model = metrics.model || ''
  const duration = metrics.duration_ms
  const ttft = metrics.ttft_ms

  // Shorten model name for display
  const modelShort = model
    .replace('us.anthropic.', '')
    .replace('us.amazon.', '')

  return (
    <div className="mt-3 pt-2.5 border-t border-gray-100 dark:border-gray-800">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-400 dark:text-gray-500">
        <span className="flex items-center gap-1" title="Total tokens">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
          {total.toLocaleString()}
        </span>
        <span title="Input tokens">↓{input.toLocaleString()}</span>
        <span title="Output tokens">↑{output.toLocaleString()}</span>
        {cacheRead > 0 && (
          <span className="text-green-500 dark:text-green-400" title="Cache read tokens">
            ⚡{cacheRead.toLocaleString()}
          </span>
        )}
        {cost != null && (
          <span title="Estimated cost">💰${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)}</span>
        )}
        {duration != null && (
          <span title="Total latency">⏱️{duration >= 1000 ? `${(duration / 1000).toFixed(1)}s` : `${duration}ms`}</span>
        )}
        {ttft != null && (
          <span title="Time to first token">TTF:{ttft}ms</span>
        )}
        {modelShort && (
          <span className="text-gray-300 dark:text-gray-600" title="Model used">{modelShort}</span>
        )}
      </div>
    </div>
  )
}

function ChatView({ theme, toggleTheme }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [cookingActive, setCookingActive] = useState(false)
  const [sessionId, setSessionId] = useState(() => {
    const stored = sessionStorage.getItem('chat_session_id')
    if (stored) return stored
    const id = crypto.randomUUID()
    sessionStorage.setItem('chat_session_id', id)
    return id
  })
  const [suggestions, setSuggestions] = useState([
    { icon: '📋', text: 'List all my restaurants' },
    { icon: '📊', text: 'Which menu has the most items?' },
    { icon: '🍽️', text: 'Show me a random menu' },
    { icon: '🔍', text: 'What can you help me with?' },
  ])
  const messagesEndRef = useRef(null)
  const fileInputRef = useRef(null)
  const textareaRef = useRef(null)
  const chatContainerRef = useRef(null)
  const userScrolledUpRef = useRef(false)
  const ttftRef = useRef(null) // Time to first token tracking
  const [pendingAutoSubmit, setPendingAutoSubmit] = useState(false)

  // Speech-to-text (Whisper tiny.en, runs in browser)
  const {
    isListening,
    isLoading: isSpeechLoading,
    isModelLoading,
    modelProgress,
    transcript: speechTranscript,
    error: speechError,
    recordingDuration,
    startListening,
    stopListening,
    resetTranscript,
    isSupported: isSpeechSupported,
  } = useSpeechToText({
    model: 'Xenova/whisper-tiny.en',
    silenceThreshold: 0.01,
    silenceTimeout: 2000,
    onSilenceDetected: () => {
      stopListening()
      setPendingAutoSubmit(true)
    },
  })

  // Auto-submit speech transcript when recording stops
  useEffect(() => {
    if (speechTranscript && pendingAutoSubmit && !loading) {
      setPendingAutoSubmit(false)
      const text = speechTranscript.trim()
      resetTranscript()
      if (text) {
        handleQuickAction(text)
      }
    }
  }, [speechTranscript, pendingAutoSubmit, loading])

  const handleMicClick = async () => {
    if (isListening) {
      stopListening()
      setPendingAutoSubmit(true)
    } else {
      resetTranscript()
      setInput('')
      setPendingAutoSubmit(false)
      await startListening()
    }
  }

  const formatDuration = (seconds) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  // Fetch restaurant names on mount for dynamic suggestions
  useEffect(() => {
    let cancelled = false
    const fetchRestaurants = async () => {
      try {
        const token = sessionStorage.getItem('access_token')
        if (!token) return

        const { default: appConfig } = await import('../config')
        const escapedArn = encodeURIComponent(appConfig.agentcoreArn)
        const url = `https://bedrock-agentcore.${appConfig.region}.amazonaws.com/runtimes/${escapedArn}/invocations?qualifier=DEFAULT`

        const resp = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': `suggestions-${Date.now()}`,
          },
          body: JSON.stringify({ prompt: 'list restaurants names only, respond with just a comma-separated list of restaurant names, nothing else', session_id: `suggestions-${Date.now()}` }),
        })

        if (!resp.ok) return

        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let fullText = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            let data = line.slice(6).trim()
            if (data.startsWith('"') && data.endsWith('"')) {
              try { data = JSON.parse(data) } catch { data = data.slice(1, -1) }
            }
            if (data && !data.startsWith('[TOOL') && !data.startsWith('[METRICS]')) {
              fullText += data
            }
          }
        }

        if (cancelled) return

        // Try to extract restaurant names (comma-separated or line-separated)
        const names = fullText
          .replace(/\*\*/g, '')
          .split(/[,\n|•]/)
          .map(n => n.replace(/^\d+[\.\)]\s*/, '').trim())
          .filter(n => n.length > 3 && n.length < 40 && !n.match(/^(here|total|restaurant|menu|stored|list|all|your|the|i |you)/i))

        if (names.length >= 2) {
          const shuffled = names.sort(() => Math.random() - 0.5)
          const icons = ['🍛', '🌮', '🍜', '🥘', '🍕', '🥗']
          setSuggestions([
            { icon: '📋', text: 'List all my restaurants' },
            { icon: icons[Math.floor(Math.random() * icons.length)], text: `Show me the ${shuffled[0]} menu` },
            { icon: icons[Math.floor(Math.random() * icons.length)], text: `Regenerate HTML for ${shuffled[1]}` },
            { icon: '📊', text: 'Which menu has the most items?' },
          ])
        }
      } catch { /* silently fail — static suggestions remain */ }
    }

    fetchRestaurants()
    return () => { cancelled = true }
  }, [])

  const handleNewConversation = () => {
    const id = crypto.randomUUID()
    sessionStorage.setItem('chat_session_id', id)
    setSessionId(id)
    setMessages([])
    setLoading(false)
    setCookingActive(false)
  }

  const handleQuickAction = (text) => {
    if (loading) return
    setInput(text)
    // Directly trigger send with the text (bypasses stale state)
    setMessages((prev) => [...prev, { role: 'user', content: text, timestamp: new Date() }])
    setInput('')
    setLoading(true)
    ;(async () => {
      try {
        setMessages((prev) => [...prev, { role: 'assistant', content: '', isStreaming: true, timestamp: new Date() }])
        ttftRef.current = { start: performance.now(), captured: false }
        await invokeAgent(text, [], sessionId, (event) => {
          switch (event.type) {
            case 'content':
              setCookingActive(false)
              if (ttftRef.current && !ttftRef.current.captured) {
                ttftRef.current.ttft = Math.round(performance.now() - ttftRef.current.start)
                ttftRef.current.captured = true
              }
              setMessages((prev) => {
                const updated = [...prev]
                const last = updated[updated.length - 1]
                if (last && last.role === 'assistant') {
                  updated[updated.length - 1] = { ...last, content: last.content + event.data }
                }
                return updated
              })
              break
            case 'tool_use':
              setMessages((prev) => {
                const updated = [...prev]
                const last = updated[updated.length - 1]
                if (last && last.role === 'assistant') {
                  const toolName = event.data.trim()
                  let icon = '🔧', label = toolName
                  if (toolName === 'list_restaurant_menus') { icon = '📋'; label = 'Checking stored menus...' }
                  else if (toolName === 'get_current_menu') { icon = '🔍'; label = 'Loading menu data...' }
                  else if (toolName === 'process_document') { icon = '👨‍🍳'; label = 'Extracting menu items...' }
                  const separator = last.content.endsWith('\n') ? '' : '\n\n'
                  updated[updated.length - 1] = { ...last, content: last.content + `${separator}${icon} *${label}*\n\n` }
                }
                return updated
              })
              break
            case 'metrics':
              setMessages((prev) => {
                const updated = [...prev]
                const last = updated[updated.length - 1]
                if (last && last.role === 'assistant') {
                  const metricsWithTtft = { ...event.data }
                  if (ttftRef.current && ttftRef.current.ttft) {
                    metricsWithTtft.ttft_ms = ttftRef.current.ttft
                  }
                  updated[updated.length - 1] = { ...last, metrics: metricsWithTtft }
                }
                return updated
              })
              break
            case 'error':
              setMessages((prev) => {
                const updated = [...prev]
                const last = updated[updated.length - 1]
                if (last && last.role === 'assistant') {
                  updated[updated.length - 1] = { ...last, content: event.data, isError: true, isStreaming: false }
                }
                return updated
              })
              break
            case 'done':
              setMessages((prev) => {
                const updated = [...prev]
                const last = updated[updated.length - 1]
                if (last && last.role === 'assistant') {
                  updated[updated.length - 1] = { ...last, isStreaming: false }
                }
                return updated
              })
              break
          }
        })
      } catch (err) {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last && last.role === 'assistant') {
            updated[updated.length - 1] = { ...last, content: `Error: ${err.message}`, isError: true, isStreaming: false }
          }
          return updated
        })
      } finally {
        setLoading(false)
      }
    })()
  }

  useEffect(() => {
    // Only auto-scroll if user hasn't manually scrolled up
    if (!userScrolledUpRef.current) {
      const container = chatContainerRef.current
      if (container) {
        container.scrollTop = container.scrollHeight
      }
    }
  }, [messages])

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 150) + 'px'
    }
  }, [input])

  const handleSend = async () => {
    if ((!input.trim() && files.length === 0) || loading) return

    const userContent = input.trim() || 'Process this menu'
    const attachedFiles = [...files]

    const fileNames = attachedFiles.map((f) => f.name)
    const displayContent = fileNames.length > 0
      ? `${userContent}\n\n📎 ${fileNames.join(', ')}`
      : userContent

    setMessages((prev) => [...prev, { role: 'user', content: displayContent, timestamp: new Date() }])
    setInput('')
    setFiles([])
    setLoading(true)

    try {
      // Add a placeholder assistant message that we'll stream into
      setMessages((prev) => [...prev, { role: 'assistant', content: '', isStreaming: true, timestamp: new Date() }])

      ttftRef.current = { start: performance.now(), captured: false }

      await invokeAgent(userContent, attachedFiles, sessionId, (event) => {
        switch (event.type) {
          case 'content':
            setCookingActive(false)
            // Capture TTFT on first content chunk
            if (ttftRef.current && !ttftRef.current.captured) {
              ttftRef.current.ttft = Math.round(performance.now() - ttftRef.current.start)
              ttftRef.current.captured = true
            }
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last && last.role === 'assistant') {
                updated[updated.length - 1] = { ...last, content: last.content + event.data }
              }
              return updated
            })
            break
          case 'tool_use':
            // Show tool use on its own line, collapse consecutive same-name calls
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last && last.role === 'assistant') {
                const toolName = event.data.trim()
                const lines = last.content.split('\n')
                const lastToolLine = lines.filter(l => l.match(/^(🔧|👨‍🍳|🎨)/)).pop()
                if (lastToolLine && lastToolLine.includes(toolName)) {
                  return updated // skip duplicate
                }
                // Fun icons per tool
                let icon = '🔧'
                let label = toolName
                if (toolName === 'process_document') {
                  icon = '👨‍🍳'
                  label = 'Extracting menu items...'
                } else if (toolName === 'analyze_menu_style') {
                  icon = '🎨'
                  label = 'Analyzing original style...'
                } else if (toolName === 'regenerate_menu_html') {
                  icon = '✨'
                  label = 'Generating styled menu...'
                } else if (toolName === 'save_menu') {
                  icon = '💾'
                  label = 'Saving to database...'
                } else if (toolName === 'list_restaurant_menus') {
                  icon = '📋'
                  label = 'Checking stored menus...'
                } else if (toolName === 'get_current_menu') {
                  icon = '🔍'
                  label = 'Loading menu data...'
                }
                const separator = last.content.endsWith('\n') ? '' : '\n\n'
                updated[updated.length - 1] = { ...last, content: last.content + `${separator}${icon} *${label}*\n\n` }
              }
              return updated
            })
            if (event.data.trim() === 'process_document') {
              setCookingActive(true)
            } else if (event.data.trim() === 'regenerate_menu_html' || event.data.trim() === 'analyze_menu_style') {
              setCookingActive(true)
            }
            break
          case 'metrics':
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last && last.role === 'assistant') {
                const metricsWithTtft = { ...event.data }
                if (ttftRef.current && ttftRef.current.ttft) {
                  metricsWithTtft.ttft_ms = ttftRef.current.ttft
                }
                updated[updated.length - 1] = { ...last, metrics: metricsWithTtft }
              }
              return updated
            })
            break
          case 'error':
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last && last.role === 'assistant') {
                updated[updated.length - 1] = { ...last, content: event.data, isError: true, isStreaming: false }
              }
              return updated
            })
            break
          case 'done':
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last && last.role === 'assistant') {
                updated[updated.length - 1] = { ...last, isStreaming: false }
              }
              return updated
            })
            break
        }
      })
    } catch (err) {
      setMessages((prev) => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = { ...last, content: `Error: ${err.message}`, isError: true, isStreaming: false }
        } else {
          updated.push({ role: 'assistant', content: `Error: ${err.message}`, isError: true })
        }
        return updated
      })
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleFileSelect = (e) => {
    const selected = Array.from(e.target.files || [])
    setFiles((prev) => [...prev, ...selected])
    e.target.value = ''
  }

  const handleDrop = (e) => {
    e.preventDefault()
    const dropped = Array.from(e.dataTransfer.files || [])
    setFiles((prev) => [...prev, ...dropped])
  }

  const handleDragOver = (e) => {
    e.preventDefault()
  }

  const removeFile = (index) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }

  const handleLogout = () => {
    sessionStorage.clear()
    window.location.href = '/'
  }

  const handleCopyMessage = async (content) => {
    try {
      const { marked } = await import('marked')
      const html = marked.parse(content)
      // Copy as rich HTML (for Word/Docs) + plain text fallback
      const blob = new Blob([html], { type: 'text/html' })
      const textBlob = new Blob([content], { type: 'text/plain' })
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': blob,
          'text/plain': textBlob,
        })
      ])
    } catch {
      // Fallback: plain text copy
      navigator.clipboard.writeText(content)
    }
  }

  const handleExportConversation = async () => {
    const { marked } = await import('marked')
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 16)
    let html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Menu Assistant Chat - ${timestamp}</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:800px;margin:40px auto;padding:20px;background:#1a1a2e;color:#e0e0e0;}
h1{color:#f97316;}.subtitle{color:#888;font-size:13px;margin-top:4px;}.msg{margin:16px 0;padding:16px;border-radius:12px;overflow-wrap:break-word;}.user{background:#f97316;color:white;margin-left:20%;}.user a{color:#fed7aa;}.assistant{background:#2d2d44;margin-right:10%;}.assistant a{color:#fb923c;}
.role{font-weight:600;font-size:12px;opacity:0.7;margin-bottom:6px;}.time{font-size:11px;opacity:0.5;margin-top:8px;}.content{line-height:1.7;}
.content h1,.content h2,.content h3{margin-top:1em;margin-bottom:0.5em;color:#f97316;}.content p{margin:0.5em 0;}.content ul,.content ol{margin:0.5em 0;padding-left:1.5em;}.content li{margin:0.25em 0;}
.content table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px;}.content th,.content td{border:1px solid #444;padding:8px 12px;text-align:left;}.content th{background:#333;font-weight:600;}
.content code{background:rgba(255,255,255,0.08);padding:2px 6px;border-radius:4px;font-size:0.9em;}.content pre{background:#1e293b;padding:12px;border-radius:8px;overflow-x:auto;}.content pre code{background:none;padding:0;}
.content blockquote{border-left:3px solid #f97316;padding:0.5em 1em;margin:0.5em 0;opacity:0.8;}.content strong{color:#fff;}.content a{color:#fb923c;}
.footer{margin-top:40px;padding-top:20px;border-top:1px solid #333;text-align:center;font-size:12px;color:#666;}.aws-badge{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:#232f3e;border-radius:6px;color:#ff9900;font-weight:500;margin-top:8px;}</style></head><body>
<h1>🍽️ Menu Assistant Chat</h1><p class="subtitle">Exported: ${new Date().toLocaleString()} • Session: ${sessionId.slice(0, 8)}...</p><hr style="border-color:#333;margin:20px 0;">\n`

    messages.forEach((msg) => {
      const role = msg.role === 'user' ? 'You' : 'Assistant'
      const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : ''
      const renderedContent = msg.role === 'assistant' ? marked.parse(msg.content) : msg.content.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>')
      html += `<div class="msg ${msg.role}"><div class="role">${role}</div><div class="content">${renderedContent}</div><div class="time">${time}</div></div>\n`
    })

    html += `<div class="footer"><div class="aws-badge">☁️ Built by AWS Startup SA Team • Powered by Amazon Bedrock</div><p style="margin-top:8px;font-size:11px;">AgentCore Runtime • Claude Sonnet 4 • Strands SDK</p></div></body></html>`
    const blob = new Blob([html], { type: 'text/html' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `menu-chat-${timestamp}.html`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div
      className="min-h-screen flex flex-col bg-gray-50 dark:bg-gray-950 transition-colors duration-200"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      {/* Header */}
      <header className="sticky top-0 z-10 glass-panel border-b border-gray-200/60 dark:border-gray-800/60 px-6 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-brand-500 to-brand-600 shadow-lg shadow-brand-500/20 animate-glow">
              <span className="text-lg">🍽️</span>
            </div>
            <div>
              <h1 className="text-base font-semibold leading-tight gradient-text">
                Menu Assistant
              </h1>
              <p className="text-xs text-gray-400 dark:text-gray-500">AWS Bedrock-powered menu processing</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleExportConversation}
              disabled={messages.length === 0}
              className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-brand-600 dark:hover:text-brand-400 px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Export conversation to HTML"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Export
            </button>
            <button
              onClick={handleNewConversation}
              className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-brand-600 dark:hover:text-brand-400 px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title="Start a new conversation"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              New Chat
            </button>
            <ThemeToggle theme={theme} toggleTheme={toggleTheme} />
            <span className="text-sm text-gray-500 dark:text-gray-400 hidden sm:inline">
              {getUserName() || ''}
            </span>
            <button
              onClick={handleLogout}
              className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              Sign Out
            </button>
          </div>
        </div>
      </header>

      {/* Messages */}
      <div
        ref={chatContainerRef}
        className="flex-1 overflow-y-auto px-4 py-6"
        onScroll={(e) => {
          const el = e.target
          const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
          // If user scrolled more than 150px from bottom, they're reading history
          userScrolledUpRef.current = distanceFromBottom > 150
        }}
      >
        <div className="max-w-7xl mx-auto">
          {messages.length === 0 && (
            <div className="text-center mt-24 animate-fade-in relative">
              {/* Floating particles background */}
              <div className="absolute inset-0 overflow-hidden pointer-events-none">
                <div className="floating-particle absolute top-10 left-1/4 w-2 h-2 rounded-full bg-brand-500/20" style={{ animationDelay: '0s' }} />
                <div className="floating-particle absolute top-20 right-1/3 w-3 h-3 rounded-full bg-brand-400/15" style={{ animationDelay: '1s' }} />
                <div className="floating-particle absolute top-32 left-1/3 w-1.5 h-1.5 rounded-full bg-brand-600/20" style={{ animationDelay: '2s' }} />
                <div className="floating-particle absolute top-16 right-1/4 w-2.5 h-2.5 rounded-full bg-brand-300/15" style={{ animationDelay: '3s' }} />
                <div className="floating-particle absolute top-40 left-1/2 w-2 h-2 rounded-full bg-brand-500/10" style={{ animationDelay: '4s' }} />
              </div>

              <div className="relative inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-brand-500/10 to-brand-600/20 dark:from-brand-500/20 dark:to-brand-600/30 mb-5 animate-glow">
                <span className="text-5xl">🍽️</span>
              </div>
              <h2 className="text-2xl font-bold mb-2 gradient-text">
                How can I help?
              </h2>
              <p className="text-gray-500 dark:text-gray-400 max-w-md mx-auto text-sm">
                Upload a menu file to extract items, or ask me to add, edit, or export menu data.
              </p>
              <div className="flex flex-wrap justify-center gap-2 mt-6">
                {['PDF', 'JPG', 'PNG', 'HEIC', 'TIFF', 'WEBP'].map((fmt) => (
                  <span
                    key={fmt}
                    className="text-xs px-2.5 py-1 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 font-medium"
                  >
                    {fmt}
                  </span>
                ))}
              </div>
              {/* Quick action bubbles */}
              <div className="flex flex-wrap justify-center gap-2.5 mt-8 max-w-lg mx-auto">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion.text}
                    onClick={() => handleQuickAction(suggestion.text)}
                    disabled={loading}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm text-gray-700 dark:text-gray-300 hover:border-brand-400 dark:hover:border-brand-500 hover:bg-brand-50 dark:hover:bg-brand-950/30 hover:text-brand-600 dark:hover:text-brand-400 transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <span>{suggestion.icon}</span>
                    <span>{suggestion.text}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`mb-5 flex items-end gap-2.5 animate-slide-up ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              style={{ animationDelay: `${Math.min(i * 50, 200)}ms` }}
            >
              {/* Assistant avatar — plate icon with spinning gradient ring + sparkle */}
              {msg.role !== 'user' && (
                <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center mb-1 relative">
                  <div className="absolute inset-0 rounded-full bg-gradient-to-r from-brand-500 via-amber-300 to-yellow-200 animate-[spin_3s_linear_infinite] opacity-90" />
                  <div className="absolute inset-[2px] rounded-full bg-gray-900 dark:bg-gray-800" />
                  <span className="relative text-xs">🍽️</span>
                  <span className="absolute -top-0.5 -right-0.5 text-[8px] animate-ping">✨</span>
                </div>
              )}

              <div
                className={`max-w-[75%] rounded-2xl px-4 py-3 transition-all duration-300 hover:shadow-lg hover:-translate-y-0.5 ${
                  msg.role === 'user'
                    ? 'bg-gradient-to-br from-brand-500 to-brand-600 text-white shadow-md shadow-brand-500/15 hover:border-brand-300 hover:ring-1 hover:ring-brand-300/50'
                    : msg.isError
                    ? 'bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800/50 text-red-700 dark:text-red-300 hover:border-red-400 dark:hover:border-red-600'
                    : 'bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-gray-800 dark:text-gray-200 shadow-sm hover:border-brand-400 dark:hover:border-brand-500 hover:ring-1 hover:ring-brand-400/30 dark:hover:ring-brand-500/30'
                }`}
              >
                {msg.role === 'user' ? (
                  <>
                    <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
                      {msg.content}
                    </pre>
                    {msg.timestamp && (
                      <span className="block text-[10px] text-white/50 mt-1.5 text-right">
                        {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <MarkdownMessage content={msg.content} />
                    {msg.isStreaming && cookingActive && (
                      <div className="flex items-center gap-3 mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
                        <div className="relative">
                          <span className="text-3xl animate-bounce" style={{ animationDuration: '1.5s' }}>
                            {msg.content.includes('style') || msg.content.includes('Generating') ? '🎨' : '👨‍🍳'}
                          </span>
                          <span className="absolute -top-1 -right-1 text-sm animate-ping" style={{ animationDuration: '2s' }}>✨</span>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-gray-600 dark:text-gray-300 flex items-center gap-1">
                            {msg.content.includes('style') || msg.content.includes('Generating')
                              ? 'Designing your menu'
                              : 'Cooking up your menu'}
                            <span className="inline-flex gap-0.5 ml-1">
                              <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                              <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                              <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                            </span>
                          </p>
                          <p className="text-xs text-gray-400 dark:text-gray-500">
                            {msg.content.includes('style') || msg.content.includes('Generating')
                              ? 'Analyzing style → Generating HTML → Uploading 🖌️'
                              : 'Extracting dishes, prices & categories 🍳🔥'}
                          </p>
                        </div>
                      </div>
                    )}
                    {msg.metrics && <MetricsBar metrics={msg.metrics} />}
                    {/* Timestamp + actions */}
                    {!msg.isStreaming && msg.content && (
                      <div className="flex items-center justify-between mt-2 pt-2 border-t border-gray-100 dark:border-gray-800">
                        <span className="text-[10px] text-gray-400 dark:text-gray-600">
                          {msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                        </span>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleCopyMessage(msg.content)}
                            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded transition-colors"
                            title="Copy response"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* User avatar */}
              {msg.role === 'user' && (
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-700 dark:bg-gray-600 flex items-center justify-center shadow-sm mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
              )}
            </div>
          ))}

          {loading && (!messages.length || messages[messages.length - 1]?.content === '') && (
            <div className="flex items-end gap-2.5 justify-start mb-4 animate-slide-up">
              <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center mb-1 relative">
                <div className="absolute inset-0 rounded-full bg-gradient-to-r from-brand-500 via-amber-300 to-yellow-200 animate-[spin_3s_linear_infinite] opacity-90" />
                <div className="absolute inset-[2px] rounded-full bg-gray-900 dark:bg-gray-800" />
                <span className="relative text-xs">🍽️</span>
                <span className="absolute -top-0.5 -right-0.5 text-[8px] animate-ping">✨</span>
              </div>
              <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl px-5 py-4 shadow-sm">
                {files.length > 0 || messages[messages.length - 1]?.content?.includes('📎') ? (
                  <div className="flex flex-col items-center gap-3 py-2">
                    <div className="relative">
                      <div className="text-5xl animate-bounce" style={{ animationDuration: '1.5s' }}>👨‍🍳</div>
                      <div className="absolute -top-2 -right-2 text-2xl animate-ping" style={{ animationDuration: '2s' }}>✨</div>
                      <div className="absolute -bottom-1 left-0 text-lg animate-pulse">🍳</div>
                      <div className="absolute -bottom-1 right-0 text-lg animate-pulse" style={{ animationDelay: '0.5s' }}>🔥</div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-600 dark:text-gray-300">Cooking up your menu</span>
                      <span className="inline-flex gap-0.5">
                        <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 dark:text-gray-500">Extracting dishes, prices & categories</p>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="flex gap-1">
                      <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                    <span className="text-sm text-gray-400 dark:text-gray-500">Thinking...</span>
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Bottom area (file preview + input) — pinned to bottom */}
      <div className="sticky bottom-0 z-10">
        {/* File preview bar */}
        {files.length > 0 && (
          <div className="border-t border-gray-200 dark:border-gray-800 bg-brand-50/50 dark:bg-brand-950/20 px-4 py-2.5">
            <div className="max-w-7xl mx-auto flex flex-wrap gap-2">
            {files.map((file, i) => (
              <div
                key={i}
                className="flex items-center gap-2 bg-white dark:bg-gray-800 border border-brand-200 dark:border-brand-800/50 rounded-lg px-3 py-1.5 text-sm shadow-sm animate-fade-in"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-brand-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <span className="text-gray-700 dark:text-gray-200 max-w-[180px] truncate font-medium">
                  {file.name}
                </span>
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  {(file.size / 1024).toFixed(0)}KB
                </span>
                <button
                  onClick={() => removeFile(i)}
                  className="text-gray-400 hover:text-red-500 dark:hover:text-red-400 ml-0.5 transition-colors"
                  aria-label={`Remove ${file.name}`}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-4">
        <div className="max-w-7xl mx-auto">
          {/* Model loading progress */}
          {isModelLoading && (
            <div className="mb-3 flex items-center gap-3 px-4 py-2.5 rounded-xl bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800/50">
              <svg className="h-4 w-4 animate-spin text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <div className="flex-1">
                <p className="text-sm text-blue-700 dark:text-blue-300 font-medium">
                  Loading speech model (first time only, ~40MB)... {modelProgress > 0 ? `${modelProgress}%` : ''}
                </p>
                {modelProgress > 0 && (
                  <div className="mt-1.5 h-1.5 w-full bg-blue-100 dark:bg-blue-900/50 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded-full transition-all duration-300"
                      style={{ width: `${modelProgress}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Listening indicator with waveform */}
          {isListening && (
            <div className="mb-3 flex items-center gap-3 px-4 py-2.5 rounded-xl bg-green-50 dark:bg-green-950/30 border border-green-300 dark:border-green-700/50">
              <AudioWaveform isActive={isListening} color="#22c55e" />
              <div className="flex items-center gap-2 flex-1">
                <span className="text-sm text-green-700 dark:text-green-300 font-medium">
                  {isSpeechLoading ? 'Processing...' : 'Listening...'}
                </span>
                {speechTranscript && (
                  <span className="text-sm text-green-600 dark:text-green-400 italic truncate max-w-[200px]">
                    "{speechTranscript}"
                  </span>
                )}
              </div>
              <div className="px-2.5 py-1 bg-green-100 dark:bg-green-900/50 rounded-md font-mono text-sm font-semibold text-green-700 dark:text-green-300 min-w-[60px] text-center">
                {formatDuration(recordingDuration)}
              </div>
            </div>
          )}

          <div className="flex items-end gap-2 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-2xl px-3 py-2 focus-within:ring-2 focus-within:ring-brand-500/50 focus-within:border-brand-400 dark:focus-within:border-brand-500 transition-all input-glow">
            {/* File upload button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={loading}
              className="flex-shrink-0 p-2 text-gray-400 dark:text-gray-500 hover:text-brand-500 dark:hover:text-brand-400 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-40"
              title="Attach menu file (PDF, image)"
              aria-label="Attach menu file"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
              </svg>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_TYPES}
              multiple
              onChange={handleFileSelect}
              className="hidden"
              aria-hidden="true"
            />

            {/* Mic button */}
            {isSpeechSupported && (
              <button
                onClick={handleMicClick}
                disabled={loading || isModelLoading || isSpeechLoading}
                className={`flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-full border-2 transition-all duration-300 ${
                  isListening
                    ? 'bg-red-500 border-red-500 text-white animate-[micPulse_1.5s_ease-in-out_infinite]'
                    : 'bg-brand-500/10 dark:bg-brand-400/15 border-brand-500/30 dark:border-brand-400/30 text-brand-500 dark:text-brand-400 hover:bg-brand-500/20 dark:hover:bg-brand-400/25'
                } disabled:opacity-40 disabled:cursor-not-allowed`}
                title={isModelLoading ? 'Loading model...' : isListening ? 'Click to stop recording' : 'Click to start voice input'}
                aria-label={isListening ? 'Stop recording' : 'Start voice input'}
              >
                {isModelLoading ? (
                  <svg className="h-5 w-5 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                ) : (
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                  </svg>
                )}
              </button>
            )}

            {/* Textarea */}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={files.length > 0 ? 'Add instructions or press Send...' : 'Ask about menus or drop a file here...'}
              rows={1}
              className="flex-1 resize-none bg-transparent border-0 outline-none text-sm text-gray-800 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 py-2 max-h-[150px]"
            />

            {/* Send button */}
            <button
              onClick={handleSend}
              disabled={loading || (!input.trim() && files.length === 0)}
              className="flex-shrink-0 p-2 bg-gradient-to-br from-brand-500 to-brand-600 hover:from-brand-600 hover:to-brand-700 disabled:from-gray-200 disabled:to-gray-300 dark:disabled:from-gray-700 dark:disabled:to-gray-800 text-white disabled:text-gray-400 dark:disabled:text-gray-500 rounded-xl transition-all duration-300 hover:shadow-lg hover:shadow-brand-500/30 hover:scale-105 disabled:shadow-none disabled:scale-100"
              aria-label="Send message"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 19V5m0 0l-7 7m7-7l7 7" />
              </svg>
            </button>
          </div>

          <p className="text-xs text-center text-gray-400 dark:text-gray-600 mt-2.5">
            Drag & drop files, use the attach button, or click the mic for voice input. Supports PDF, JPG, PNG, HEIC, TIFF, WEBP.
            <button
              onClick={() => {
                const el = document.getElementById('session-id-display')
                if (el) el.classList.toggle('hidden')
              }}
              className="ml-2 text-gray-400 dark:text-gray-500 hover:text-brand-500 dark:hover:text-brand-400 transition-colors"
              title="Show session ID"
            >
              ⓘ
            </button>
          </p>
          <p id="session-id-display" className="hidden text-[11px] text-center text-gray-500 dark:text-gray-400 mt-1 font-mono select-all cursor-pointer mx-auto w-fit bg-gray-100 dark:bg-gray-800 rounded px-3 py-1" title="Click to copy" onClick={() => navigator.clipboard.writeText(sessionId)}>
            Session: {sessionId}
          </p>
          <p className="text-[11px] text-center text-gray-500 dark:text-gray-400 mt-2">
            Built with ❤️ by <span className="font-semibold text-gray-600 dark:text-gray-300">AWS Startup SA Team</span> • Powered by <span className="font-semibold text-[#ff9900]">Amazon Bedrock</span>
          </p>
        </div>
      </div>
      </div>{/* end sticky bottom wrapper */}
    </div>
  )
}

export default ChatView
