import { useState } from 'react'
import Dashboard from './components/Dashboard.jsx'
import ReviewQueue from './components/ReviewQueue.jsx'
import RecordDetail from './components/RecordDetail.jsx'
import QAChat from './components/QAChat.jsx'
import styles from './App.module.css'

// Sample data — in production this comes from the reconciliation engine
const MOCK_RESULTS = buildSampleData()

function buildSampleData() {
  const summary = {
    records_processed: 110,
    matched: 97,
    partial: 8,
    unresolved: 5,
    match_rate: 88.2,
    processing_time_s: 83.4,
    llm_calls: 24,
    no_llm_pct: 78,
    as_of_date: '2 April 2026',
  }

  const records = [
    // Fully reconciled
    {
      record_id: 'led-001', status: 'MATCHED', sub_reason: null, confidence: 1.0, source: 'exact',
      amount: 5984.09, customer: 'Rahul Sharma', order_id: 'ORD770487', date: '1 Feb 2026',
      explanation: {
        headline: 'Fully reconciled',
        summary: 'This payment was found in your order book, confirmed by Razorpay, and the bank deposit matches exactly.',
        checklist: [
          { passed: true, label: 'Order ORD770487 found in your records' },
          { passed: true, label: 'Razorpay confirmed the payment of Rs.5,984.09' },
          { passed: true, label: 'Bank deposit matches after Razorpay fee deduction' },
        ],
        flags: [], days_elapsed: null, next_step: null, confidence: 1.0,
      },
    },
    {
      record_id: 'led-002', status: 'MATCHED', sub_reason: null, confidence: 0.87, source: 'fuzzy',
      amount: 1105.80, customer: 'Priya Patel', order_id: 'ORD334521', date: '15 Jan 2026',
      explanation: {
        headline: 'Reconciled — bank description was garbled',
        summary: 'The bank statement showed only a code ("TXN5530") with no customer name. The system matched it using the payment amount and deposit date.',
        checklist: [
          { passed: true,  label: 'Payment amount matches exactly: Rs.1,105.80' },
          { passed: true,  label: 'Bank deposit arrived 3 days after payment — normal' },
          { passed: false, label: 'Bank description was unreadable ("TXN5530") — matched on amount + date instead' },
        ],
        flags: [], days_elapsed: null, next_step: null, confidence: 0.87,
      },
    },
    {
      record_id: 'led-003', status: 'MATCHED', sub_reason: 'no_action_needed', confidence: 1.0, source: 'exact',
      amount: 899.00, customer: 'Arjun Singh', order_id: 'ORD555052', date: '10 Feb 2026',
      explanation: {
        headline: 'Payment failed — nothing to reconcile',
        summary: "Arjun's card was declined. No money moved, so there's nothing to match against a bank deposit. This is correctly closed.",
        checklist: [
          { passed: true, label: "Card payment was declined — no money was charged" },
          { passed: true, label: 'No Razorpay capture and no bank deposit expected' },
        ],
        flags: [], days_elapsed: null, next_step: null, confidence: 1.0,
      },
    },
    {
      record_id: 'led-004', status: 'MATCHED', sub_reason: null, confidence: 0.95, source: 'llm',
      amount: 2103.73, customer: 'Meera Reddy', order_id: 'ORD621445', date: '1 Mar 2026',
      notes: 'Monthly gym membership renewal',
      explanation: {
        headline: 'Reconciled — bank used your company\'s legal name',
        summary: 'The bank deposit said "FITZONE WELLNESS PVT LTD" instead of "FitZone Gym". The system recognised this as your own registered company name and confirmed the match.',
        checklist: [
          { passed: true, label: 'Payment amount matches after fee: Rs.2,103.73' },
          { passed: true, label: 'Bank deposit arrived 1 day after payment — normal' },
          { passed: true, label: 'Bank used your legal name "FITZONE WELLNESS PVT LTD" — confirmed as your own settlement' },
          { passed: true, label: 'Independently verified by a second AI check' },
        ],
        flags: [], days_elapsed: null, next_step: null, confidence: 0.95,
      },
    },

    // Partially reconciled
    {
      record_id: 'led-005', status: 'PARTIAL', sub_reason: 'awaiting_settlement', confidence: 0.90, source: 'direct',
      amount: 7324.76, customer: 'Vikram Iyer', order_id: 'ORD887341', date: '30 Mar 2026',
      explanation: {
        headline: 'Waiting for bank to deposit',
        summary: "Vikram's payment went through on Razorpay 3 days ago. Banks typically take 1–5 days to transfer the money. Nothing is wrong — check back in a day or two.",
        checklist: [
          { passed: true,  label: 'Your order book confirms the sale' },
          { passed: true,  label: 'Razorpay confirmed the payment of Rs.7,324.76' },
          { passed: false, label: 'Bank deposit not yet received (3 days since payment)' },
        ],
        flags: [], days_elapsed: 3, next_step: 'No action needed yet — the bank deposit will arrive soon and this will auto-close.', confidence: 0.90,
      },
    },
    {
      record_id: 'rzp-001', status: 'PARTIAL', sub_reason: 'no_ledger_record', confidence: 0.85, source: 'fuzzy',
      amount: 4200.00, customer: 'Unknown', order_id: 'ORD943718', date: '28 Jan 2026',
      explanation: {
        headline: 'Money received but no order on record',
        summary: 'Razorpay captured a payment of Rs.4,200 and the bank deposit has arrived — but there is no matching order in your system. This usually happens when a payment is processed directly through the dashboard, bypassing the normal checkout.',
        checklist: [
          { passed: true,  label: 'Razorpay shows payment of Rs.4,200.00 captured' },
          { passed: true,  label: 'Bank deposit of Rs.4,101.12 received (after Razorpay fee)' },
          { passed: false, label: 'No matching order found in your records' },
        ],
        flags: [], days_elapsed: null, next_step: 'Ask your team to check if this was a manual payment or offline order. Add it to your order book if so.', confidence: 0.85,
      },
    },

    // Needs your attention
    {
      record_id: 'bank-001', status: 'UNRESOLVED', sub_reason: 'unidentified_bank_credit', confidence: 0.0, source: 'direct',
      amount: 1452.71, customer: null, order_id: null, date: '14 Feb 2026', narration: 'BANK REVERSAL FEES',
      explanation: {
        headline: 'Unexplained bank credit',
        summary: 'Your bank account received Rs.1,452.71 with the description "BANK REVERSAL FEES". This does not match any customer payment or Razorpay settlement. It could be a bank fee refund, interest payout, or a misdirected transfer.',
        checklist: [
          { passed: false, label: 'No matching customer order found' },
          { passed: false, label: 'No matching Razorpay payment found' },
          { passed: true,  label: 'Bank credited Rs.1,452.71 on 14 Feb with description "BANK REVERSAL FEES"' },
        ],
        flags: ['Unexplained money received'], days_elapsed: null, next_step: 'Contact your bank to find out what this credit is for, then record it against the correct account.', confidence: 0.0,
      },
    },
    {
      record_id: 'led-006', status: 'UNRESOLVED', sub_reason: 'low_confidence', confidence: 0.82, source: 'llm',
      amount: 7592.50, customer: 'Rahul Sharma', order_id: 'ORD112233', date: '20 Jan 2026',
      explanation: {
        headline: 'Likely a match — but needs your confirmation',
        summary: "The system found a probable match for this Rs.7,592.50 payment from Rahul Sharma. The amount and timing line up, but the bank description was too vague to confirm automatically. Please review and confirm if this looks right.",
        checklist: [
          { passed: true,  label: 'Payment amount and expected bank deposit match' },
          { passed: true,  label: 'Bank deposit arrived within the normal window (7 days)' },
          { passed: false, label: "Bank description was too generic to confirm automatically" },
        ],
        flags: [], days_elapsed: 7, next_step: 'Review the details below and confirm if this is the correct match, or mark it as no match.', confidence: 0.82,
      },
    },
  ]

  return { summary, records }
}

