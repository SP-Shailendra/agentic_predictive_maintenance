import { Activity, Bell, CircleDot, Clock3, LogOut, MessageCircle, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useState } from 'react'

export default function Navbar({ plants, selectedPlantId, onSelectPlant, pendingNotifications, overdueCount = 0, connected, connectionStatus, authEnabled = false, userName = '', userRoles = [], onSignOut, onOpenChat, onOpenApprovals, demoData = false, demoSummary = '' }) {
  const pendingCount = pendingNotifications.length
  const [demoInfoOpen, setDemoInfoOpen] = useState(false)
  const connection = connectionStatus || (connected ? 'connected' : 'disconnected')

  return (
    <header className="topbar">
      <a className="brand" href="#overview" aria-label="Compunnel FieldNote overview">
        <span className="brand-mark"><Activity size={19} strokeWidth={2.5} /></span>
        <span className="brand-name">Compunnel FieldNote</span>
      </a>
      <div className="topbar-center">
        <span className={`plant-status ${connection === 'connected' ? 'is-connected' : connection === 'checking' ? 'is-checking' : 'is-disconnected'}`}>
          {connection === 'connected' ? <CircleDot size={13} aria-hidden="true" /> : connection === 'checking' ? <Clock3 size={13} aria-hidden="true" /> : <TriangleAlert size={13} aria-hidden="true" />}
          <span>{connection === 'connected' ? 'Connected' : connection === 'checking' ? 'Checking' : 'Disconnected'}</span>
          <span className="status-divider">·</span>
          <span>{selectedPlantId === 'all' ? 'All plants' : plants.find((plant) => plant.id === selectedPlantId)?.name || 'Plant'}</span>
        </span>
        <label className="plant-picker">
          <span className="sr-only">Select plant</span>
          <select className="plant-select" value={selectedPlantId} onChange={(event) => onSelectPlant(event.target.value)} aria-label="Select plant">
            <option value="all">All plants ({plants.length})</option>
            {plants.map((plant) => <option value={plant.id} key={plant.id}>{plant.code} · {plant.location}</option>)}
          </select>
        </label>
      </div>
      <div className="topbar-right">
        {demoData && <span className="demo-nav-wrap"><button className="demo-nav-pill" type="button" aria-label="Synthetic demo telemetry is enabled. Show data details." aria-expanded={demoInfoOpen} onClick={() => setDemoInfoOpen((open) => !open)} title="Synthetic demo telemetry is enabled">SYNTHETIC DATA</button>{demoInfoOpen && <span className="demo-nav-popover" role="status">{demoSummary}</span>}</span>}
        <span className="secure-label" tabIndex={0} data-tooltip="A human must approve before any work order is dispatched." title="A human must approve before any work order is dispatched."><ShieldCheck size={14} /> Approval-gated</span>
        <button className="icon-button chat-launch-button" type="button" onClick={onOpenChat} aria-label="Open Compunnel FieldNote assistant" title="Ask Compunnel FieldNote about live data"><MessageCircle size={17} /></button>
        <div className="notification-area">
          <button
            type="button"
            className={`icon-button notification-button ${overdueCount ? 'has-overdue' : ''}`}
            title={`${overdueCount} overdue · ${pendingCount} awaiting approval`}
            aria-label={`Open approvals, ${pendingCount} awaiting approval, ${overdueCount} overdue`}
            onClick={onOpenApprovals}
          >
            <Bell size={17} />
            {pendingCount > 0 && <span className="notification-count">{overdueCount || pendingCount}</span>}
          </button>
        </div>
        {authEnabled ? (
          <>
            <span className="authenticated-user" title={userRoles.join(', ')}>{userName || 'Signed in'}</span>
            <button className="icon-button sign-out-button" type="button" onClick={onSignOut} aria-label="Sign out" title="Sign out"><LogOut size={16} /></button>
          </>
        ) : <span className="operator-avatar" title="Local development identity">OP</span>}
      </div>
    </header>
  )
}
