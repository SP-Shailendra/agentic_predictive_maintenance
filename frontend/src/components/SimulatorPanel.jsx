import { useState } from 'react'
import { Play, RotateCcw } from 'lucide-react'
import { api } from '../services/api.js'

const scenarios = [
  ['vibration_rise', 'Vibration rise', 'Ramps vibration toward and above the effective advisory limit.'],
  ['overheating', 'Overheating', 'Ramps temperature toward and above the effective advisory limit.'],
  ['sudden_spike', 'Sudden spike', 'Emits one severe spike, followed by baseline samples.'],
  ['data_dropout', 'Data dropout', 'Omits samples for the selected duration.'],
  ['invalid_reading', 'Invalid reading', 'Submits an out-of-range value that validation must reject.'],
  ['sensor_flatline', 'Sensor flatline', 'Repeats the latest sensor values for the selected duration.'],
  ['healthy_baseline', 'Healthy baseline', 'Emits readings below the configured advisory limits.'],
]

export default function SimulatorPanel({ asset, enabled, canControl = true, activeScenario, onChanged }) {
  const [scenario, setScenario] = useState('vibration_rise')
  const [severity, setSeverity] = useState(0.7)
  const [duration, setDuration] = useState(12)
  const [forceFallback, setForceFallback] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  if (!enabled || !asset) return null
  const controlsDisabled = busy || !canControl || Boolean(error)

  async function start(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const result = await api.startScenario({
        asset_id: asset.id,
        scenario,
        duration_readings: Number(duration),
        severity: Number(severity),
        force_llm_fallback: forceFallback,
      })
      setMessage(result.message || 'Scenario started.')
      await onChanged()
    } catch (requestError) {
      setError(requestError.message || 'Scenario could not be started.')
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const result = await api.stopScenario(asset.id)
      setMessage(result.message || 'Scenario stopped and baseline restored.')
      await onChanged()
    } catch (requestError) {
      setError(requestError.message || 'Scenario could not be stopped.')
    } finally {
      setBusy(false)
    }
  }

  const selected = scenarios.find(([value]) => value === scenario)

  return (
    <section className="simulator-panel" aria-labelledby="simulator-title">
      <div className="simulator-heading">
        <div><h3 id="simulator-title">Simulation scenario</h3><p>Demo telemetry only. Scenarios never start a workflow or dispatch work.</p></div>
        {activeScenario && <span className="scenario-chip">{activeScenario.scenario.replaceAll('_', ' ')} · {activeScenario.remaining_readings} left</span>}
      </div>
      {!canControl && <p className="simulator-message">An operator or reliability engineer role is required to change simulation scenarios.</p>}
      <form className="simulator-form" onSubmit={start}>
        <label>Scenario<select value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={controlsDisabled}>{scenarios.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <p className="scenario-description">{selected?.[2]}</p>
        <label>Severity <output>{Number(severity).toFixed(1)}</output><input type="range" min="0" max="1" step="0.1" value={severity} onChange={(event) => setSeverity(event.target.value)} disabled={controlsDisabled} /></label>
        <label>Duration · readings<input type="number" min="1" max="120" step="1" value={duration} onChange={(event) => setDuration(event.target.value)} disabled={controlsDisabled} required /></label>
        <label className="fallback-toggle"><input type="checkbox" checked={forceFallback} onChange={(event) => setForceFallback(event.target.checked)} disabled={controlsDisabled} />Force threshold fallback for workflows started during this scenario</label>
        <div className="simulator-actions">
          <button className="button button-primary" type="submit" disabled={controlsDisabled}><Play size={14} />{busy ? 'Starting…' : 'Start scenario'}</button>
          <button className="button button-quiet" type="button" onClick={stop} disabled={controlsDisabled}><RotateCcw size={14} />{busy ? 'Working…' : 'Stop / reset'}</button>
        </div>
        {error && <p className="simulator-error" role="alert">{error}</p>}
        {error && <button className="button button-quiet simulator-retry" type="button" onClick={() => setError('')}>Retry</button>}
        {message && <p className="simulator-message" role="status">{message}</p>}
      </form>
    </section>
  )
}