export default function App() {
  const [activeTab, setActiveTab]       = useState('dashboard')
  const [selectedRecord, setSelectedRecord] = useState(null)
  const [data, setData]                 = useState(MOCK_RESULTS)
  const [actionLog, setActionLog]       = useState([])

  function handleAction(recordId, action, note = '') {
    setData(prev => {
      const records = prev.records.map(r => {
        if (r.record_id !== recordId) return r
        if (action === 'approve')     return { ...r, status: 'MATCHED',    _human_action: 'Confirmed as reconciled' }
        if (action === 'reject')      return { ...r, status: 'UNRESOLVED', _human_action: 'Confirmed — no match', _confirmed_no_match: true }
        if (action === 'manual_link') return { ...r, status: 'MATCHED',    _human_action: 'Manually linked', _note: note }
        return r
      })
      const matched    = records.filter(r => r.status === 'MATCHED').length
      const partial    = records.filter(r => r.status === 'PARTIAL').length
      const unresolved = records.filter(r => r.status === 'UNRESOLVED').length
      return {
        ...prev, records,
        summary: { ...prev.summary, matched, partial, unresolved,
          match_rate: +(matched / prev.summary.records_processed * 100).toFixed(1) },
      }
    })
    setActionLog(log => [{ ts: new Date().toLocaleTimeString(), recordId, action, note }, ...log.slice(0, 49)])
    setSelectedRecord(null)
  }

  const needsReview = data.records.filter(r => r.status === 'UNRESOLVED' && !r._confirmed_no_match)

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandIcon}>💳</span>
          <span>Reconciliation Dashboard</span>
        </div>
        <nav className={styles.nav}>
          <button className={`${styles.navBtn} ${activeTab === 'dashboard' ? styles.navActive : ''}`}
            onClick={() => setActiveTab('dashboard')}>Overview</button>
          <button className={`${styles.navBtn} ${activeTab === 'review' ? styles.navActive : ''}`}
            onClick={() => setActiveTab('review')}>
            Needs Review {needsReview.length > 0 && <span className={styles.badge}>{needsReview.length}</span>}
          </button>
          <button className={`${styles.navBtn} ${activeTab === 'all' ? styles.navActive : ''}`}
            onClick={() => setActiveTab('all')}>All Transactions</button>
          <button className={`${styles.navBtn} ${activeTab === 'qa' ? styles.navActive : ''}`}
            onClick={() => setActiveTab('qa')}>Ask a Question</button>
        </nav>
      </header>

      <main className={styles.main}>
        {activeTab === 'dashboard' && <Dashboard summary={data.summary} actionLog={actionLog} />}
        {activeTab === 'review'    && <ReviewQueue records={needsReview} onSelect={setSelectedRecord} />}
        {activeTab === 'all'       && <ReviewQueue records={data.records} onSelect={setSelectedRecord} showAll />}
        {activeTab === 'qa'        && <QAChat />}
      </main>

      {selectedRecord && (
        <RecordDetail record={selectedRecord} onAction={handleAction} onClose={() => setSelectedRecord(null)} />
      )}
    </div>
  )
}
