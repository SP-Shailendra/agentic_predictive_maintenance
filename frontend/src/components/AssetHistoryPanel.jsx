import { AlertCircle, Clock3, LoaderCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../services/api.js'

export default function AssetHistoryPanel({ assetId, onOpenWorkflow }) {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    api.assetHistory(assetId)
      .then((rows) => { if (active) setEvents(rows) })
      .catch((requestError) => { if (active) setError(requestError.message || 'Asset history is unavailable.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [assetId])

  if (loading) return <div className="asset-history-state"><LoaderCircle size={16} className="animate-spin" />Loading asset history…</div>
  if (error) return <p className="asset-history-state is-error"><AlertCircle size={15} />{error}</p>
  if (!events.length) return <p className="asset-tab-empty">No history is available for this asset.</p>

  return (
    <ol className="asset-history-list" aria-label="Asset history">
      {events.map((event, index) => (
        <li key={`${event.event_type}-${event.created_at}-${index}`}>
          <span className="asset-history-marker" aria-hidden="true"><Clock3 size={13} /></span>
          <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</time>
          <strong>{event.title}</strong>
          {event.detail && <p>{event.detail}</p>}
          {event.actor && <small>{event.actor}</small>}
          {event.work_order_id && <button className="button button-quiet" type="button" onClick={() => onOpenWorkflow(event.work_order_id)}>Open work order</button>}
        </li>
      ))}
    </ol>
  )
}