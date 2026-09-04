import { useState, useRef, useEffect } from 'react'
import styles from './QAChat.module.css'

// Suggested questions to help new users get started
const SUGGESTIONS = [
  "How many payments are waiting for bank settlement?",
  "Show me unresolved transactions",
  "Are there any gym membership payments?",
  "Any unexplained bank credits?",
  "Show me high-value transactions that need review",
]

// Real Q&A handler — calls GET /api/qa with Agent 9
async function askQuestion(question, filters = {}, conversationHistory = []) {
  const params = new URLSearchParams({ q: question })
  
  // Auto-detect status filter from natural language
  const qLower = question.toLowerCase()
  if (!filters.status) {
    if (qLower.includes('matched') && !qLower.includes('unmatched')) {
      filters.status = 'MATCHED'
    } else if (qLower.includes('partial') || qLower.includes('in progress')) {
      filters.status = 'PARTIAL'
    } else if (qLower.includes('unresolved') || qLower.includes('needs review')) {
      filters.status = 'UNRESOLVED'
    }
  }
  
  if (filters.status) {
    params.append('status', filters.status)
  }
  if (filters.min_amount !== undefined) {
    params.append('min_amount', filters.min_amount.toString())
  }
  if (filters.max_amount !== undefined) {
    params.append('max_amount', filters.max_amount.toString())
  }
  
  // Send conversation history if available
  if (conversationHistory.length > 0) {
    params.append('history', JSON.stringify(conversationHistory))
  }
  
  const response = await fetch(`http://localhost:8000/api/qa?${params}`)
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`)
  }
  
  return await response.json()
}

const STATUS_COLOR = { MATCHED: '#16a34a', PARTIAL: '#d97706', UNRESOLVED: '#dc2626' }
const STATUS_BG    = { MATCHED: '#f0fdf4', PARTIAL: '#fffbeb', UNRESOLVED: '#fef2f2' }
const STATUS_LABEL = { MATCHED: 'Reconciled', PARTIAL: 'In Progress', UNRESOLVED: 'Needs Review' }

export default function QAChat() {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      text: "Hi! I can answer questions about your reconciled payments. Ask me anything — for example, which payments are still pending, whether there are any unexplained bank credits, or to find specific transactions.",
      records: [],
    }
  ])
  const [input, setInput]     = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(question) {
    const q = question.trim()
    if (!q || loading) return

    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: q, records: [] }])
    setLoading(true)

    try {
      // Build conversation history (last 5 exchanges for context)
      const history = messages.slice(-10).map(m => ({
        role: m.role,
        text: m.text
      }))
      
      const result = await askQuestion(q, {}, history)
      setMessages(prev => [...prev, { role: 'assistant', text: result.answer, records: result.records || [] }])
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', text: "Sorry, something went wrong. Please try again.", records: [] }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.titleRow}>
        <div>
          <h1 className={styles.title}>Ask a question</h1>
          <p className={styles.subtitle}>Search your reconciled payments in plain English</p>
        </div>
      </div>

      {/* Suggestions */}
      {messages.length <= 1 && (
        <div className={styles.suggestions}>
          {SUGGESTIONS.map(s => (
            <button key={s} className={styles.suggBtn} onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Chat window */}
      <div className={styles.chatBox}>
        {messages.map((msg, i) => (
          <div key={i} className={`${styles.message} ${styles['msg_' + msg.role]}`}>
            <div className={styles.bubble}>
              <div className={styles.msgText}>
                {msg.text.split('\n').map((line, j) => (
                  <p key={j}>{line}</p>
                ))}
              </div>

              {/* Inline record cards */}
              {msg.records?.length > 0 && (
                <div className={styles.recordCards}>
                  {msg.records.map((r, j) => (
                    <div key={j} className={styles.recordCard}
                      style={{ borderLeftColor: STATUS_COLOR[r.status], background: STATUS_BG[r.status] }}>
                      <div className={styles.rcTop}>
                        <span className={styles.rcStatus} style={{ color: STATUS_COLOR[r.status] }}>
                          {STATUS_LABEL[r.status]}
                        </span>
                        <span className={styles.rcAmount}>Rs.{r.amount?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
                      </div>
                      <div className={styles.rcMeta}>
                        {r.customer && <span>{r.customer}</span>}
                        {r.narration && !r.customer && <span className={styles.rcNarr}>{r.narration}</span>}
                        {r.date && <span>{r.date}</span>}
                        {r.notes && <span className={styles.rcNotes}>{r.notes}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className={`${styles.message} ${styles.msg_assistant}`}>
            <div className={styles.bubble}>
              <div className={styles.typing}>
                <span /><span /><span />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className={styles.inputRow}>
        <input
          className={styles.input}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send(input)}
          placeholder="Ask about your payments…"
          disabled={loading}
          autoFocus
        />
        <button
          className={styles.sendBtn}
          onClick={() => send(input)}
          disabled={!input.trim() || loading}
        >
          Send
        </button>
      </div>
    </div>
  )
}
