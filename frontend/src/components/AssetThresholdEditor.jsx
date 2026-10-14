import { useEffect, useState } from 'react'
import { Clock3, Save, SlidersHorizontal } from 'lucide-react'
import { api } from '../services/api.js'

function displayLimit(value, unit) {
  return value == null ? 'Demo default' : `${value} ${unit}`
}

export default function AssetThresholdEditor({ asset, canEdit, onSaved }) {
  const [open, setOpen] = useState(false)
  const [vibration, setVibration] = useState('')
  const [temperature, setTemperature] = useState('')
  const [reason, setReason] = useState('')
  const [history, setHistory] = useState([])
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setVibration(asset?.vibration_threshold_source === 'asset' ? String(asset.vibration_threshold_mm_s) : '')
    setTemperature(asset?.temperature_threshold_source === 'asset' ? String(asset.temperature_threshold_c) : '')
    setHistory([])
    setHistoryLoaded(false)
    setError('')
  }, [asset?.id, asset?.vibration_threshold_mm_s, asset?.temperature_threshold_c, asset?.vibration_threshold_source, asset?.temperature_threshold_source])

  if (!asset || !canEdit) return null

  async function loadHistory() {
    if (historyLoaded || historyLoading) return
    setHistoryLoading(true)
    try {
      setHistory(await api.assetThresholdHistory(asset.id))
      setHistoryLoaded(true)
    } catch (requestError) {
      setError(requestError.message || 'Threshold history could not be loaded.')
    } finally {
      setHistoryLoading(false)
    }
  }

  async function saveThresholds(event) {
    event.preventDefault()
    if (reason.trim().length < 5) {
      setError('Enter a reason of at least five characters.')
      return
    }
    const vibrationValue = vibration.trim() === '' ? null : Number(vibration)
    const temperatureValue = temperature.trim() === '' ? null : Number(temperature)
    if ((vibrationValue != null && !Number.isFinite(vibrationValue)) || (temperatureValue != null && !Number.isFinite(temperatureValue))) {
      setError('Thresholds must be finite numbers.')
      return
    }
    setSaving(true)
    setError('')
    try {
      await api.updateAssetThresholds(asset.id, {
        vibration_threshold_mm_s: vibrationValue,
        temperature_threshold_c: temperatureValue,
        reason: reason.trim(),
      })
      setReason('')
      setHistoryLoaded(false)
      await onSaved()
    } catch (requestError) {
      setError(requestError.message || 'Threshold update failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="threshold-editor" aria-labelledby="threshold-editor-title">
      <div className="threshold-editor-heading">
        <div><h3 id="threshold-editor-title"><SlidersHorizontal size={14} /> Advisory thresholds</h3><p>Effective: {asset.vibration_threshold_mm_s} mm/s vibration · {asset.temperature_threshold_c} °C temperature</p></div>
        <button className="button button-quiet" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>{open ? 'Close editor' : 'Edit limits'}</button>
      </div>
      {open && (
        <div className="threshold-editor-content">
          <form className="threshold-form" onSubmit={saveThresholds}>
            <label>Vibration · mm/s<input type="number" min="0" max="500" step="0.1" value={vibration} onChange={(event) => setVibration(event.target.value)} placeholder={`Demo default: ${asset.vibration_threshold_mm_s}`} /></label>
            <label>Temperature · °C<input type="number" min="-50" max="500" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} placeholder={`Demo default: ${asset.temperature_threshold_c}`} /></label>
            <label className="threshold-reason">Reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength={5} maxLength={1000} rows={2} required placeholder="Record the engineering basis for this change" /></label>
            {error && <p className="threshold-error" role="alert">{error}</p>}
            <button className="button button-primary threshold-save" type="submit" disabled={saving}><Save size={14} />{saving ? 'Saving…' : 'Save limits'}</button>
          </form>
          <details className="threshold-history" onToggle={(event) => event.currentTarget.open && loadHistory()}>
            <summary><Clock3 size={14} /> Change history <span>{historyLoading ? 'Loading…' : historyLoaded ? `${history.length} records` : ''}</span></summary>
            {error && !historyLoaded && <p className="threshold-error" role="alert">{error}</p>}
            {historyLoaded && (history.length ? <ol>{history.map((change) => (
              <li key={change.id}>
                <time>{new Date(change.created_at).toLocaleString()}</time>
                <span>Vibration {displayLimit(change.vibration_old_mm_s, 'mm/s')} → {displayLimit(change.vibration_new_mm_s, 'mm/s')}</span>
                <span>Temperature {displayLimit(change.temperature_old_c, '°C')} → {displayLimit(change.temperature_new_c, '°C')}</span>
                <span>{change.reason}</span>
              </li>
            ))}</ol> : <p>No threshold changes recorded.</p>)}
          </details>
        </div>
      )}
    </section>
  )
}