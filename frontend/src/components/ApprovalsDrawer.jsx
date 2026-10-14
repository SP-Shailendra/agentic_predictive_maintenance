import { useEffect, useRef } from 'react'
import { ClipboardCheck, Clock3, X } from 'lucide-react'
import StatusPill from './StatusPill.jsx'

function waitingDuration(value) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return 'under a minute'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours`
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  return hours ? `${days} days ${hours} hours` : `${days} days`
}

export default function ApprovalsDrawer({ open, pending, recentlyDecided = [], onClose, onReview }) {
  const drawerRef = useRef(null)
  const groups = [...pending.reduce((map, workflow) => {
    const state = workflow.workflow_state || {}
    const key = `${state.asset_tag || workflow.asset_id}:${state.approval_overdue ? 'overdue' : 'waiting'}`
    if (!map.has(key)) map.set(key, { key, assetTag: state.asset_tag || 'Asset', assetName: state.asset_name || 'Unknown asset', workflows: [] })
    map.get(key).workflows.push(workflow)
    return map
  }, new Map()).values()]
  const overdueCount = pending.filter((workflow) => workflow.workflow_state?.approval_overdue).length

  useEffect(() => {
    if (!open) return undefined
    const previousFocus = document.activeElement
    drawerRef.current?.focus()
    function handleKeyDown(event) {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab') return
      const controls = [...(drawerRef.current?.querySelectorAll('button:not(:disabled), [tabindex="0"]') || [])]
      if (!controls.length) return
      if (event.shiftKey && document.activeElement === controls[0]) {
        event.preventDefault()
        controls.at(-1).focus()
      } else if (!event.shiftKey && document.activeElement === controls.at(-1)) {
        event.preventDefault()
        controls[0].focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement) previousFocus.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <>
      <button className="approvals-backdrop" type="button" aria-label="Close approvals" onClick={() => onClose()} />
      <aside className="approvals-drawer" role="dialog" aria-modal="true" aria-labelledby="approvals-drawer-title" tabIndex={-1} ref={drawerRef}>
        <header className="approvals-drawer-header">
          <div><span><ClipboardCheck size={16} /></span><div><h2 id="approvals-drawer-title">Awaiting approval</h2><p>{overdueCount} overdue · {pending.length} total · overdue first</p></div></div>
          <button className="icon-button" type="button" onClick={() => onClose()} aria-label="Close approvals" title="Close approvals"><X size={17} /></button>
        </header>
        <div className="approvals-drawer-list">
          {groups.length ? groups.map((group) => (
            <section className="approval-asset-group" key={group.key}>
              <h3>{group.assetName} <small>{group.assetTag} ×{group.workflows.length}</small></h3>
              {group.workflows.map((workflow) => (
                <article className={`approval-drawer-item ${workflow.workflow_state?.approval_overdue ? 'is-overdue' : ''}`} key={workflow.id}>
                  <div className="approval-drawer-item-heading"><strong>{workflow.summary}</strong>{workflow.workflow_state?.approval_overdue && <b className="approval-overdue-label">Overdue</b>}<StatusPill status={workflow.status} /></div>
                  <p className="approval-waiting"><Clock3 size={12} />{new Date(workflow.created_at).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })} · waiting {waitingDuration(workflow.created_at)}</p>
                  <button className="button button-quiet" type="button" onClick={() => { onReview(workflow); onClose() }}>Review work order</button>
                </article>
              ))}
            </section>
          )) : <p className="approvals-empty">No work orders are waiting for approval.</p>}
          <section className="approval-recent-group">
            <h3>Recently decided</h3>
            {recentlyDecided.length ? recentlyDecided.slice(0, 8).map((workflow) => (
              <button className="approval-recent-item" type="button" key={workflow.id} onClick={() => { onReview(workflow); onClose() }}>
                <span><strong>{workflow.summary}</strong><small>{new Date(workflow.created_at).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })}</small></span>
                <StatusPill status={workflow.status} cmmsReference={workflow.cmms_reference} />
              </button>
            )) : <p className="approvals-empty">No recent decisions.</p>}
          </section>
        </div>
      </aside>
    </>
  )
}