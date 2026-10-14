import { Activity, CircleDot, Search, ShieldCheck, Thermometer, TriangleAlert } from 'lucide-react'
import StatusPill from './StatusPill.jsx'

export default function AssetGrid({ assets, selectedId, onSelect, activeFilters = [], onClearFilters, scenarios = [] }) {
  if (!assets.length) {
    return (
      <div className="empty-state asset-empty">
        <Search size={19} aria-hidden="true" />
        <strong>No assets match these filters</strong>
        <span>{activeFilters.length ? `Active filters: ${activeFilters.join(' · ')}` : 'There are no assets in this scope yet.'}</span>
        {activeFilters.length > 0 && <button className="button button-quiet" type="button" onClick={onClearFilters}>Clear filters</button>}
      </div>
    )
  }

  return (
    <div className="asset-list">
      {assets.map((asset, index) => {
        const selected = asset.id === selectedId
        const vibration = asset.latest_vibration_mm_s
        const temperature = asset.latest_temperature_c
        const vibrationLimit = asset.vibration_threshold_mm_s
        const temperatureLimit = asset.temperature_threshold_c
        const critical = ['critical', 'high'].includes(asset.criticality)
        const proximity = (value, limit) => Number.isFinite(value) && Number.isFinite(limit) && limit > 0
          ? Math.max(0, Math.round((value / limit) * 100))
          : null
        const vibrationProximity = proximity(vibration, vibrationLimit)
        const temperatureProximity = proximity(temperature, temperatureLimit)
        const scenario = scenarios.find((item) => item.asset_id === asset.id)
        return (
          <button
            key={asset.id}
            type="button"
            onClick={() => onSelect(asset)}
            className={`asset-row ${selected ? 'selected' : ''}`}
            aria-pressed={selected}
            aria-label={`${asset.asset_tag}, ${asset.name}, ${asset.location}, ${asset.status}${asset.alert_active ? ', open alert' : ''}`}
            data-asset-id={asset.id}
            title={`Select ${asset.asset_tag}`}
          >
            <span className="asset-id">{asset.asset_tag}<i className={`asset-criticality-icon ${critical ? 'is-critical' : ''}`} title={`Criticality: ${asset.criticality}`} aria-label={`Criticality: ${asset.criticality}`}>{critical ? <TriangleAlert size={12} /> : <ShieldCheck size={12} />}</i></span>
            <span className="asset-main">
              <span className="asset-name">{asset.name}</span>
              {scenario && <span className="scenario-chip">{scenario.scenario.replaceAll('_', ' ')} · demo</span>}
            </span>
            <span className={`asset-value asset-vibration ${vibration != null && vibrationLimit != null && vibration >= vibrationLimit ? 'value-attention' : ''}`} title={vibrationProximity == null ? '' : `${vibrationProximity}% of ${asset.vibration_threshold_source === 'asset' ? 'asset-specific' : 'demo advisory'} vibration limit`}>
              <Activity size={13} aria-hidden="true" />
              <span><b>{vibration?.toFixed(1) ?? '—'}</b><small>{vibrationProximity == null ? 'mm/s' : `${vibrationProximity}%`}</small></span>
              {vibrationProximity != null && <i className="asset-proximity" title={`${vibrationProximity}% of ${asset.vibration_threshold_source === 'asset' ? 'asset-specific' : 'demo advisory'} limit`}><i style={{ width: `${Math.min(100, vibrationProximity)}%` }} /></i>}
            </span>
            <span className={`asset-value asset-temperature ${temperature != null && temperatureLimit != null && temperature >= temperatureLimit ? 'value-attention' : ''}`} title={temperatureProximity == null ? '' : `${temperatureProximity}% of ${asset.temperature_threshold_source === 'asset' ? 'asset-specific' : 'demo advisory'} temperature limit`}>
              <Thermometer size={13} aria-hidden="true" />
              <span><b>{temperature?.toFixed(0) ?? '—'}</b><small>{temperatureProximity == null ? '°C' : `${temperatureProximity}%`}</small></span>
              {temperatureProximity != null && <i className="asset-proximity" title={`${temperatureProximity}% of ${asset.temperature_threshold_source === 'asset' ? 'asset-specific' : 'demo advisory'} limit`}><i style={{ width: `${Math.min(100, temperatureProximity)}%` }} /></i>}
            </span>
            <span className="asset-condition">
              {asset.status === 'operational'
                ? <span className="asset-normal-dot" title="Latest readings within limits" aria-label="Normal"><CircleDot size={13} /><span>Normal</span></span>
                : <StatusPill status={asset.status} className="asset-row-status" />}
              {asset.alert_active && <span className="asset-open-alert"><TriangleAlert size={11} aria-hidden="true" />{asset.alert_acknowledged_at ? 'Acknowledged' : 'Alert open'}</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}
