import { AlertTriangle, Ban, Check, CheckCircle2, ClipboardCheck, Download, LoaderCircle, ShieldCheck, TriangleAlert, Wrench, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import StatusPill from './StatusPill.jsx'
import AgentTerminalLog from './AgentTerminalLog.jsx'
import { api } from '../services/api.js'

const rootCauses = [
  ['bearing_wear', 'Bearing wear'],
  ['imbalance', 'Imbalance'],
  ['misalignment', 'Misalignment'],
  ['lubrication', 'Lubrication'],
  ['cooling_restriction', 'Cooling restriction'],
  ['overheating', 'Overheating'],
  ['friction', 'Friction'],
  ['overload', 'Overload'],
  ['sensor_fault', 'Sensor fault'],
  ['other', 'Other / undetermined'],
]

export default function ApprovalModal({ workflow, auditEvents = [], activity = [], currentAsset, plantId, latestReading, onClose, onApprove, onReject, onLifecycle, onCloseOut, canApprove = true, canWorkOrder = true, busy }) {
  const [comment, setComment] = useState('')
  const [confirmAction, setConfirmAction] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [staleAcknowledged, setStaleAcknowledged] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const [confirmedRootCause, setConfirmedRootCause] = useState('other')
  const [alertWasUseful, setAlertWasUseful] = useState('')
  const [closeoutComment, setCloseoutComment] = useState('')
  const [activeTab, setActiveTab] = useState('review')
  const [traceExpanded, setTraceExpanded] = useState(false)
  const [sourceDetail, setSourceDetail] = useState(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const dialogRef = useRef(null)
  const state = workflow?.workflow_state || {}
  const awaiting = workflow?.status === 'awaiting_approval'
  const latestEvent = auditEvents.at(-1)

  useEffect(() => {
    if (!workflow) return undefined
    const previousFocus = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialogRef.current?.focus()

    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        if (confirmAction) setConfirmAction(null)
        else onClose()
      }
      if (event.key !== 'Tab') return
      const focusable = dialogRef.current?.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement) previousFocus.focus()
    }
  }, [workflow?.id, confirmAction])

  useEffect(() => {
    setComment('')
    setConfirmAction(null)
    setConfirmed(false)
    setStaleAcknowledged(false)
    setDecisionError('')
    setConfirmedRootCause('other')
    setAlertWasUseful('')
    setCloseoutComment('')
    setActiveTab('review')
    setTraceExpanded(false)
    setSourceDetail(null)
    setSourceLoading(false)
    setSourceError('')
  }, [workflow?.id])

  if (!workflow) return null

  function openConfirmation(action) {
    setDecisionError('')
    setConfirmed(false)
    setStaleAcknowledged(false)
    setConfirmAction(action)
  }

  async function submitDecision() {
    if (confirmAction === 'approve' && (!confirmed || (workflow.approval_is_stale && !staleAcknowledged))) return
    setDecisionError('')
    const result = confirmAction === 'approve'
      ? await onApprove(workflow.id, comment, staleAcknowledged)
      : await onReject(workflow.id, comment)
    if (result?.ok) {
      setConfirmAction(null)
      setConfirmed(false)
    } else {
      setDecisionError(result?.message || 'The decision could not be saved. Please retry.')
    }
  }

  async function advanceLifecycle(status) {
    setDecisionError('')
    const result = await onLifecycle(workflow.id, status)
    if (!result?.ok) setDecisionError(result?.message || 'Lifecycle update failed. Please retry.')
  }

  async function submitCloseOut() {
    if (!confirmedRootCause || alertWasUseful === '') return
    setDecisionError('')
    const result = await onCloseOut(workflow.id, {
      confirmed_root_cause: confirmedRootCause,
      alert_was_useful: alertWasUseful === 'yes',
      comment: closeoutComment,
    })
    if (!result?.ok) setDecisionError(result?.message || 'Close-out could not be saved. Please retry.')
  }

  async function openCitation(citation) {
    if (sourceDetail?.chunk_id === citation.chunk_id) {
      setSourceDetail(null)
      setSourceError('')
      return
    }
    setSourceLoading(true)
    setSourceError('')
    try {
      setSourceDetail(await api.knowledgeChunk(citation.chunk_id, plantId))
    } catch {
      setSourceDetail(null)
      setSourceError('This source is unavailable or you no longer have access to it.')
    } finally {
      setSourceLoading(false)
    }
  }

  const telemetry = state.telemetry || {}
  const severity = String(state.severity || 'high').toLowerCase()
  const diagnosisAgeSeconds = workflow.diagnosis_age_seconds ?? Math.max(0, (Date.now() - new Date(workflow.created_at).getTime()) / 1000)
  const diagnosisAgeMinutes = Math.floor(diagnosisAgeSeconds / 60)
  const diagnosisAgeLabel = diagnosisAgeMinutes < 60 ? `${diagnosisAgeMinutes}m` : `${Math.floor(diagnosisAgeMinutes / 60)}h ${diagnosisAgeMinutes % 60}m`
  const approvalIsStale = Boolean(workflow.approval_is_stale)
  const originalTelemetry = state.telemetry || {}
  const readingsChanged = latestReading && new Date(latestReading.timestamp) > new Date(workflow.created_at)
    && ['vibration_mm_s', 'temperature_c', 'pressure_bar', 'rpm'].some((key) => originalTelemetry[key] != null && Number(originalTelemetry[key]) !== Number(latestReading[key]))

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !confirmAction && onClose()}>
      <section className="approval-modal" role="dialog" aria-modal="true" aria-labelledby="approval-title" tabIndex={-1} ref={dialogRef}>
        <div className="modal-topline">
          <span><ClipboardCheck size={15} /> WORK ORDER REVIEW · <span className="mono">{workflow.id}</span> · {new Date(workflow.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
          <button className="icon-button modal-close" type="button" onClick={() => api.workOrderAuditPack(workflow.id).catch((error) => setDecisionError(error.message || 'Audit pack download failed.'))} aria-label="Download work order audit pack" title="Download PDF audit pack"><Download size={17} /></button>
          <button className="icon-button modal-close" type="button" onClick={onClose} aria-label="Close review" title="Close review"><X size={18} /></button>
        </div>
        <div className="review-tabs" role="tablist" aria-label="Work order detail views">
          <button id="review-tab-review" role="tab" type="button" aria-selected={activeTab === 'review'} onClick={() => setActiveTab('review')}>Decision</button>
          <button id="review-tab-trace" role="tab" type="button" aria-selected={activeTab === 'trace'} onClick={() => setActiveTab('trace')}>Trace · {activity.length} steps</button>
        </div>
        {activeTab === 'trace' ? (
          <div className="modal-content trace-tab-content" role="tabpanel" aria-labelledby="review-tab-trace">
            <button className="trace-summary-toggle" type="button" aria-expanded={traceExpanded} onClick={() => setTraceExpanded((value) => !value)}>{activity.length} steps · {workflow.status.replaceAll('_', ' ')} · {traceExpanded ? 'Hide trace' : 'Expand trace'}</button>
            {traceExpanded && <AgentTerminalLog activity={activity} />}
          </div>
        ) : (
        <div className="modal-content">
          <div className="review-kicker">{workflow.asset?.asset_tag || state.asset_tag || 'ASSET'} · {workflow.summary}</div>
          <div className="review-title-row">
            <h2 id="approval-title">{awaiting ? 'Review proposed action' : 'Work order record'}</h2>
            <StatusPill status={workflow.status} cmmsReference={workflow.cmms_reference} />
          </div>
          <div className="review-grid">
            <div className="review-column">
              <div className="review-alert"><AlertTriangle size={17} /><span><b className={`severity-${severity}`}>{severity} severity</b><small>{state.message || 'Operator review requested.'}</small>
                    {telemetry.vibration_mm_s != null && state.vibration_threshold_mm_s != null && <small>Vibration {Number(telemetry.vibration_mm_s).toFixed(2)} mm/s · {state.vibration_threshold_source === 'asset' ? 'Asset limit' : 'Demo advisory'} {state.vibration_threshold_mm_s} mm/s</small>}
                    {telemetry.temperature_c != null && state.temperature_threshold_c != null && <small>Temperature {Number(telemetry.temperature_c).toFixed(1)} °C · {state.temperature_threshold_source === 'asset' ? 'Asset limit' : 'Demo advisory'} {state.temperature_threshold_c} °C</small>}
              </span></div>
              <div className="review-section"><label>DIAGNOSIS</label><p>{workflow.diagnosis || 'No diagnosis recorded.'}</p></div>
              {(state.diagnosis_confidence != null || state.diagnostic_method || state.diagnostic_model_version) && (
                <div className="confidence-note"><TriangleAlert size={15} /><span>Confidence {state.diagnosis_confidence == null ? 'not available' : `${Math.round(state.diagnosis_confidence * 100)}%`} · method: {state.diagnostic_method || 'not available'} · model: {state.diagnostic_model_version || 'not available'}. Demo confidence is not a validated operational probability.</span></div>
              )}
              {state.evidence_status === 'unavailable' && <div className="confidence-note evidence-unavailable"><TriangleAlert size={15} /><span>Knowledge evidence unavailable. This assessment was completed without retrieved maintenance references.</span></div>}
              {state.evidence_status === 'no_matches' && <div className="review-section"><label>MAINTENANCE REFERENCES</label><p>The knowledge search completed, but found no matching references.</p></div>}
              {state.citation_verification_status === 'model_supported' && <div className="review-section"><label>CLAIM-LEVEL CITATION CHECK</label><p>The configured verifier found support for each diagnosis and recommendation claim in the cited passages. This model check is advisory, not a guarantee.</p></div>}
              {state.citation_verification_status === 'issues' && <div className="confidence-note evidence-unavailable"><TriangleAlert size={15} /><span>One or more diagnosis or recommendation claims were unsupported or uncertain against retrieved evidence. Review the cited passages before approval.</span></div>}
              {state.citation_verification_status === 'unavailable' && state.evidence_sources?.length > 0 && <div className="confidence-note evidence-unavailable"><TriangleAlert size={15} /><span>Claim-level citation support was not verified. Treat recommendations as unverified and review the cited passages.</span></div>}
              {state.evidence_sources?.length > 0 ? <div className="review-section citation-list"><label>RETRIEVED SOURCE PASSAGES</label>{state.evidence_sources.map((citation, index) => <button className="citation-reference" type="button" key={citation.chunk_id} aria-expanded={sourceDetail?.chunk_id === citation.chunk_id} onClick={() => openCitation(citation)}><strong>REFERENCE {String(index + 1).padStart(2, '0')} · {citation.filename || 'Maintenance source'}</strong><span>{[citation.revision && `Revision ${citation.revision}`, citation.page_number && `Page ${citation.page_number}`, citation.section_title].filter(Boolean).join(' · ') || 'Open retrieved passage'}</span></button>)}</div> : state.evidence?.length > 0 && <div className="review-section"><label>LEGACY REFERENCE NOTES</label>{state.evidence.map((note, index) => <p key={`${index}-${note}`}>{note}</p>)}</div>}
              {sourceLoading && <p className="citation-source-status" role="status">Loading source passage…</p>}
              {sourceError && <p className="citation-source-status is-error" role="alert">{sourceError}</p>}
              {sourceDetail && <section className="citation-source-detail" aria-label="Retrieved source passage"><header><strong>{sourceDetail.filename}</strong><button className="button button-quiet" type="button" onClick={() => setSourceDetail(null)}>Close source</button></header><small>{[sourceDetail.revision && `Revision ${sourceDetail.revision}`, sourceDetail.page_number && `Page ${sourceDetail.page_number}`, sourceDetail.section_title].filter(Boolean).join(' · ')}</small><blockquote>{sourceDetail.content}</blockquote></section>}
              <div className="review-section recommendation"><label>RECOMMENDED ACTION</label><p>{workflow.recommendation || 'No recommendation recorded.'}</p></div>
            </div>
            <div className="review-column">
              <div className="review-section"><label>POLICY CHECKS</label>
                {state.compliance_check?.checks?.length ? <ul className="policy-check-list">{state.compliance_check.checks.map((check, index) => <li key={`${index}-${check}`}><CheckCircle2 size={14} />{check}</li>)}</ul> : <p>Policy details are unavailable.</p>}
                {state.compliance_check?.compliant === false && <p className="policy-failure"><Ban size={14} /> {state.compliance_check.reason || 'Workflow is blocked by the policy check.'}</p>}
              </div>
              <div className="reference-row"><span>CMMS REFERENCE</span><b className="mono">{workflow.cmms_reference || (awaiting ? 'Not yet created · generated on dispatch' : 'Not available')}</b></div>
              {awaiting && <label className="comment-label">DECISION NOTE (OPTIONAL)<textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Add context for the maintenance record" rows="3" /></label>}
              {latestEvent && <div className="audit-attribution"><span className="mono">RECORDED EVENT</span><p>{latestEvent.actor_type || 'api_client'} · {new Date(latestEvent.created_at).toLocaleString()}</p>{latestEvent.details?.comment && <p>Note: {latestEvent.details.comment}</p>}</div>}
            </div>
          </div>
          <div className={`approval-freshness ${approvalIsStale || readingsChanged ? 'is-warning' : ''}`}>
            <span>Diagnosis age: {diagnosisAgeLabel} · Current asset: {workflow.current_asset_status || currentAsset?.status || 'status unavailable'}</span>
            {workflow.telemetry_delta && <span>Change since diagnosis: vib {workflow.telemetry_delta.vibration_mm_s == null ? '—' : `${workflow.telemetry_delta.vibration_mm_s > 0 ? '+' : ''}${workflow.telemetry_delta.vibration_mm_s.toFixed(2)} mm/s`} · temp {workflow.telemetry_delta.temperature_c == null ? '—' : `${workflow.telemetry_delta.temperature_c > 0 ? '+' : ''}${workflow.telemetry_delta.temperature_c.toFixed(1)} °C`}</span>}
            {(approvalIsStale || readingsChanged) && <strong>Current conditions differ from the diagnosis snapshot. Recheck current telemetry before deciding.</strong>}
            {approvalIsStale && <small>Approval remains available after an explicit acknowledgement.</small>}
            {canApprove && latestEvent && <span>Recorded by {latestEvent.actor_id || latestEvent.actor_type} · {new Date(latestEvent.created_at).toLocaleString()}</span>}
          </div>
        </div>
        )}
        {awaiting && canApprove ? (
          <div className="modal-actions decision-actions">
            <button className="button button-reject" type="button" onClick={() => openConfirmation('reject')} disabled={busy}><X size={15} />Reject</button>
            <button className="button button-approve" type="button" onClick={() => openConfirmation('approve')} disabled={busy}><Check size={15} />Approve &amp; Dispatch…</button>
          </div>
        ) : awaiting ? (
          <div className="modal-actions"><p className="decision-error" role="status">An approver role is required to record a decision.</p></div>
        ) : workflow.status === 'dispatched' && canWorkOrder ? (
          <div className="modal-actions lifecycle-actions">
            {decisionError && <p className="decision-error" role="alert">{decisionError}</p>}
            <button className="button button-primary" type="button" onClick={() => advanceLifecycle('in_progress')} disabled={busy}><Wrench size={15} />{busy ? 'Updating…' : 'Mark in progress'}</button>
          </div>
        ) : workflow.status === 'in_progress' && canWorkOrder ? (
          <div className="modal-actions lifecycle-actions">
            {decisionError && <p className="decision-error" role="alert">{decisionError}</p>}
            <button className="button button-primary" type="button" onClick={() => advanceLifecycle('completed')} disabled={busy}><Check size={15} />{busy ? 'Updating…' : 'Mark completed'}</button>
          </div>
        ) : workflow.status === 'completed' && canWorkOrder ? (
          <div className="modal-content closeout-content">
            <div className="review-section"><label>TECHNICIAN CLOSE-OUT</label><p>Record the confirmed root cause and whether this alert was useful. These answers feed pilot evaluation metrics.</p></div>
            <label className="comment-label">CONFIRMED ROOT CAUSE<select value={confirmedRootCause} onChange={(event) => setConfirmedRootCause(event.target.value)}>{rootCauses.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <fieldset className="useful-fieldset"><legend>WAS THE ALERT USEFUL?</legend><label><input type="radio" name={`alert-useful-${workflow.id}`} value="yes" checked={alertWasUseful === 'yes'} onChange={(event) => setAlertWasUseful(event.target.value)} />Yes</label><label><input type="radio" name={`alert-useful-${workflow.id}`} value="no" checked={alertWasUseful === 'no'} onChange={(event) => setAlertWasUseful(event.target.value)} />No</label></fieldset>
            <label className="comment-label">CLOSE-OUT NOTE (OPTIONAL)<textarea value={closeoutComment} onChange={(event) => setCloseoutComment(event.target.value)} rows="2" placeholder="Add inspection or repair context" /></label>
            {decisionError && <p className="decision-error" role="alert">{decisionError}</p>}
            <div className="modal-actions"><button className="button button-primary" type="button" onClick={submitCloseOut} disabled={busy || alertWasUseful === ''}>{busy ? 'Saving…' : 'Record close-out'}</button></div>
          </div>
        ) : (
          <div className="modal-actions"><button className="button button-primary" type="button" onClick={onClose}>Done</button></div>
        )}

        {confirmAction && (
          <div className="confirm-backdrop" role="presentation">
            <section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
              <h3 id="confirm-title">{confirmAction === 'approve' ? 'Confirm dispatch' : 'Confirm rejection'}</h3>
              <p id="confirm-description">{confirmAction === 'approve' ? `You are approving ${workflow.id} for ${workflow.asset?.name || state.asset_name || 'this asset'}.` : `Reject ${workflow.id}? It will be recorded without dispatch.`}</p>
              {confirmAction === 'approve' ? (
                <>
                  <ul><li>Work order is queued for the configured CMMS boundary after approval.</li><li>The API records the decision, verified actor when available, time, and note in workflow history.</li><li>This does not control or stop the machine.</li></ul>
                  <label className="confirm-checkbox"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I have reviewed the evidence and policy checks.</label>
                  {approvalIsStale && <label className="confirm-checkbox stale-acknowledgement"><input type="checkbox" checked={staleAcknowledged} onChange={(event) => setStaleAcknowledged(event.target.checked)} />I acknowledge the diagnosis is stale and have rechecked the current asset status and readings.</label>}
                </>
              ) : <label className="comment-label">REJECTION REASON (OPTIONAL)<textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Record why this proposal is being rejected" rows="3" /></label>}
              {decisionError && <p className="decision-error" role="alert">{decisionError}</p>}
              <div className="confirm-actions">
                <button className="button button-quiet" type="button" onClick={() => setConfirmAction(null)} disabled={busy}>Cancel</button>
                <button className={confirmAction === 'approve' ? 'button button-approve' : 'button button-reject'} type="button" onClick={submitDecision} disabled={busy || (confirmAction === 'approve' && (!confirmed || (approvalIsStale && !staleAcknowledged)))}>
                  {busy ? <LoaderCircle className="animate-spin" size={15} /> : confirmAction === 'approve' ? <Check size={15} /> : <X size={15} />}
                  {busy ? 'Submitting…' : confirmAction === 'approve' ? 'Confirm dispatch' : 'Reject proposal'}
                </button>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  )
}
