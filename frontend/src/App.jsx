import { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard.jsx'
import ReviewQueue from './components/ReviewQueue.jsx'
import RecordDetail from './components/RecordDetail.jsx'
import QAChat from './components/QAChat.jsx'
import styles from './App.module.css'

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [selectedRecord, setSelectedRecord] = useState(null)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionLog, setActionLog] = useState([])

  // Fetch real data from backend on mount
  useEffect(() => {
    fetch('http://localhost:8000/api/summary')
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(json => {
        setData(json)
        setLoading(false)
      })
      .catch(err => {
        console.error('Failed to load data:', err)
        setError(err.message)
        setLoading(false)
      })
  }, [])

  if (loading) {
    return (
      <div className={styles.app}>
        <header className={styles.header}>
          <h1>AI Finance Controller</h1>
        </header>
        <div style={{ padding: '40px', textAlign: 'center', color: '#888' }}>
          Loading pipeline results...
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={styles.app}>
        <header className={styles.header}>
          <h1>AI Finance Controller</h1>
        </header>
        <div style={{ padding: '40px', textAlign: 'center', color: '#dc2626' }}>
          <strong>Error loading data:</strong> {error}
          <br /><br />
          Make sure the backend is running: <code>python api/main.py</code>
        </div>
      </div>
    )
  }

  const needsReview = data.records.filter(r => r.status === 'UNRESOLVED')

  function handleAction(recordId, action, note = '') {
    const record = data.records.find(r => r.record_id === recordId)
    if (!record) return

    // Send to backend
    fetch('http://localhost:8000/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_id: recordId, action, note }),
    }).catch(err => console.error('Action API failed:', err))

    // Update UI
    const timestamp = new Date().toLocaleString('en-IN', { hour12: false })
    setActionLog(prev => [{
      timestamp,
      record_id: recordId,
      action,
      note,
      customer: record.customer || '',
      amount: record.amount,
    }, ...prev])

    const updated = data.records.map(r => {
      if (r.record_id !== recordId) return r
      let status = r.status
      let sub_reason = r.sub_reason
      if (action === 'approve') {
        status = 'MATCHED'
        sub_reason = null
      } else if (action === 'reject') {
        sub_reason = 'confirmed_no_match'
      }
      return { ...r, status, sub_reason, _human_action: action, _note: note }
    })

    const matched    = updated.filter(r => r.status === 'MATCHED').length
    const partial    = updated.filter(r => r.status === 'PARTIAL').length
    const unresolved = updated.filter(r => r.status === 'UNRESOLVED').length

    setData({
      ...data,
      records: updated,
      summary: {
        ...data.summary,
        matched,
        partial,
        unresolved,
        match_rate: +(matched / data.summary.records_processed * 100).toFixed(1),
      },
    })
    setSelectedRecord(null)
  }

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <h1>AI Finance Controller</h1>
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
        <RecordDetail
          record={selectedRecord}
          onAction={handleAction}
          onClose={() => setSelectedRecord(null)}
        />
      )}
    </div>
  )
}
