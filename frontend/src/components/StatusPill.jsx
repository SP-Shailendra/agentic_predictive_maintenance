import { Ban, Check, CircleDot, CircleHelp, Clock3, Diamond, TriangleAlert, X } from 'lucide-react'

const statusMap = {
  operational: { label: 'Normal', tone: 'normal', Icon: CircleDot },
  normal: { label: 'Normal', tone: 'normal', Icon: CircleDot },
  watch: { label: 'Watch', tone: 'watch', Icon: Diamond },
  attention: { label: 'Attention', tone: 'attention', Icon: TriangleAlert },
  offline: { label: 'Stale (est.)', tone: 'stale', Icon: Clock3 },
  stale: { label: 'Stale (est.)', tone: 'stale', Icon: Clock3 },
  awaiting_approval: { label: 'Awaiting approval', tone: 'awaiting', Icon: Clock3 },
  dispatch_queued: { label: 'Approved · Queued', tone: 'awaiting', Icon: Clock3 },
  dispatching: { label: 'Dispatching', tone: 'watch', Icon: Clock3 },
  dispatch_retry: { label: 'Dispatch retry', tone: 'watch', Icon: Clock3 },
  dispatch_failed: { label: 'Dispatch failed', tone: 'danger', Icon: TriangleAlert },
  dispatched: { label: 'Approved · Dispatched', tone: 'normal', Icon: Check },
  in_progress: { label: 'In progress', tone: 'watch', Icon: Clock3 },
  completed: { label: 'Completed', tone: 'normal', Icon: Check },
  closed: { label: 'Closed', tone: 'stale', Icon: Check },
  rejected: { label: 'Rejected', tone: 'danger', Icon: X },
  blocked: { label: 'Blocked by policy', tone: 'blocked', Icon: Ban },
  processing: { label: 'Processing', tone: 'watch', Icon: Clock3 },
}

export default function StatusPill({ status, className = '', cmmsReference }) {
  const key = String(status || 'unknown').toLowerCase()
  const mapped = statusMap[key] || { label: String(status || 'Unknown').replaceAll('_', ' '), tone: 'unknown', Icon: CircleHelp }
  const { Icon } = mapped
  const label = key === 'dispatched' && String(cmmsReference || '').startsWith('SIM-')
    ? 'Dispatched (simulated reference)'
    : mapped.label

  return (
    <span className={`status-pill status-pill-${mapped.tone} ${className}`.trim()}>
      <Icon size={12} aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}