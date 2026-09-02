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

// Mock Q&A handler — in production this calls GET /api/qa?q=...
// which routes to agents/qa_agent.py
async function askQuestion(question, filters) {
  await new Promise(r => setTimeout(r, 800 + Math.random() * 600))

  const q = question.toLowerCase()

  if (q.includes("waiting") || q.includes("pending") || q.includes("settlement")) {
    return {
      answer: "There are 5 payments currently waiting for bank settlement. The most recent is from Vikram Iyer (Rs.7,324.76, paid 30 Mar) — it has been 3 days since payment was captured. All are within the normal 1–10 day window, so no action is needed yet.",
      records: [
        { status: "PARTIAL", sub_reason: "awaiting_settlement", amount: 7324.76, date: "30 Mar", customer: "Vikram Iyer" },
        { status: "PARTIAL", sub_reason: "awaiting_settlement", amount: 5089.03, date: "23 Mar", customer: "Sneha Gupta" },
      ]
    }
  }

  if (q.includes("unresolved") || q.includes("needs review") || q.includes("unclear")) {
    return {
      answer: "There are 5 transactions that need your review:\n\n• Rs.7,592.50 from Rahul Sharma (20 Jan) — probable match but bank description was too vague to confirm automatically\n• Rs.1,452.71 on 14 Feb — an unexplained bank credit labelled 'BANK REVERSAL FEES'\n• 3 others waiting for bank deposit",
      records: [
        { status: "UNRESOLVED", sub_reason: "low_confidence",            amount: 7592.50, date: "20 Jan", customer: "Rahul Sharma" },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit",  amount: 1452.71, date: "14 Feb", customer: null },
      ]
    }
  }

  if (q.includes("gym") || q.includes("membership") || q.includes("fitzone")) {
    return {
      answer: "Found 3 gym membership payments. These were matched even though the bank statement showed 'FITZONE WELLNESS PVT LTD' instead of 'FitZone Gym' — the system recognised this as your registered legal name and confirmed the match.\n\n• Meera Reddy — Rs.2,103.73 (1 Mar) — Monthly renewal\n• Arjun Singh — Rs.2,223.00 (15 Mar) — Personal training package\n• Kiran Patel — Rs.2,707.00 (22 Mar) — Annual premium upgrade",
      records: [
        { status: "MATCHED", sub_reason: null, amount: 2103.73, date: "1 Mar",  customer: "Meera Reddy",  notes: "Monthly gym membership renewal" },
        { status: "MATCHED", sub_reason: null, amount: 2223.00, date: "15 Mar", customer: "Arjun Singh",  notes: "Personal training package - 10 sessions" },
        { status: "MATCHED", sub_reason: null, amount: 2707.00, date: "22 Mar", customer: "Kiran Patel",  notes: "Annual premium membership upgrade" },
      ]
    }
  }

  if (q.includes("unexplained") || q.includes("unidentified") || q.includes("mystery") || q.includes("credit")) {
    return {
      answer: "Found 5 unexplained bank credits that have no matching customer payment or Razorpay transaction. The largest is Rs.1,452.71 on 14 Feb labelled 'BANK REVERSAL FEES'. You should contact your bank to identify the source of each credit.",
      records: [
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit", amount: 1452.71, date: "14 Feb", customer: null, narration: "BANK REVERSAL FEES" },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit", amount:  729.08, date: "18 Feb", customer: null, narration: "BANK CHG REVERSAL" },
      ]
    }
  }

  if (q.includes("high") || q.includes("large") || q.includes("50000") || q.includes("₹50")) {
    return {
      answer: "No transactions above ₹50,000 were found in this dataset. All high-value transactions above that threshold would automatically require manual sign-off before being marked as reconciled, regardless of how confident the system is.",
      records: []
    }
  }

  return {
    answer: `I searched the reconciled records for "${question}" but couldn't find a specific match. Try asking about: pending settlements, unresolved transactions, gym membership payments, or unexplained bank credits.`,
    records: []
  }
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
      const result = await askQuestion(q)
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
