import { useState, useRef, useEffect, useCallback } from 'react'
import MessageBubble from './MessageBubble.jsx'

// ── Constants ──────────────────────────────────────────────────────────────
const LS_MESSAGES     = 'arup_chat_messages'
const LS_SESSION_ID   = 'arup_session_id'
const LS_CLINICIAN_ID = 'arup_clinician_id'

const WELCOME = {
  role: 'assistant',
  data: {
    answer: 'Welcome to the ARUP AI Test Advisor. Upload your ARUP content using the panel on the left, then ask a question such as:\n\n• "What is the recommended ARUP test for antiphospholipid syndrome?"\n• "Specimen requirements for lupus anticoagulant testing"\n• "Which tests should I order to diagnose CMV in an immunocompromised patient?"',
    recommendations: [], citations: [], confidence: null,
    follow_up_questions: [], disclaimer: '', isWelcome: true,
  },
}

// ── Helpers ────────────────────────────────────────────────────────────────
function loadMessages() {
  try {
    const raw = localStorage.getItem(LS_MESSAGES)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length > 0) return parsed
    }
  } catch { /* ignore */ }
  return [WELCOME]
}

function saveMessages(msgs) {
  try {
    // Persist last 40 messages to keep localStorage size manageable
    localStorage.setItem(LS_MESSAGES, JSON.stringify(msgs.slice(-40)))
  } catch { /* ignore quota errors */ }
}

function loadClinicianId() {
  return localStorage.getItem(LS_CLINICIAN_ID) || ''
}

function loadSessionId() {
  return localStorage.getItem(LS_SESSION_ID) || null
}

// ── ClinicianSetup modal ────────────────────────────────────────────────────
function ClinicianSetup({ onSave }) {
  const [name, setName] = useState('')
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(23,23,23,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--white)', borderRadius: 12,
        padding: '32px 36px', width: 380, boxShadow: '0 8px 40px rgba(0,0,0,0.18)',
        border: '1px solid var(--accent3)',
      }}>
        <div style={{ marginBottom: 20 }}>
          <div style={{
            width: 48, height: 48, borderRadius: 8,
            background: 'var(--primary)', display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            marginBottom: 14,
          }}>
            <span style={{ color: '#fff', fontWeight: 700, fontSize: 18 }}>A</span>
          </div>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--dark-sky)' }}>
            ARUP AI Test Advisor
          </h2>
          <p style={{ margin: '8px 0 0', fontSize: 14, color: 'var(--accent2)', lineHeight: 1.5 }}>
            Enter your name or NPI to enable session memory and cross-session context.
          </p>
        </div>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent2)',
                        textTransform: 'uppercase', letterSpacing: '0.04em', display: 'block', marginBottom: 6 }}>
          Your name or NPI
        </label>
        <input
          autoFocus
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && name.trim() && onSave(name.trim())}
          placeholder="e.g. Dr. Smith or NPI 1234567890"
          style={{
            width: '100%', padding: '10px 14px', fontSize: 14,
            border: '1px solid var(--accent)', borderRadius: 6,
            outline: 'none', boxSizing: 'border-box', marginBottom: 16,
            color: 'var(--dark-sky)', background: 'var(--bg-primary)',
          }}
        />
        <button
          className="btn btn-primary"
          style={{ width: '100%', padding: '10px 0', fontSize: 15 }}
          disabled={!name.trim()}
          onClick={() => name.trim() && onSave(name.trim())}
        >
          Start session
        </button>
        <p style={{ margin: '12px 0 0', fontSize: 11, color: 'var(--accent)', textAlign: 'center' }}>
          Stored locally on this device only · No patient PHI should be entered
        </p>
      </div>
    </div>
  )
}

// ── Clinical context pill panel ─────────────────────────────────────────────
// ── Helper: format confirmed_clarifications safely ───────────────────────────
// Supports legacy list[str] and current list[{question, answer}] shapes.
function formatConfirmedClarifications(items) {
  if (!Array.isArray(items) || items.length === 0) return ''
  return items.map(item => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object') {
      const q = (item.question || '').trim()
      const a = (item.answer   || '').trim()
      if (q && a) return `${q} → ${a}`
      return a || q
    }
    return ''
  }).filter(Boolean).join(' · ')
}

