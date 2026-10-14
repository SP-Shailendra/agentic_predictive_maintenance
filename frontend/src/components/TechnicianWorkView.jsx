import { ClipboardCheck, LoaderCircle, Wrench } from 'lucide-react'
import { useState } from 'react'
import StatusPill from './StatusPill.jsx'

export default function TechnicianWorkView({ workOrders, candidates, canAssign, isTechnician, onOpenWorkflow, onAssign }) {
  const [assignees, setAssignees] = useState({})
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState('')

  async function submitAssignment(event, workflow) {
    event.preventDefault()
    const subject = (assignees[workflow.id] ?? workflow.assigned_to_sub ?? '').trim()
    if (!subject) return
    setError('')
    setBusyId(workflow.id)
    const result = await onAssign(workflow.id, subject)
    if (!result?.ok) setError(result?.message || 'Assignment could not be saved.')
    setBusyId(null)
  }

  return (
    <section className="technician-workspace" aria-labelledby="my-work-title">
      <header className="technician-work-header">
        <div><p className="eyebrow">FIELD WORK</p><h2 id="my-work-title">{isTechnician ? 'My work' : 'Technician assignments'}</h2><p>{isTechnician ? 'Assigned work orders and technician close-outs.' : 'Assign active work orders to technician subjects.'}</p></div>
        <span className="section-label mono">{workOrders.length} assigned</span>
      </header>
      <ol className="technician-work-list">
        {workOrders.length ? workOrders.map((workflow) => (
          <li key={workflow.id}>
            <div><strong>{workflow.summary}</strong><small>{workflow.workflow_state?.asset_tag || workflow.asset_id} · {new Date(workflow.created_at).toLocaleString()}</small></div>
            <StatusPill status={workflow.status} cmmsReference={workflow.cmms_reference} />
            <button className="button button-quiet" type="button" onClick={() => onOpenWorkflow(workflow.id)}><ClipboardCheck size={14} />Open work order</button>
          </li>
        )) : <li className="technician-work-empty"><Wrench size={16} />No assigned work orders are ready for technician action.</li>}
      </ol>
      {canAssign && <section className="technician-assignment" aria-labelledby="assignment-title">
        <h3 id="assignment-title">Assign an active work order</h3>
        <p>Enter the technician’s verified identity subject from your organization directory.</p>
        {error && <p className="decision-error" role="alert">{error}</p>}
        <ol className="technician-assignment-list">
          {candidates.length ? candidates.map((workflow) => (
            <li key={workflow.id}>
              <div><strong>{workflow.summary}</strong><small>{workflow.workflow_state?.asset_tag || workflow.asset_id} · {workflow.status.replaceAll('_', ' ')}</small></div>
              <form onSubmit={(event) => submitAssignment(event, workflow)}>
                <label><span className="sr-only">Technician subject for {workflow.summary}</span><input value={assignees[workflow.id] ?? workflow.assigned_to_sub ?? ''} onChange={(event) => setAssignees((current) => ({ ...current, [workflow.id]: event.target.value }))} placeholder="Technician subject" maxLength={255} /></label>
                <button className="button button-quiet" type="submit" disabled={busyId === workflow.id || !(assignees[workflow.id] ?? workflow.assigned_to_sub ?? '').trim()}>{busyId === workflow.id ? <LoaderCircle size={14} className="animate-spin" /> : 'Assign'}</button>
              </form>
            </li>
          )) : <li className="technician-work-empty">No active work orders can be assigned.</li>}
        </ol>
      </section>}
    </section>
  )
}