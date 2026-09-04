import styles from './ReviewQueue.module.css'

const STATUS_COLOR  = { MATCHED: '#16a34a', PARTIAL: '#d97706', UNRESOLVED: '#dc2626' }
const STATUS_BG     = { MATCHED: '#dcfce7', PARTIAL: '#fef3c7', UNRESOLVED: '#fee2e2' }

const SUB_REASON_LABEL = {
  no_action_needed:           'Payment failed — no money moved',
  awaiting_settlement:        'Waiting for bank to deposit the money',
  no_ledger_record:           'Money received — but no order was recorded in your system',
  overdue_settlement:         'Bank deposit is late',
  agent_disagreement:         'System checks disagreed — need your call',
  low_confidence:             'Looks like a match — but not 100% sure',
  high_value_review_required: 'Large amount — needs your sign-off',
  unidentified_bank_credit:   'Money appeared in bank — source unknown',
  no_candidates_found:        'No match found in any system',
}

export default function ReviewQueue({ records, onSelect, showAll = false }) {
  const sorted = [...records].sort((a, b) => {
    // Unresolved first, then partial, then matched; within each group sort by amount desc
    const priority = { UNRESOLVED: 0, PARTIAL: 1, MATCHED: 2 }
    if (priority[a.status] !== priority[b.status])
      return priority[a.status] - priority[b.status]
    return (b.amount || 0) - (a.amount || 0)
  })

  if (sorted.length === 0)
    return <p style={{ color: '#888', padding: '40px 0', textAlign: 'center' }}>
      No records in this queue.
    </p>

  return (
    <div className={styles.page}>
      <div className={styles.headerRow}>
        <h1 className={styles.title}>
          {showAll ? `All Records (${sorted.length})` : `Review Queue (${sorted.length})`}
        </h1>
        <p className={styles.hint}>Click any row to see full explanation and actions</p>
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Status</th>
              <th>What happened</th>
              <th>Amount (Rs.)</th>
              <th>Customer</th>
              <th>Date</th>
              <th>Confidence</th>
              <th>How resolved</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(r => (
              <tr key={r.record_id} className={styles.row} onClick={() => onSelect(r)}>
                <td>
                  <span className={styles.badge}
                    style={{ background: STATUS_BG[r.status], color: STATUS_COLOR[r.status] }}>
                    {r.status === 'MATCHED'    ? 'Reconciled'
                   : r.status === 'PARTIAL'    ? 'In Progress'
                   :                             'Needs Review'}
                    {r._human_action && <span className={styles.humanTag}> (you reviewed)</span>}
                  </span>
                </td>
                <td className={styles.subReason}>
                  {r.sub_reason ? SUB_REASON_LABEL[r.sub_reason] || r.sub_reason : '—'}
                </td>
                <td className={styles.amount}>
                  {r.amount ? r.amount.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '—'}
                </td>
                <td>{r.customer || r.narration || '—'}</td>
                <td className={styles.date}>{r.date || '—'}</td>
                <td className={styles.confidence}>
                  {r.confidence > 0
                    ? <ConfBar value={r.confidence} />
                    : <span style={{ color: '#ccc' }}>—</span>}
                </td>
                <td className={styles.source}>
                  {r.source === 'exact'  ? 'Order ID match'
                 : r.source === 'fuzzy'  ? 'Amount & date'
                 : r.source === 'llm'    ? 'AI verified'
                 :                         r.source}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ConfBar({ value }) {
  const pct = Math.round(value * 100)
  const color = pct >= 85 ? '#16a34a' : pct >= 70 ? '#d97706' : '#dc2626'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 60, height: 6, background: '#e5e7eb', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 12, color, fontWeight: 600 }}>{pct}%</span>
    </div>
  )
}