function ContextPanel({ context }) {
  const [open, setOpen] = useState(false)
  if (!context) return null

  const patient = context.patient || {}
  const session = context.session || {}

  const hasFacts = patient.age || patient.sex ||
    (patient.relevant_conditions || []).length > 0 ||
    session.primary_concern || (session.tests_ordered || []).length > 0

  if (!hasFacts) return null

  const confirmedText = formatConfirmedClarifications(session.confirmed_clarifications)

  return (
    <div style={{ margin: '0 32px 6px' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: '#EFF6FF', border: '1px solid #BFDBFE',
          borderRadius: 20, padding: '4px 12px', cursor: 'pointer',
          fontSize: 12, color: '#1D4ED8', fontWeight: 600,
        }}
      >
        <span>🧠</span>
        <span>Clinical context active</span>
        <span style={{ fontSize: 10, opacity: 0.7 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{
          marginTop: 6, padding: '12px 16px',
          background: '#F0F9FF', border: '1px solid #BAE6FD',
          borderRadius: 8, fontSize: 12, color: '#0C4A6E', lineHeight: 1.7,
        }}>
          {patient.age && <div><b>Age:</b> {patient.age}</div>}
          {patient.sex && <div><b>Sex:</b> {patient.sex}</div>}
          {(patient.relevant_conditions || []).length > 0 && (
            <div><b>Conditions:</b> {patient.relevant_conditions.join(', ')}</div>
          )}
          {session.primary_concern && (
            <div><b>Concern:</b> {session.primary_concern}</div>
          )}
          {(session.tests_ordered || []).length > 0 && (
            <div><b>Tests ordered:</b> {session.tests_ordered.join(', ')}</div>
          )}
          {confirmedText && (
            <div><b>Confirmed:</b> {confirmedText}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main ChatWindow ─────────────────────────────────────────────────────────
export default function ChatWindow({ api, hasDocuments, onSessionChange }) {
  // Rehydrate from localStorage on mount
  const [messages, setMessages]     = useState(loadMessages)
  const [input, setInput]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [clinicianId, setClinicianId] = useState(loadClinicianId)
  const [sessionId, setSessionId]   = useState(loadSessionId)
  const [showSetup, setShowSetup]   = useState(!loadClinicianId())
  const [clinicalContext, setClinicalContext] = useState(null)

  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  // Persist messages to localStorage whenever they change
  useEffect(() => {
    saveMessages(messages)
  }, [messages])

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Start a new backend session when clinicianId is set and no session exists
  const startSession = useCallback(async (cid) => {
    try {
      const r = await fetch(`${api}/sessions/start?clinician_id=${encodeURIComponent(cid)}`, {
        method: 'POST',
      })
      const d = await r.json()
      const sid = d.session_id
      setSessionId(sid)
      onSessionChange?.(sid)
      localStorage.setItem(LS_SESSION_ID, sid)
      localStorage.setItem(LS_CLINICIAN_ID, cid)
      return sid
    } catch {
      return null
    }
  }, [api])

  const handleClinicianSave = useCallback(async (name) => {
    setClinicianId(name)
    setShowSetup(false)
    await startSession(name)
  }, [startSession])

  // ── Send a query ─────────────────────────────────────────────────────────
  const sendQuery = useCallback(async (queryOverride) => {
    const query = (queryOverride || input).trim()
    if (!query || loading) return

    const history = messages
      .filter(m => m.role !== 'assistant' || m.data?.answer)
      .slice(-10)
      .map(m => ({
        role: m.role,
        content: m.role === 'user' ? m.content : (m.data?.answer || ''),
      }))

    setMessages(prev => [...prev, { role: 'user', content: query }])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch(`${api}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          history,
          session_id:   sessionId,
          clinician_id: clinicianId || 'anonymous',
        }),
      })

      // Parse response safely — backend may return JSON or plain text
      const contentType = res.headers.get('content-type') || ''
      let data
      if (contentType.includes('application/json')) {
        data = await res.json()
      } else {
        const text = await res.text()
        data = { detail: text }
      }

      // Non-2xx response — throw with the detail so the catch block
      // can show a meaningful message instead of a generic connectivity error
      if (!res.ok) {
        const detail = data?.detail || data?.answer || `Backend error ${res.status}`
        throw new Error(`__server__${detail}`)
      }
      if (!data || typeof data !== 'object') {
        throw new Error('__server__Unexpected backend response format')
      }

      setMessages(prev => [...prev, { role: 'assistant', data }])

      // Update the context panel if backend returned structured context
      if (data.clinical_context) {
        setClinicalContext(data.clinical_context)
      }
    } catch (err) {
      // Distinguish server errors (meaningful detail) from network failures
      const isServerError = err.message?.startsWith('__server__')
      const display = isServerError
        ? err.message.replace('__server__', '')
        : 'Could not reach the backend. Please check that the Python server is running and try again. Check the logs/ directory for details.'
      setMessages(prev => [...prev, {
        role: 'assistant',
        data: {
          answer: display,
          recommendations: [], citations: [], confidence: null,
          follow_up_questions: [], disclaimer: '',
        },
      }])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }, [input, loading, messages, sessionId, clinicianId, api])

  // ── Clear conversation ────────────────────────────────────────────────────
  const clearConversation = useCallback(async () => {
    // End the current session in the background
    if (sessionId && messages.length > 1) {
      const history = messages
        .filter(m => !m.data?.isWelcome)
        .slice(-20)
        .map(m => ({
          role: m.role,
          content: m.role === 'user' ? m.content : (m.data?.answer || ''),
        }))
      fetch(`${api}/sessions/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          clinician_id: clinicianId || 'anonymous',
          history,
        }),
      }).catch(() => {})
    }

    // Start a new session
    const newSid = clinicianId ? await startSession(clinicianId) : null
    setSessionId(newSid)
    onSessionChange?.(newSid)

    // Reset UI
    setMessages([WELCOME])
    setClinicalContext(null)
    localStorage.removeItem(LS_MESSAGES)
  }, [sessionId, messages, clinicianId, api, startSession])

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuery() }
  }

  const canSend = input.trim().length > 0 && !loading

  return (
    <>
      {showSetup && <ClinicianSetup onSave={handleClinicianSave} />}

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--bg-salt)' }}>

        {/* Session bar */}
        {clinicianId && (
          <div style={{
            background: 'var(--white)', borderBottom: '1px solid var(--accent3)',
            padding: '6px 32px', display: 'flex', alignItems: 'center',
            gap: 10, flexShrink: 0,
          }}>
            <span style={{ fontSize: 12, color: 'var(--accent2)' }}>
              👤 <b>{clinicianId}</b>
            </span>
            {sessionId && (
              <span style={{ fontSize: 11, color: 'var(--accent)', fontFamily: 'monospace' }}>
                Session: {sessionId.slice(0, 8)}…
              </span>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <button
                className="btn btn-outlined"
                style={{ padding: '3px 12px', fontSize: 12 }}
                onClick={clearConversation}
              >
                New session
              </button>
              <button
                style={{
                  background: 'none', border: 'none',
                  fontSize: 11, color: 'var(--accent)', cursor: 'pointer', padding: '3px 6px',
                }}
                onClick={() => {
                  localStorage.removeItem(LS_CLINICIAN_ID)
                  localStorage.removeItem(LS_SESSION_ID)
                  setClinicianId('')
                  setSessionId(null)
                  onSessionChange?.(null)
                  setShowSetup(true)
                }}
              >
                Switch user
              </button>
            </div>
          </div>
        )}

        {/* Message list */}
        <div style={{
          flex: 1, overflowY: 'auto',
          padding: '24px 32px',
          display: 'flex', flexDirection: 'column', gap: 16,
        }}>
          {messages.map((msg, i) => (
            <MessageBubble
              key={i}
              message={msg}
              onClarificationAnswer={sendQuery}
              onFollowUpDraft={setInput}
            />
          ))}
          {loading && <ThinkingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* Clinical context panel */}
        <ContextPanel context={clinicalContext} />

        {/* No-documents warning */}
        {!hasDocuments && (
          <div style={{
            margin: '0 32px 8px', padding: '10px 16px',
            background: 'var(--info-container)', border: '1px solid var(--info)',
            borderRadius: 'var(--card-radius)', display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontSize: 16 }}>ⓘ</span>
            <span className="text-body-sm" style={{ color: 'var(--accent2)' }}>
              Upload ARUP documents first — the advisor needs a knowledge base before it can answer questions.
            </span>
          </div>
        )}

        {/* Input bar */}
        <div style={{
          background: 'var(--white)', borderTop: '1px solid var(--accent)',
          padding: '14px 32px 18px', flexShrink: 0,
        }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder="Ask about a condition, test name, or specimen requirement…"
              rows={1}
              style={{
                flex: 1, resize: 'none', padding: '10px 16px',
                borderRadius: 'var(--card-radius)', border: '1px solid var(--accent)',
                background: 'var(--bg-primary)', color: 'var(--dark-sky)',
                fontSize: 16, fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                lineHeight: 1.5, maxHeight: 120, overflowY: 'auto',
                transition: 'border-color .15s',
              }}
              onFocus={e  => e.target.style.borderColor = 'var(--primary)'}
              onBlur={e   => e.target.style.borderColor = 'var(--accent)'}
              onInput={e  => {
                e.target.style.height = 'auto'
                e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
              }}
            />
            <button
              className="btn btn-primary"
              onClick={() => sendQuery()}
              disabled={!canSend}
              style={{ flexShrink: 0, padding: '10px 28px' }}
            >
              {loading ? 'Sending…' : 'Send'}
            </button>
          </div>
          <p className="text-caption" style={{ color: 'var(--accent)', marginTop: 8, textAlign: 'center' }}>
            Responses are grounded in your uploaded ARUP content only. Not for independent clinical use.
          </p>
        </div>
      </div>
    </>
  )
}

function ThinkingIndicator() {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
      <div style={{
        background: 'var(--white)', border: '1px solid var(--accent3)',
        borderRadius: '2px 8px 8px 8px', padding: '12px 18px',
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          {[0, 150, 300].map(delay => (
            <span key={delay} style={{
              width: 6, height: 6, borderRadius: '50%',
              background: 'var(--primary)', display: 'inline-block',
              animation: `bounce 1s infinite ${delay}ms`,
            }} />
          ))}
        </div>
        <span className="text-body-sm" style={{ color: 'var(--accent2)' }}>
          Searching ARUP knowledge base…
        </span>
      </div>
      <style>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: .4; }
          30% { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
    </div>
  )
}