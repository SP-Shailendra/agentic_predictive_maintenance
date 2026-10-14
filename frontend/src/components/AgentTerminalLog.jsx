import { Check, Circle, Clock3, FileText, ScanSearch, ShieldCheck, Wrench } from 'lucide-react'

const icons = { Edge: ActivityIcon, Detect: ScanSearch, Diagnose: Wrench, Decide: ShieldCheck, Dispatch: Wrench, Document: FileText, Human: ShieldCheck }
function ActivityIcon({ size = 15 }) { return <Circle size={size} /> }

export default function AgentTerminalLog({ activity = [] }) {
  if (!activity.length) {
    return <div className="log-empty"><span className="pulse-mark" />Select a workflow in the queue to see its trace.</div>
  }
  return (
    <div className="agent-log">
      {activity.map((entry, index) => {
        const Icon = icons[entry.agent] || Circle
        const waiting = entry.status === 'waiting'
        const error = entry.status === 'error' || entry.status === 'rejected'
        const createdAt = entry.created_at
        const step = entry.agent === 'Decide' ? 'POLICY' : entry.agent === 'Dispatch' && waiting ? 'DISPATCH / HOLD' : entry.agent.toUpperCase()
        return (
          <div className={`log-entry ${waiting ? 'log-waiting' : ''} ${error ? 'log-error' : ''}`} key={`${entry.agent}-${index}`}>
            <span className="log-icon"><Icon size={14} /></span>
            <div className="log-copy">
              <div><strong>{createdAt ? new Date(createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'Time unavailable'} · {step}</strong><span className="log-time">{waiting ? <Clock3 size={11} /> : error ? <Circle size={11} /> : <Check size={11} />}{waiting ? 'HOLD' : error ? 'REVIEW' : 'DONE'}</span></div>
              <p>{entry.message}</p>
            </div>
          </div>
        )
      })}
    </div>
  )
}
