import { useState } from 'react'
import styles from './RecordDetail.module.css'

export default function RecordDetail({ record, onAction, onClose }) {
  const [linkNote, setLinkNote] = useState('')
  const [showLinkInput, setShowLinkInput] = useState(false)
  const { explanation, status, sub_reason, confidence, source } = record
  const isActionable = status === 'UNRESOLVED' && !record._confirmed_no_match && !record._human_action

  return (
    <div className={styles.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div className={styles.panel}>
        {/* Header */}
        <div className={styles.panelHeader}>
          <div>
            <div className={styles.headline}>{explanation.headline}</div>
            <div className={styles.meta}>
              Record {record.record_id.slice(0, 24)}
              {record.order_id && <> &middot; Order {record.order_id}</>}
              {record.customer && <> &middot; {record.customer}</>}
              {record.amount && <> &middot; Rs.{record.amount.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</>}
            </div>
          </div>
          <button className={styles.closeBtn} onClick={onClose}>&#x2715;</button>
        </div>

        {/* Checklist — what the system checked */}
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>What we checked</h3>
          <ul className={styles.checklist}>
            {explanation.checklist.map((item, i) => (
              <li key={i} className={`${styles.checkItem} ${item.passed ? styles.pass : styles.fail}`}>
                <span className={styles.checkIcon}>{item.passed ? '✓' : '✗'}</span>
                <span>{item.label}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Risk flags */}
        {explanation.risk_flags?.length > 0 && (
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Risk Flags</h3>
            <div className={styles.flags}>
              {explanation.risk_flags.map(f => (
                <span key={f} className={styles.flag}>{f}</span>
              ))}
            </div>
          </section>
        )}

        {/* Time context */}
        {explanation.days_elapsed != null && (
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Time Context</h3>
            <p className={styles.timeNote}>
              {explanation.days_elapsed} days elapsed since payment captured
            </p>
          </section>
        )}

        {/* Ledger notes (for semantic brand narration records) */}
        {record.notes && (
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Ledger Notes</h3>
            <p className={styles.notesText}>{record.notes}</p>
          </section>
        )}

        {/* Recommendation */}
        {explanation.recommendation && (
          <div className={styles.recommendation}>
            <strong>Recommended action: </strong>{explanation.recommendation}
          </div>
        )}

        {/* Human review actions */}
        {isActionable && (
          <section className={styles.actionsSection}>
            <h3 className={styles.sectionTitle}>Your decision</h3>
            <p className={styles.actionHint}>Your decision is permanently logged. Once confirmed, it cannot be deleted — only the reasoning can be updated.</p>
            <div className={styles.actionBtns}>
              <button className={styles.btnApprove}
                onClick={() => onAction(record.record_id, 'approve')}>
                Yes, this is a match
              </button>
              <button className={styles.btnReject}
                onClick={() => onAction(record.record_id, 'reject')}>
                Not a match
              </button>
              <button className={styles.btnLink}
                onClick={() => setShowLinkInput(v => !v)}>
                Link to a different record
              </button>
            </div>
            {showLinkInput && (
              <div className={styles.linkInput}>
                <input
                  type="text"
                  placeholder="Enter correct record ID or reference..."
                  value={linkNote}
                  onChange={e => setLinkNote(e.target.value)}
                  className={styles.input}
                  autoFocus
                />
                <button className={styles.btnApprove}
                  disabled={!linkNote.trim()}
                  onClick={() => onAction(record.record_id, 'manual_link', linkNote)}>
                  Confirm Link
                </button>
              </div>
            )}
          </section>
        )}

        {/* Already actioned */}
        {record._human_action && (
          <div className={styles.actionedBadge}>
            Human action recorded: <strong>{record._human_action}</strong>
            {record._note && <> — "{record._note}"</>}
          </div>
        )}
      </div>
    </div>
  )
}
