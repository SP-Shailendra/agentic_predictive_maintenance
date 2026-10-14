import { Activity, AlertCircle, Check, Clock3 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import './TelemetryChart.css'
import StatusPill from './StatusPill.jsx'

const signalDefinitions = [
  { key: 'vibration_mm_s', label: 'Vibration', unit: 'mm/s', color: 'var(--sig-vibration)', thresholdSourceKey: 'vibration_threshold_source', precision: 2, min: 0, max: 500 },
  { key: 'temperature_c', label: 'Temperature', unit: '°C', color: 'var(--sig-temperature)', thresholdSourceKey: 'temperature_threshold_source', precision: 1, min: -50, max: 500 },
  { key: 'pressure_bar', label: 'Pressure', unit: 'bar', color: 'var(--sig-pressure)', precision: 2, min: 0, max: 2000 },
  { key: 'rpm', label: 'RPM', unit: 'rpm', color: 'var(--sig-rpm)', precision: 0, min: 0, max: 100000 },
]

function formatTime(value, windowMs = 0) {
  return new Date(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    ...(windowMs <= 10 * 60 * 1000 ? { second: '2-digit' } : {}),
  })
}

function formatDateTime(value) {
  return new Date(value).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function signalSummary(points, key, precision) {
  const values = points.map((point) => point[key]).filter(Number.isFinite)
  if (!values.length) return { minimum: null, maximum: null }
  return { minimum: Math.min(...values).toFixed(precision), maximum: Math.max(...values).toFixed(precision) }
}

function getMedian(values) {
  const sorted = [...values].sort((left, right) => left - right)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

function getRangeIssue(reading) {
  return signalDefinitions.find(({ key, min, max }) => !Number.isFinite(reading[key]) || reading[key] < min || reading[key] > max)
}

export default function TelemetryChart({ data, events = [], assetTag = 'selected asset', thresholds = {}, onQuality, showRecentEvents = true }) {
  const [activeIndex, setActiveIndex] = useState(null)
  const [showAllSignals, setShowAllSignals] = useState(false)
  const signals = signalDefinitions.map((signal) => ({
    ...signal,
    threshold: signal.thresholdSourceKey ? thresholds[signal.key] : undefined,
    thresholdSource: signal.thresholdSourceKey ? thresholds[signal.thresholdSourceKey] || 'demo' : undefined,
  })).filter((signal, index) => showAllSignals || index < 2)
  const points = data.map((reading) => ({ ...reading, time: new Date(reading.timestamp).getTime() }))
  const latest = points.at(-1)
  const intervals = points.slice(1).map((point, index) => point.time - points[index].time).filter((value) => value > 0)
  const expectedInterval = intervals.length >= 2 ? getMedian(intervals) : null
  const ageMs = latest ? Math.max(0, Date.now() - latest.time) : null
  const isStale = expectedInterval != null && ageMs > Math.max(expectedInterval * 3, 15000)
  const rangeIssue = latest ? getRangeIssue(latest) : null
  const firstTime = points[0]?.time
  const lastTime = latest?.time
  const windowMs = points.length > 1 ? lastTime - firstTime : 0
  const spansMidnight = points.length > 1 && new Date(firstTime).toDateString() !== new Date(lastTime).toDateString()
  const crosshairPoint = activeIndex == null ? latest : points[activeIndex] || latest
  const chartEvents = events.map((event) => ({
    ...event,
    eventTime: new Date(event.created_at).getTime(),
  })).filter((event) => Number.isFinite(event.eventTime)
    && event.eventTime >= firstTime && event.eventTime <= lastTime)
  const recentEvents = showRecentEvents ? events.slice(0, 3) : []
  const quality = latest ? {
    stale: isStale,
    freshness: expectedInterval == null ? 'Freshness establishing (est.)' : isStale ? 'Readings may be stale (est.)' : 'Fresh (est.)',
    rangeIssue: rangeIssue ? `${rangeIssue.label} ${String(latest[rangeIssue.key])} outside ${rangeIssue.min}..${rangeIssue.max}` : null,
    ageSeconds: Math.floor(ageMs / 1000),
    source: latest.source || 'source unavailable',
  } : null

  useEffect(() => {
    onQuality?.(quality)
  }, [latest?.id, quality?.stale, quality?.freshness, quality?.rangeIssue, quality?.ageSeconds, quality?.source, onQuality])

  return (
    <div className="telemetry-view">
      {points.length ? (
        <>
          <div className="crosshair-readout" aria-live="polite">
            <strong>{activeIndex == null ? 'Latest reading' : 'Hovered reading'}</strong>
            <time>{spansMidnight ? formatDateTime(crosshairPoint.time) : formatTime(crosshairPoint.time, windowMs)}</time>
            {signals.map((signal) => (
              <span key={signal.key}>{signal.label === 'Temperature' ? 'Temp' : signal.label === 'Pressure' ? 'Press' : signal.label === 'Vibration' ? 'Vib' : 'RPM'} {Number(crosshairPoint[signal.key]).toFixed(signal.precision)} {signal.unit}</span>
            ))}
            {activeIndex != null && signals.map((signal) => {
              const summary = signalSummary(points, signal.key, signal.precision)
              return <span className="readout-window" key={`${signal.key}-window`}>{signal.label} window {summary.minimum}–{summary.maximum} {signal.unit}</span>
            })}
            <span className="readout-source">Source: {crosshairPoint.source || 'unavailable'}</span>
          </div>
          <div className="chart-wrap" aria-label="Synchronized asset telemetry trends">
          {signals.map((signal, index) => {
            const values = points.map((point) => point[signal.key]).filter(Number.isFinite)
            const observedMin = values.length ? Math.min(...values) : 0
            const observedMax = values.length ? Math.max(...values) : 1
            const padding = Math.max((observedMax - observedMin) * 0.16, Math.abs(observedMax) * 0.04, 0.1)
            const domain = [
              ['vibration_mm_s', 'pressure_bar', 'rpm'].includes(signal.key) ? Math.max(0, observedMin - padding) : Math.max(signal.min, observedMin - padding),
              Math.max(observedMax + padding, signal.threshold == null ? 0 : signal.threshold + Math.max(signal.threshold * 0.08, 0.5)),
            ]
            return (
              <div className="signal-row" key={signal.key}>
                <div className="signal-meta">
                  <span className="signal-heading"><i className="signal-swatch" style={{ background: signal.color }} /><span className="signal-name">{signal.label}</span><small>{signal.unit}</small></span>
                </div>
                <div className="signal-plot">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={points}
                      margin={{ top: 8, right: 34, left: 8, bottom: 0 }}
                      onMouseMove={(chartState) => {
                        if (Number.isInteger(chartState?.activeTooltipIndex)) setActiveIndex(chartState.activeTooltipIndex)
                      }}
                      onMouseLeave={() => setActiveIndex(null)}
                    >
                      <CartesianGrid stroke="var(--row-divider)" strokeDasharray="3 5" vertical={false} />
                      <XAxis
                        dataKey="time"
                        type="number"
                        scale="time"
                        domain={['dataMin', 'dataMax']}
                        tickCount={4}
                        padding={{ left: 10, right: 24 }}
                        tickLine={false}
                        axisLine={false}
                        tick={{ fill: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                        minTickGap={16}
                        tickMargin={7}
                        tickFormatter={(value) => formatTime(value, windowMs)}
                        hide={index < signals.length - 1}
                      />
                      <YAxis domain={domain} tickCount={3} tickLine={false} axisLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)' }} tickFormatter={(value) => Number(value).toFixed(signal.precision)} width={44} />
                      <Tooltip content={() => null} cursor={false} />
                      {signal.threshold != null && (
                        <ReferenceLine
                          y={signal.threshold}
                          stroke="var(--danger)"
                          strokeDasharray="4 3"
                          label={{ value: `${signal.thresholdSource === 'demo' ? 'Demo advisory' : 'Asset limit'} ${signal.threshold} ${signal.unit}`, position: 'insideTopRight', fill: 'var(--danger)', fontSize: 11 }}
                        />
                      )}
                      {chartEvents.map((event) => (
                        <ReferenceLine
                          key={`${signal.key}-${event.id}`}
                          x={event.eventTime}
                          stroke="var(--await-fg)"
                          strokeDasharray="2 3"
                          label={index === 0 ? { value: `WO ${event.id.slice(0, 6)}`, position: 'insideTop', fill: 'var(--await-fg)', fontSize: 11 } : undefined}
                        />
                      ))}
                      {activeIndex != null && crosshairPoint && <ReferenceLine x={crosshairPoint.time} stroke="var(--nav-bg)" strokeWidth={1} />}
                      <Line
                        type="monotone"
                        dataKey={signal.key}
                        stroke={signal.color}
                        strokeWidth={1.6}
                        dot={false}
                        activeDot={{ r: 3, fill: signal.color, stroke: 'white', strokeWidth: 1 }}
                        isAnimationActive={false}
                        connectNulls={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )
          })}
          </div>
          <div className="chart-legend-row">
            <span><i className="legend-demo" />Demo advisory line</span>
            <span><i className="legend-workflow" />Workflow event (not a completed repair)</span>
            <button className="button button-quiet show-signals-button" type="button" onClick={() => setShowAllSignals((value) => !value)} aria-expanded={showAllSignals}>{showAllSignals ? 'Show fewer signals' : 'Show all signals'}</button>
          </div>
        </>
      ) : (
        <div className="chart-empty"><Activity size={18} /><strong>No readings returned for {assetTag}</strong><span>Nothing to plot. No lines are drawn without data.</span></div>
      )}

      <div className="telemetry-quality" aria-live="polite">
        {latest && !onQuality && (
          <>
            <span className={`quality-state ${isStale ? 'is-stale' : ''}`}>
              {isStale ? <Clock3 size={12} /> : expectedInterval == null ? <Clock3 size={12} /> : <Check size={12} />}
              {expectedInterval == null ? 'Freshness establishing (est.)' : isStale ? `Readings may be stale (est.) · over 3× typical ${Math.max(1, Math.round(expectedInterval / 1000))}s interval` : 'Fresh (est.)'}
            </span>
            {rangeIssue
              ? <span className="quality-invalid"><AlertCircle size={12} />Range check failed: {rangeIssue.label} {String(latest[rangeIssue.key])} outside {rangeIssue.min}..{rangeIssue.max}. Payload validation only.</span>
              : <span className="quality-valid"><Check size={12} />Range check passed</span>}
            <span className="quality-age"><Clock3 size={12} />{Math.floor(ageMs / 1000)}s ago</span>
          </>
        )}
      </div>

      {recentEvents.length > 0 && (
        <div className="telemetry-events">
          <span className="events-heading">Recent workflows</span>
          {recentEvents.map((event) => (
            <div className="telemetry-event" key={event.id}>
              <span><b>{event.summary}</b><small>{new Date(event.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</small></span>
              <StatusPill status={event.status} cmmsReference={event.cmms_reference} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
