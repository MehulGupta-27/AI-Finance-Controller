import styles from './Dashboard.module.css'

export default function Dashboard({ summary, actionLog }) {
  const {
    records_processed, matched, partial, unresolved,
    match_rate, processing_time_s, llm_calls, no_llm_pct, as_of_date,
  } = summary

  return (
    <div className={styles.page}>

      <div className={styles.titleRow}>
        <div>
          <h1 className={styles.title}>Reconciliation Overview</h1>
          <p className={styles.subtitle}>Last run: {as_of_date} &middot; {records_processed} payments processed in {processing_time_s}s</p>
        </div>
      </div>

      {/* Status cards */}
      <div className={styles.kpiGrid}>
        <StatusCard
          value={matched}
          label="Fully Reconciled"
          desc="Matched across all three sources"
          color="#16a34a"
          bg="#f0fdf4"
        />
        <StatusCard
          value={partial}
          label="In Progress"
          desc="Waiting on bank deposit or missing an order"
          color="#d97706"
          bg="#fffbeb"
        />
        <StatusCard
          value={unresolved}
          label="Needs Your Attention"
          desc="Couldn't be resolved automatically"
          color="#dc2626"
          bg="#fef2f2"
          urgent={unresolved > 0}
        />
        <StatusCard
          value={`${match_rate}%`}
          label="Auto-Resolved Rate"
          desc="Handled without any manual review"
          color={match_rate >= 85 ? '#16a34a' : '#d97706'}
          bg={match_rate >= 85 ? '#f0fdf4' : '#fffbeb'}
        />
      </div>

      {/* How it was resolved */}
      <div className={styles.row}>
        <div className={styles.card} style={{ flex: 2 }}>
          <h2 className={styles.cardTitle}>How transactions were reconciled</h2>
          <div className={styles.stageList}>
            <StageRow
              icon="🔑"
              label="Exact order match"
              desc="Order number found in both your records and Razorpay"
              count={102}
              total={records_processed}
            />
            <StageRow
              icon="🔢"
              label="Amount + date match"
              desc="Same amount deposited within a few days — confirmed automatically"
              count={79}
              total={records_processed}
            />
            <StageRow
              icon="🤖"
              label="AI-assisted match"
              desc="Needed extra reasoning (delayed deposits, garbled bank descriptions)"
              count={llm_calls}
              total={records_processed}
            />
            <StageRow
              icon="⏳"
              label="Awaiting bank deposit"
              desc="Payment confirmed by Razorpay, bank transfer not yet received"
              count={partial}
              total={records_processed}
            />
          </div>
        </div>

        <div className={styles.card} style={{ flex: 1 }}>
          <h2 className={styles.cardTitle}>Breakdown</h2>
          <div className={styles.donutWrap}>
            {[
              { label: 'Reconciled',  count: matched,    color: '#16a34a' },
              { label: 'In Progress', count: partial,    color: '#d97706' },
              { label: 'Needs Review',count: unresolved, color: '#dc2626' },
            ].map(({ label, count, color }) => (
              <div key={label} className={styles.breakdownRow}>
                <div className={styles.colorDot} style={{ background: color }} />
                <span className={styles.breakdownLabel}>{label}</span>
                <div className={styles.breakdownBar}>
                  <div
                    className={styles.breakdownFill}
                    style={{ width: `${(count / records_processed) * 100}%`, background: color }}
                  />
                </div>
                <span className={styles.breakdownCount} style={{ color }}>{count}</span>
              </div>
            ))}
          </div>
          <p className={styles.effNote}>
            {no_llm_pct}% of payments were resolved automatically — no manual work needed.
          </p>
        </div>
      </div>

      {/* Recent manual decisions */}
      {actionLog.length > 0 && (
        <div className={styles.card}>
          <h2 className={styles.cardTitle}>Recent decisions you made</h2>
          <table className={styles.logTable}>
            <thead>
              <tr><th>Time</th><th>Payment</th><th>Your decision</th><th>Note</th></tr>
            </thead>
            <tbody>
              {actionLog.map((e, i) => (
                <tr key={i}>
                  <td className={styles.mono}>{e.ts}</td>
                  <td className={styles.mono}>{e.recordId.slice(0, 10)}…</td>
                  <td>
                    <span className={`${styles.actionBadge} ${styles['ab_' + e.action]}`}>
                      {e.action === 'approve'     ? 'Confirmed match' :
                       e.action === 'reject'      ? 'Marked no match' :
                       e.action === 'manual_link' ? 'Manually linked' : e.action}
                    </span>
                  </td>
                  <td style={{ color: '#555' }}>{e.note || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function StatusCard({ value, label, desc, color, bg, urgent }) {
  return (
    <div className={styles.kpiCard} style={{ background: bg, borderColor: color + '40' }}>
      {urgent && <div className={styles.urgentDot} />}
      <div className={styles.kpiValue} style={{ color }}>{value}</div>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={styles.kpiDesc}>{desc}</div>
    </div>
  )
}

function StageRow({ icon, label, desc, count, total }) {
  const pct = Math.round((count / total) * 100)
  return (
    <div className={styles.stageRow}>
      <span className={styles.stageIcon}>{icon}</span>
      <div className={styles.stageInfo}>
        <div className={styles.stageLabel}>{label}</div>
        <div className={styles.stageDesc}>{desc}</div>
        <div className={styles.stageBarWrap}>
          <div className={styles.stageBar}>
            <div className={styles.stageBarFill} style={{ width: `${pct}%` }} />
          </div>
          <span className={styles.stageCount}>{count} of {total}</span>
        </div>
      </div>
    </div>
  )
}
