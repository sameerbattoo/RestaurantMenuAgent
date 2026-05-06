import { useState, useRef, useEffect } from 'react'
import { invokeAgent } from '../services/agent'
import { getUserName } from '../services/auth'
import ThemeToggle from './ThemeToggle'
import MarkdownMessage from './MarkdownMessage'

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
  const messagesEndRef = useRef(null)
  const fileInputRef = useRef(null)
  const textareaRef = useRef(null)

  const handleNewConversation = () => {
    const id = crypto.randomUUID()
    sessionStorage.setItem('chat_session_id', id)
    setSessionId(id)
    setMessages([])
    setLoading(false)
    setCookingActive(false)
  }

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
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

    setMessages((prev) => [...prev, { role: 'user', content: displayContent }])
    setInput('')
    setFiles([])
    setLoading(true)

    try {
      // Add a placeholder assistant message that we'll stream into
      setMessages((prev) => [...prev, { role: 'assistant', content: '', isStreaming: true }])

      await invokeAgent(userContent, attachedFiles, sessionId, (event) => {
        switch (event.type) {
          case 'content':
            setCookingActive(false)
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
                updated[updated.length - 1] = { ...last, metrics: event.data }
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
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-brand-500 to-brand-600 shadow-sm">
              <span className="text-lg">🍽️</span>
            </div>
            <div>
              <h1 className="text-base font-semibold text-gray-900 dark:text-white leading-tight">
                Menu Assistant
              </h1>
              <p className="text-xs text-gray-400 dark:text-gray-500">AWS Bedrock-powered menu processing</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
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
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-7xl mx-auto">
          {messages.length === 0 && (
            <div className="text-center mt-24 animate-fade-in">
              <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-brand-500/10 to-brand-600/10 dark:from-brand-500/20 dark:to-brand-600/20 mb-5">
                <span className="text-4xl">🍽️</span>
              </div>
              <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100 mb-2">
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
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`mb-5 flex items-end gap-2.5 animate-slide-up ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              style={{ animationDelay: `${Math.min(i * 50, 200)}ms` }}
            >
              {/* Assistant avatar */}
              {msg.role !== 'user' && (
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-brand-500 to-brand-600 flex items-center justify-center shadow-sm mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
              )}

              <div
                className={`max-w-[75%] rounded-2xl px-4 py-3 transition-all duration-300 hover:shadow-lg hover:-translate-y-0.5 ${
                  msg.role === 'user'
                    ? 'bg-gradient-to-br from-brand-500 to-brand-600 text-white shadow-md shadow-brand-500/15'
                    : msg.isError
                    ? 'bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800/50 text-red-700 dark:text-red-300'
                    : 'bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-gray-800 dark:text-gray-200 shadow-sm'
                }`}
              >
                {msg.role === 'user' ? (
                  <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
                    {msg.content}
                  </pre>
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
              <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-brand-500 to-brand-600 flex items-center justify-center shadow-sm mb-1">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
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

      {/* File preview bar */}
      {files.length > 0 && (
        <div className="sticky bottom-[72px] z-10 border-t border-gray-200 dark:border-gray-800 bg-brand-50/50 dark:bg-brand-950/20 px-4 py-2.5">
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
      <div className="sticky bottom-0 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-4">
        <div className="max-w-7xl mx-auto">
          <div className="flex items-end gap-2 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-2xl px-3 py-2 focus-within:ring-2 focus-within:ring-brand-500/50 focus-within:border-brand-400 dark:focus-within:border-brand-500 transition-all">
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
              className="flex-shrink-0 p-2 bg-brand-500 hover:bg-brand-600 disabled:bg-gray-200 dark:disabled:bg-gray-700 text-white disabled:text-gray-400 dark:disabled:text-gray-500 rounded-xl transition-all duration-200 hover:shadow-md hover:shadow-brand-500/25 disabled:shadow-none"
              aria-label="Send message"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 19V5m0 0l-7 7m7-7l7 7" />
              </svg>
            </button>
          </div>

          <p className="text-xs text-center text-gray-400 dark:text-gray-600 mt-2.5">
            Drag & drop files or use the attach button. Supports PDF, JPG, PNG, HEIC, TIFF, WEBP.
          </p>
        </div>
      </div>
    </div>
  )
}

export default ChatView
