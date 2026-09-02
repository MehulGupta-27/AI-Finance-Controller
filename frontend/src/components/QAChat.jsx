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
  await new Promise(r => setTimeout(r, 600 + Math.random() * 500))

  const q = question.toLowerCase()

  // Greetings / small talk
  if (q.match(/^(hey|hi|hello|hiya|yo|sup|good\s*(morning|afternoon|evening))(\s+\w+)?[!?.]*$/) ||
      q.length < 5) {
    return {
      answer: "Hi! I can help you understand your reconciliation results. You can ask me things like:\n\n- How many payments are waiting for bank settlement?\n- Are there any unexplained bank credits?\n- Show me gym membership payments\n- How many transactions need review?\n\nWhat would you like to know?",
      records: []
    }
  }

  // How many / count questions about status
  if (q.match(/how many|how much|count|total|number of|summary|overview/)) {
    return {
      answer: "Here's a summary of the current reconciliation run:\n\n• 97 payments fully reconciled — no action needed\n• 8 payments in progress — mostly waiting for bank deposits to arrive\n• 5 payments need your review — the system wasn't confident enough to decide automatically\n\nOut of 110 payments processed, 88% were handled automatically with no manual work.",
      records: [
        { status: "PARTIAL",    sub_reason: "awaiting_settlement",    amount: 7324.76, date: "30 Mar", customer: "Vikram Iyer" },
        { status: "UNRESOLVED", sub_reason: "low_confidence",         amount: 7592.50, date: "20 Jan", customer: "Rahul Sharma" },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit", amount: 1452.71, date: "14 Feb", customer: null },
      ]
    }
  }

  // Pending / waiting / settlement
  if (q.match(/wait|pending|settling|settlement|not arrived|deposit|bank.*transfer|transfer.*bank|on.*way/)) {
    return {
      answer: "There are 5 payments currently waiting for bank settlement. The most recent is from Vikram Iyer (Rs.7,324.76, paid 30 Mar) — 3 days since payment was captured. All are within the normal 1–10 day window, so no action is needed yet. They will auto-resolve once the bank deposits arrive.",
      records: [
        { status: "PARTIAL", sub_reason: "awaiting_settlement", amount: 7324.76, date: "30 Mar", customer: "Vikram Iyer" },
        { status: "PARTIAL", sub_reason: "awaiting_settlement", amount: 5089.03, date: "23 Mar", customer: "Sneha Gupta" },
        { status: "PARTIAL", sub_reason: "awaiting_settlement", amount: 5061.42, date: "30 Mar", customer: "Arjun Kumar" },
      ]
    }
  }

  // Review / unresolved / unclear / flag / attention / check / manual
  if (q.match(/review|unresolved|unclear|flag|attention|check|manual|problem|issue|concern|worry|action|need.*look|look.*need|for review|to review|outstanding|open|exception/)) {
    return {
      answer: "5 transactions need your attention:\n\n• Rs.7,592.50 from Rahul Sharma (20 Jan) — the AI found a probable match but wasn't confident enough to confirm automatically. Amount and timing look right, but the bank description was too vague.\n• Rs.1,452.71 on 14 Feb — an unexplained bank credit labelled 'BANK REVERSAL FEES' with no matching customer payment.\n• 3 more are waiting for overdue bank deposits.",
      records: [
        { status: "UNRESOLVED", sub_reason: "low_confidence",            amount: 7592.50, date: "20 Jan", customer: "Rahul Sharma" },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit",  amount: 1452.71, date: "14 Feb", customer: null },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit",  amount:  729.08, date: "18 Feb", customer: null },
      ]
    }
  }

  // Gym / membership / fitzone / fitness / personal training
  if (q.match(/gym|membership|fitzone|fitness|training|wellness|fzw/)) {
    return {
      answer: "Found 3 gym membership payments, all fully reconciled. These were trickier to match than usual — the bank statement showed 'FITZONE WELLNESS PVT LTD' (your legal company name) instead of 'FitZone Gym'. The system recognised this as your own registered name and confirmed the match using that context.\n\n• Meera Reddy — Rs.2,103.73 (1 Mar) — Monthly renewal\n• Arjun Singh — Rs.2,223.00 (15 Mar) — Personal training package\n• Kiran Patel — Rs.2,707.00 (22 Mar) — Annual premium upgrade",
      records: [
        { status: "MATCHED", sub_reason: null, amount: 2103.73, date: "1 Mar",  customer: "Meera Reddy",  notes: "Monthly gym membership renewal" },
        { status: "MATCHED", sub_reason: null, amount: 2223.00, date: "15 Mar", customer: "Arjun Singh",  notes: "Personal training package - 10 sessions" },
        { status: "MATCHED", sub_reason: null, amount: 2707.00, date: "22 Mar", customer: "Kiran Patel",  notes: "Annual premium membership upgrade" },
      ]
    }
  }

  // Unexplained / unidentified / mystery / bank credit / strange / weird / unknown
  if (q.match(/unexplained|unidentified|mystery|strange|weird|unknown|random|extra|reversal|interest|credit(?! card)/)) {
    return {
      answer: "Found 5 unexplained bank credits with no matching customer payment or Razorpay transaction. These could be bank fee reversals, quarterly interest payouts, or misdirected transfers. You should contact your bank to identify each one and then record it against the correct account.",
      records: [
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit", amount: 1452.71, date: "14 Feb", customer: null, narration: "BANK REVERSAL FEES" },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit", amount:  729.08, date: "18 Feb", customer: null, narration: "BANK CHG REVERSAL" },
        { status: "UNRESOLVED", sub_reason: "unidentified_bank_credit", amount: 1100.11, date: "20 Feb", customer: null, narration: "MISC CR ADJ" },
      ]
    }
  }

  // Failed / declined / rejected payments
  if (q.match(/fail|declin|reject|cancel|not.*paid|didn.*pay|bounce/)) {
    return {
      answer: "Found 5 failed payment attempts. These were all card declines — no money moved, so there's nothing to reconcile. They've been correctly closed with no action needed.",
      records: [
        { status: "MATCHED", sub_reason: "no_action_needed", amount: 899.00,  date: "10 Feb", customer: "Arjun Singh" },
        { status: "MATCHED", sub_reason: "no_action_needed", amount: 1250.00, date: "15 Feb", customer: "Priya Mehta" },
      ]
    }
  }

  // Refund / partial / partially
  if (q.match(/refund|partial|partly|part.*pay|return/)) {
    return {
      answer: "Found 5 partial refund transactions. All have been reconciled correctly — the system calculated the expected bank deposit as the original charge minus the Razorpay fee minus the refund amount, and matched each one exactly.",
      records: [
        { status: "MATCHED", sub_reason: null, amount: 725.50,  date: "12 Jan", customer: "Rahul Verma",   notes: "Partial refund of Rs.363.65 applied" },
        { status: "MATCHED", sub_reason: null, amount: 3097.31, date: "19 Jan", customer: "Ananya Sharma", notes: "Partial refund of Rs.1,577.93 applied" },
      ]
    }
  }

  // High value / large amount
  if (q.match(/high.?value|large|big|above|over|more than|greater|50.?000|₹50/)) {
    return {
      answer: "No transactions above Rs.50,000 were found in this dataset. Any transaction above that threshold is automatically sent to manual review regardless of how confident the system is — it's a hard rule with no exceptions.",
      records: []
    }
  }

  // Specific customer name lookup
  const knownCustomers = ["rahul", "priya", "meera", "arjun", "vikram", "sneha", "kiran", "ravi", "ananya"]
  const customerMatch = knownCustomers.find(name => q.includes(name))
  if (customerMatch) {
    const name = customerMatch.charAt(0).toUpperCase() + customerMatch.slice(1)
    return {
      answer: `Found payments associated with "${name}". Showing the most relevant results below. Click any row in the Transactions tab for full details.`,
      records: [
        { status: "MATCHED", sub_reason: null, amount: 5984.09, date: "1 Feb", customer: name + " (sample)" },
      ]
    }
  }

  // What / explain / tell me — general help
  if (q.match(/what|explain|tell|show|list|give|help|how does|what is|what are/)) {
    return {
      answer: "Here's what I can tell you about this reconciliation run:\n\n• 110 payments were processed in total\n• 97 are fully reconciled (88.2%)\n• 8 are in progress — mostly waiting for bank deposits\n• 5 need your review\n\nYou can ask me about specific types: pending settlements, unexplained bank credits, gym membership payments, failed payments, or partial refunds. Or search by a customer's name.",
      records: []
    }
  }

  // Default — still try to be helpful
  return {
    answer: `I looked through the reconciled payments for "${question}" but didn't find a specific match.\n\nHere are things you can ask me:\n• Payments waiting for bank settlement\n• Transactions that need your review\n• Gym membership or specific purchase types\n• Unexplained bank credits\n• Failed or declined payments\n• Partial refunds`,
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
