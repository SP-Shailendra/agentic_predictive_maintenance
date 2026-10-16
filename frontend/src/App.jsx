import { Activity, AlertCircle, Check, Clock3, MessageCircle, RefreshCw, Search, ShieldCheck, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import AgentTerminalLog from './components/AgentTerminalLog.jsx'
import ApprovalModal from './components/ApprovalModal.jsx'
import AssetHistoryPanel from './components/AssetHistoryPanel.jsx'
import ChatDrawer from './components/ChatDrawer.jsx'
import KnowledgeLibrary from './components/KnowledgeLibrary.jsx'
import AssetThresholdEditor from './components/AssetThresholdEditor.jsx'
import AssetGrid from './components/AssetGrid.jsx'
import ApprovalsDrawer from './components/ApprovalsDrawer.jsx'
import Navbar from './components/Navbar.jsx'
import StatusPill from './components/StatusPill.jsx'
import SimulatorPanel from './components/SimulatorPanel.jsx'
import TelemetryChart from './components/TelemetryChart.jsx'
import TechnicianWorkView from './components/TechnicianWorkView.jsx'
import { api } from './services/api.js'
import { getUserDisplayName, getUserRoles, initializeAuth, oidcEnabled, signIn, signOut } from './services/auth.js'

function ageLabel(value) {
  if (!value) return 'Waiting for data'
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  return `${Math.floor(seconds / 60)}m ago`
}

function timeLabel(value) {
  return value
    ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '--:--:--'
}

function dateTimeLabel(value) {
  return value ? new Date(value).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }) : 'Date unavailable'
}

function relativeTimeLabel(value) {
  if (!value) return 'time unavailable'
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000)
  const absolute = Math.abs(seconds)
  const unit = absolute < 60 ? `${absolute}s` : absolute < 3600 ? `${Math.round(absolute / 60)}m` : absolute < 86400 ? `${Math.round(absolute / 3600)}h` : `${Math.round(absolute / 86400)}d`
  return seconds < 0 ? `in ${unit}` : `${unit} ago`
}

function parseHashRoute() {
  const [type, id] = window.location.hash.slice(1).split('/')
  return ['asset', 'workflow'].includes(type) && id ? { type, id } : null
}

const OPEN_WORKFLOW_STATUSES = new Set([
  'processing', 'awaiting_approval', 'dispatch_queued', 'dispatching', 'dispatch_retry', 'dispatched', 'in_progress',
])

export default function App() {
  const [plants, setPlants] = useState([])
  const [selectedPlantId, setSelectedPlantId] = useState('all')
  const [assets, setAssets] = useState([])
  const [dashboardScope, setDashboardScope] = useState(null)
  const refreshRequest = useRef(0)
  const [latestTelemetryByAsset, setLatestTelemetryByAsset] = useState([])
  const [assetSearch, setAssetSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [criticalityFilter, setCriticalityFilter] = useState('all')
  const [assetSort, setAssetSort] = useState('condition')
  const [managerView, setManagerView] = useState('operator')
  const [assetTab, setAssetTab] = useState('signals')
  const [showAllAssetWorkflows, setShowAllAssetWorkflows] = useState(false)
  const [selectedAsset, setSelectedAsset] = useState(null)
  const [telemetry, setTelemetry] = useState([])
  const [telemetryQuality, setTelemetryQuality] = useState(null)
  const [readingLimit, setReadingLimit] = useState(60)
  const [workflows, setWorkflows] = useState([])
  const [myWorkOrders, setMyWorkOrders] = useState([])
  const [managerMetrics, setManagerMetrics] = useState(null)
  const [managerMetricsScope, setManagerMetricsScope] = useState(null)
  const [managerMetricsUpdatedAt, setManagerMetricsUpdatedAt] = useState(null)
  const [managerRefreshVersion, setManagerRefreshVersion] = useState(0)
  const [managerMetricsLoading, setManagerMetricsLoading] = useState(false)
  const [managerMetricsError, setManagerMetricsError] = useState('')
  const [activeWorkflow, setActiveWorkflow] = useState(null)
  const [approvalsOpen, setApprovalsOpen] = useState(false)
  const [reviewTelemetry, setReviewTelemetry] = useState([])
  const [traceWorkflowId, setTraceWorkflowId] = useState(null)
  const autoOpenedWorkflows = useRef(new Set())
  const autoSelectedAssetId = useRef(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [auditEvents, setAuditEvents] = useState([])
  const [busy, setBusy] = useState(false)
  const [acknowledgingAlert, setAcknowledgingAlert] = useState(false)
  const [snoozingAlert, setSnoozingAlert] = useState(false)
  const [refreshing, setRefreshing] = useState(true)
  const [apiStatus, setApiStatus] = useState('checking')
  const [error, setError] = useState('')
  const [toast, setToast] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [healthStatus, setHealthStatus] = useState(null)
  const [scenarios, setScenarios] = useState([])
  const [scenarioError, setScenarioError] = useState('')
  const [authState, setAuthState] = useState({ loading: oidcEnabled, enabled: oidcEnabled, user: null, error: '' })
  const [hashRoute, setHashRoute] = useState(parseHashRoute)
  const authRoles = getUserRoles(authState.user)
  const hasRole = (...roles) => !authState.enabled || roles.some((role) => authRoles.includes(role))

  async function refreshAll() {
    const requestId = ++refreshRequest.current
    setRefreshing(true)
    setManagerRefreshVersion((version) => version + 1)
    try {
      const plantFilter = selectedPlantId === 'all' ? undefined : selectedPlantId
      const [nextPlants, nextAssets, nextWorkflows, nextHealth, nextLatestTelemetry, nextMyWork] = await Promise.all([
        api.plants(),
        api.assets(plantFilter),
        api.workflows(plantFilter),
        api.health(),
        api.latestTelemetry(),
        hasRole('technician') ? api.myWork() : Promise.resolve([]),
      ])
      if (requestId !== refreshRequest.current) return
      setPlants(nextPlants)
      setAssets(nextAssets)
      setDashboardScope(selectedPlantId)
      setWorkflows(nextWorkflows)
      setMyWorkOrders(nextMyWork)
      setHealthStatus(nextHealth)
      setApiStatus('connected')
      setLatestTelemetryByAsset(nextLatestTelemetry)
      if (nextHealth.simulator_enabled) {
        try {
          const nextScenarios = await api.scenarios()
          if (requestId !== refreshRequest.current) return
          setScenarios(nextScenarios)
          setScenarioError('')
        } catch (scenarioRequestError) {
          if (requestId !== refreshRequest.current) return
          setScenarioError(scenarioRequestError.message || 'Active scenarios could not be loaded.')
        }
      } else {
        setScenarios([])
        setScenarioError('')
      }
      setError('')
      setSelectedAsset((current) => nextAssets.find((asset) => asset.id === current?.id) || nextAssets[0] || null)
      setLastUpdated(new Date())
    } catch (requestError) {
      if (requestId !== refreshRequest.current) return
      setError(requestError.message || 'API request failed')
      setApiStatus('disconnected')
    } finally {
      if (requestId === refreshRequest.current) setRefreshing(false)
    }
  }

  useEffect(() => {
    let active = true
    const handleAuthExpired = () => {
      setAuthState({ loading: false, enabled: true, user: null, error: 'Your sign-in session expired or is no longer valid. Please sign in again.' })
    }
    window.addEventListener('fieldnote:auth-expired', handleAuthExpired)
    initializeAuth()
      .then((result) => { if (active) setAuthState({ ...result, loading: false, error: '' }) })
      .catch((authError) => {
        if (active) setAuthState({ loading: false, enabled: true, user: null, error: authError.message || 'Sign-in failed.' })
      })
    return () => {
      active = false
      window.removeEventListener('fieldnote:auth-expired', handleAuthExpired)
    }
  }, [])

  useEffect(() => {
    const updateRoute = () => setHashRoute(parseHashRoute())
    window.addEventListener('hashchange', updateRoute)
    return () => window.removeEventListener('hashchange', updateRoute)
  }, [])

  useEffect(() => {
    if (!hashRoute) {
      setActiveWorkflow(null)
      return undefined
    }
    if (authState.loading || (authState.enabled && !authState.user)) return undefined
    let active = true
    setManagerView('operator')
    async function loadRoute() {
      try {
        if (hashRoute.type === 'asset') {
          const asset = assets.find((item) => item.id === hashRoute.id) || await api.asset(hashRoute.id)
          if (!active) return
          setSelectedAsset(asset)
          setAssetTab('signals')
          setActiveWorkflow(null)
        } else {
          const workflow = await api.workflow(hashRoute.id)
          const asset = assets.find((item) => item.id === workflow.asset_id) || await api.asset(workflow.asset_id)
          if (!active) return
          setSelectedAsset(asset)
          setAssetTab('workflows')
          setActiveWorkflow(workflow)
          setTraceWorkflowId(workflow.id)
        }
      } catch (requestError) {
        if (active) setToast({ message: requestError.message || 'The requested record is unavailable.', type: 'error' })
      }
    }
    void loadRoute()
    return () => { active = false }
  }, [hashRoute?.type, hashRoute?.id, authState.loading, authState.user?.profile?.sub])

  useEffect(() => {
    if (authState.loading || (authState.enabled && !authState.user)) return undefined
    refreshAll()
    const timer = window.setInterval(refreshAll, 6000)
    return () => { window.clearInterval(timer); refreshRequest.current += 1 }
  }, [selectedPlantId, authState.loading, authState.user?.profile?.sub])

  useEffect(() => {
    if (managerView !== 'manager' || authState.loading || (authState.enabled && !authState.user)) return undefined
    let active = true
    const plantFilter = selectedPlantId === 'all' ? undefined : selectedPlantId
    setManagerMetricsLoading(true)
    setManagerMetricsError('')
    api.maintenanceMetrics(plantFilter)
      .then((metrics) => {
        if (active) {
          setManagerMetrics(metrics)
          setManagerMetricsScope(selectedPlantId)
          setManagerMetricsUpdatedAt(new Date())
        }
      })
      .catch((requestError) => { if (active) setManagerMetricsError(requestError.message || 'Manager metrics are unavailable for this role.') })
      .finally(() => { if (active) setManagerMetricsLoading(false) })
    return () => { active = false }
  }, [managerView, selectedPlantId, managerRefreshVersion, authState.loading, authState.user?.profile?.sub])

  useEffect(() => {
    if (authState.loading || (authState.enabled && !authState.user)) return undefined
    setTelemetryQuality(null)
    if (!selectedAsset) {
      setTelemetry([])
      return undefined
    }
    let active = true
    const load = async () => {
      try {
        const rows = await api.telemetry(selectedAsset.id, readingLimit)
        if (active) {
          setTelemetry(rows)
          setError('')
        }
      } catch (requestError) {
        if (active) setError(requestError.message || 'Telemetry request failed')
      }
    }
    load()
    const timer = window.setInterval(load, 6000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [selectedAsset?.id, readingLimit, authState.loading, authState.user?.profile?.sub])

  useEffect(() => {
    if (authState.loading || (authState.enabled && !authState.user)) return undefined
    if (!traceWorkflowId) {
      setAuditEvents([])
      return undefined
    }
    let active = true
    api.workflowEvents(traceWorkflowId)
      .then((events) => { if (active) setAuditEvents(events) })
      .catch((requestError) => { if (active) setError(requestError.message || 'Workflow event request failed') })
    return () => { active = false }
  }, [traceWorkflowId, authState.loading, authState.user?.profile?.sub])

  useEffect(() => {
    if (authState.loading || (authState.enabled && !authState.user) || !activeWorkflow?.asset_id) {
      setReviewTelemetry([])
      return undefined
    }
    let active = true
    api.telemetry(activeWorkflow.asset_id, 1)
      .then((readings) => { if (active) setReviewTelemetry(readings) })
      .catch(() => { if (active) setReviewTelemetry([]) })
    return () => { active = false }
  }, [activeWorkflow?.id, authState.loading, authState.user?.profile?.sub])

  useEffect(() => {
    if (refreshing || !selectedAsset) return
    const assetChanged = autoSelectedAssetId.current !== selectedAsset.id
    autoSelectedAssetId.current = selectedAsset.id
    const assetWorkflow = workflows.find((workflow) => workflow.asset_id === selectedAsset?.id)
    const candidate = assetWorkflow || workflows.find((workflow) => workflow.status === 'awaiting_approval')
    if (candidate && (assetChanged || !traceWorkflowId || !autoOpenedWorkflows.current.has(candidate.id))) {
      autoOpenedWorkflows.current.add(candidate.id)
      setTraceWorkflowId(candidate.id)
    } else if (!candidate && traceWorkflowId) {
      setTraceWorkflowId(null)
    }
  }, [selectedAsset?.id, workflows, traceWorkflowId, refreshing])

  useEffect(() => {
    if (!toast) return undefined
    const timer = window.setTimeout(() => setToast(null), 3600)
    return () => window.clearTimeout(timer)
  }, [toast])

  const pending = workflows.filter((item) => item.status === 'awaiting_approval')
    .sort((left, right) => Number(Boolean(right.workflow_state?.approval_overdue)) - Number(Boolean(left.workflow_state?.approval_overdue))
      || new Date(left.created_at) - new Date(right.created_at))
  const overdueCount = pending.filter((item) => item.workflow_state?.approval_overdue).length
  const recentlyDecided = workflows.filter((item) => item.status !== 'awaiting_approval')
    .sort((left, right) => new Date(right.created_at) - new Date(left.created_at))
  const attentionCount = assets.filter((asset) => asset.alert_active).length
  const telemetryByAsset = latestTelemetryByAsset.reduce((grouped, reading) => {
    if (!grouped.has(reading.asset_id)) grouped.set(reading.asset_id, [])
    grouped.get(reading.asset_id).push(new Date(reading.timestamp).getTime())
    return grouped
  }, new Map())
  const staleCount = assets.filter((asset) => {
    const times = (telemetryByAsset.get(asset.id) || []).sort((left, right) => left - right)
    if (times.length < 3) return false
    const intervals = times.slice(1).map((time, index) => time - times[index]).filter((interval) => interval > 0).sort((left, right) => left - right)
    if (!intervals.length) return false
    const middle = Math.floor(intervals.length / 2)
    const cadence = intervals.length % 2 ? intervals[middle] : (intervals[middle - 1] + intervals[middle]) / 2
    return Date.now() - times.at(-1) > Math.max(cadence * 3, 15000)
  }).length
  const activeReading = telemetry.at(-1)
  const activeScenarios = scenarios.filter((scenario) => scenario.active)
  const selectedScenario = activeScenarios.find((scenario) => scenario.asset_id === selectedAsset?.id)
  const windowDurationMinutes = telemetry.length > 1
    ? Math.max(1, Math.round((new Date(telemetry.at(-1).timestamp) - new Date(telemetry[0].timestamp)) / 60000))
    : 0
  const selectedPlant = plants.find((plant) => plant.id === selectedPlantId)
  const reviewAsset = assets.find((asset) => asset.id === activeWorkflow?.asset_id)
  const traceWorkflow = workflows.find((item) => item.id === traceWorkflowId) || (activeWorkflow?.id === traceWorkflowId ? activeWorkflow : null)
  const assetWorkflows = workflows.filter((workflow) => workflow.asset_id === selectedAsset?.id)
  const assetOpenWorkflow = workflows.find((workflow) => workflow.asset_id === selectedAsset?.id && OPEN_WORKFLOW_STATUSES.has(workflow.status))
  const normalizedSearch = assetSearch.trim().toLowerCase()
  const visibleAssets = assets.filter((asset) => {
    const matchesSearch = !normalizedSearch || [asset.name, asset.asset_tag, asset.asset_type, asset.location, asset.id]
      .some((value) => value.toLowerCase().includes(normalizedSearch))
    const matchesStatus = statusFilter === 'all' || (statusFilter === 'attention' ? asset.alert_active : asset.status === statusFilter)
    const matchesCriticality = criticalityFilter === 'all' || asset.criticality === criticalityFilter
    return matchesSearch && matchesStatus && matchesCriticality
  }).sort((left, right) => {
    const criticalityOrder = { critical: 0, high: 1, medium: 2, low: 3 }
    if (assetSort === 'condition') {
      return Number(right.alert_active) - Number(left.alert_active)
        || (criticalityOrder[left.criticality] ?? 4) - (criticalityOrder[right.criticality] ?? 4)
        || left.asset_tag.localeCompare(right.asset_tag, undefined, { numeric: true })
    }
    if (assetSort === 'criticality') {
      return (criticalityOrder[left.criticality] ?? 4) - (criticalityOrder[right.criticality] ?? 4)
        || left.asset_tag.localeCompare(right.asset_tag, undefined, { numeric: true })
    }
    if (assetSort === 'vibration') return (right.latest_vibration_mm_s ?? -1) - (left.latest_vibration_mm_s ?? -1)
    if (assetSort === 'temperature') return (right.latest_temperature_c ?? -1) - (left.latest_temperature_c ?? -1)
    return left.asset_tag.localeCompare(right.asset_tag, undefined, { numeric: true })
  })
  const activeFilters = [
    normalizedSearch && `Search: ${assetSearch.trim()}`,
    statusFilter !== 'all' && `Condition: ${statusFilter}`,
    criticalityFilter !== 'all' && `Criticality: ${criticalityFilter}`,
  ].filter(Boolean)
  const selectedTrace = useMemo(() => {
    const current = workflows.find((item) => item.id === traceWorkflowId) || traceWorkflow
    const activity = current?.workflow_state?.activity || []
    const audit = auditEvents.map((event) => ({
      agent: 'Audit',
      status: event.details?.decision === 'rejected' ? 'rejected' : 'complete',
      created_at: event.created_at,
      message: `${event.event_type.replaceAll('_', ' ')}${event.from_status ? `: ${event.from_status} -> ${event.to_status}` : ''}${event.details?.comment ? ` · ${event.details.comment}` : ''}`,
    }))
    return [...activity, ...audit]
  }, [workflows, traceWorkflowId, traceWorkflow, auditEvents])

  async function acknowledgeAlert() {
    if (!selectedAsset || acknowledgingAlert) return
    setAcknowledgingAlert(true)
    try {
      const result = await api.acknowledgeAssetAlert(selectedAsset.id)
      await refreshAll()
      setToast({ message: result.alert_active ? 'Alert acknowledged. Recovery is still pending.' : 'Alert cleared after confirmed recovery.', type: 'success' })
    } catch (requestError) {
      setToast({ message: requestError.message || 'Could not acknowledge alert.', type: 'error' })
    } finally {
      setAcknowledgingAlert(false)
    }
  }

  async function snoozeAlert() {
    if (!selectedAsset || snoozingAlert) return
    setSnoozingAlert(true)
    try {
      const result = await api.snoozeAssetAlert(selectedAsset.id, 30, 'Operator requested a temporary quiet period.')
      await refreshAll()
      setToast({ message: `Alert snoozed until ${dateTimeLabel(result.alert_snoozed_until)}.`, type: 'success' })
    } catch (requestError) {
      setToast({ message: requestError.message || 'Could not snooze alert.', type: 'error' })
    } finally {
      setSnoozingAlert(false)
    }
  }

  async function investigate() {
    if (!selectedAsset) return
    const existing = workflows.find((workflow) => workflow.asset_id === selectedAsset.id && OPEN_WORKFLOW_STATUSES.has(workflow.status))
    if (existing) {
      navigateToWorkflow(existing.id)
      setToast({ message: existing.status === 'awaiting_approval' ? 'An investigation is already awaiting approval.' : 'An investigation is already open for this asset.', type: 'success' })
      return
    }
    setBusy(true)
    try {
      const latest = telemetry.at(-1)
      const result = await api.startWorkflow({
        asset_id: selectedAsset.id,
        alert_type: latest?.vibration_mm_s >= selectedAsset.vibration_threshold_mm_s ? 'High vibration' : latest?.temperature_c >= selectedAsset.temperature_threshold_c ? 'High temperature' : 'Operator investigation',
        severity: selectedAsset.criticality === 'critical' ? 'critical' : selectedAsset.status === 'attention' ? 'high' : 'medium',
        message: latest
          ? `Operator review requested for ${selectedAsset.asset_tag}. Latest vibration ${latest.vibration_mm_s.toFixed(1)} mm/s, temperature ${latest.temperature_c.toFixed(1)} °C.`
          : `Operator review requested for ${selectedAsset.asset_tag}. No recent telemetry is available.`,
        telemetry: latest ? {
          vibration_mm_s: latest.vibration_mm_s,
          temperature_c: latest.temperature_c,
          pressure_bar: latest.pressure_bar,
          rpm: latest.rpm,
        } : {},
      })
      await refreshAll()
      setActiveWorkflow(result)
      setTraceWorkflowId(result.id)
      navigateToWorkflow(result.id)
      setToast({ message: 'Diagnostic review is ready.', type: 'success' })
    } catch (requestError) {
      setToast({ message: requestError.message || 'Could not start investigation.', type: 'error' })
    } finally {
      setBusy(false)
    }
  }

  async function decide(workOrderId, comment, action, acknowledgeStale = false) {
    setBusy(true)
    try {
      const result = action === 'approve'
        ? await api.approveWorkflow(workOrderId, comment, acknowledgeStale)
        : await api.rejectWorkflow(workOrderId, comment)
      await refreshAll()
      setActiveWorkflow(result)
      setTraceWorkflowId(result.id)
      setToast({ message: action === 'approve' ? 'Approval recorded; dispatch queued.' : 'Recommendation rejected and recorded.', type: 'success' })
      return { ok: true }
    } catch (requestError) {
      const message = requestError.message || 'Decision could not be saved.'
      if (action === 'approve' && message.toLowerCase().includes('requires acknowledgement')) {
        try { setActiveWorkflow(await api.workflow(workOrderId)) } catch { /* Keep the existing review context. */ }
      }
      setToast({ message, type: 'error' })
      return { ok: false, message }
    } finally {
      setBusy(false)
    }
  }

  async function updateLifecycle(workOrderId, status) {
    setBusy(true)
    try {
      const result = await api.advanceWorkflow(workOrderId, status)
      await refreshAll()
      setActiveWorkflow(result)
      setTraceWorkflowId(result.id)
      setToast({ message: `Work order marked ${status.replaceAll('_', ' ')}.`, type: 'success' })
      return { ok: true }
    } catch (requestError) {
      return { ok: false, message: requestError.message || 'Lifecycle update failed.' }
    } finally {
      setBusy(false)
    }
  }

  async function closeOut(workOrderId, payload) {
    setBusy(true)
    try {
      const result = await api.closeOutWorkflow(workOrderId, payload)
      await refreshAll()
      setActiveWorkflow(result)
      setTraceWorkflowId(result.id)
      setToast({ message: 'Technician close-out recorded.', type: 'success' })
      return { ok: true }
    } catch (requestError) {
      return { ok: false, message: requestError.message || 'Close-out could not be saved.' }
    } finally {
      setBusy(false)
    }
  }

  async function assignWork(workOrderId, assignedToSub) {
    try {
      await api.assignWorkflow(workOrderId, assignedToSub)
      await refreshAll()
      return { ok: true }
    } catch (requestError) {
      return { ok: false, message: requestError.message || 'Assignment could not be saved.' }
    }
  }

  function navigateToAsset(assetId) {
    if (!assetId) return
    setChatOpen(false)
    const hash = `#asset/${assetId}`
    if (window.location.hash !== hash) window.location.hash = hash
    else setHashRoute({ type: 'asset', id: assetId })
  }

  function navigateToWorkflow(workflowId) {
    if (!workflowId) return
    setChatOpen(false)
    const hash = `#workflow/${workflowId}`
    if (window.location.hash !== hash) window.location.hash = hash
    else setHashRoute({ type: 'workflow', id: workflowId })
  }

  function closeWorkflow() {
    if (hashRoute?.type === 'workflow' && activeWorkflow?.asset_id) {
      const hash = `#asset/${activeWorkflow.asset_id}`
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${hash}`)
      setHashRoute({ type: 'asset', id: activeWorkflow.asset_id })
    }
    setActiveWorkflow(null)
  }

  function clearFilters() {
    setAssetSearch('')
    setStatusFilter('all')
    setCriticalityFilter('all')
  }

  const selectedAssetTitle = selectedAsset ? `${selectedAsset.asset_tag} · ${selectedAsset.name}` : 'Select an asset'

  if (authState.loading) {
    return <main className="auth-gate"><div className="auth-gate-panel"><span className="brand-name">Compunnel FieldNote</span><p>Checking secure sign-in…</p></div></main>
  }

  if (authState.enabled && !authState.user) {
    return (
      <main className="auth-gate">
        <section className="auth-gate-panel">
          <span className="brand-name">Compunnel FieldNote</span>
          <h1>Sign in to Fieldnote</h1>
          <p>Use your organization account to review plant telemetry and maintenance workflows.</p>
          {authState.error && <p className="auth-gate-error" role="alert">{authState.error}</p>}
          <button className="button button-primary" type="button" onClick={() => signIn().catch((authError) => setAuthState((current) => ({ ...current, error: authError.message })))}>Sign in</button>
        </section>
      </main>
    )
  }

  return (
    <div className={`app-shell ${managerView === 'manager' ? 'manager-mode' : ''} ${managerView === 'technician' ? 'technician-mode' : ''} ${managerView === 'knowledge' ? 'knowledge-mode' : ''}`} id="overview">
      <Navbar
        plants={plants}
        selectedPlantId={selectedPlantId}
        onSelectPlant={(plantId) => {
          setSelectedPlantId(plantId)
          setActiveWorkflow(null)
        }}
        pendingNotifications={pending}
        overdueCount={overdueCount}
        connected={apiStatus === 'connected'}
        connectionStatus={apiStatus}
        authEnabled={authState.enabled}
        userName={getUserDisplayName(authState.user)}
        userRoles={authRoles}
        onSignOut={signOut}
        onOpenChat={() => setChatOpen(true)}
        onOpenApprovals={() => setApprovalsOpen(true)}
        demoData={Boolean(healthStatus?.simulator_enabled)}
        demoSummary={`Synthetic readings are stored with source edge-simulator and appear in asset rows and Signals. Default advisory limits: vibration ${healthStatus?.default_vibration_threshold_mm_s ?? '—'} mm/s, temperature ${healthStatus?.default_temperature_threshold_c ?? '—'} °C.${activeScenarios.length ? ` Active scenarios: ${activeScenarios.map((scenario) => scenario.scenario.replaceAll('_', ' ')).join(', ')}.` : ''}`}
      />
      <ChatDrawer
        open={chatOpen}
        onClose={setChatOpen}
        plant={selectedPlant || { id: null, code: 'All plants' }}
        asset={selectedAsset}
        onSelectAsset={navigateToAsset}
        onOpenWorkflow={navigateToWorkflow}
        onRunDiagnosis={hasRole('operator', 'reliability_engineer') ? () => { setChatOpen(false); void investigate() } : null}
      />
      {refreshing && <div className="loading-bar" aria-hidden="true" />}
      <main className="page-wrap">
        <section className="page-heading">
          <div>
            <p className="eyebrow">CONDITION INTELLIGENCE / SHIFT A</p>
            <h1>{managerView === 'knowledge' ? 'Maintenance Knowledge' : 'Maintenance Overview'}</h1>
            <p className="heading-subtitle">
              {managerView === 'knowledge'
                ? `${selectedPlant?.name || 'All plants'} · controlled maintenance references`
                : <>{selectedPlant?.name || 'All plants'} · {assets.length} monitored assets · Updated <span className="mono">{dateTimeLabel(lastUpdated)}</span> ({ageLabel(lastUpdated)})</>}
            </p>
          </div>
          <div className="heading-right">
            {(hasRole('manager', 'technician', 'reliability_engineer')) && <div className="view-switch" role="group" aria-label="Dashboard view">
              {hasRole('operator', 'reliability_engineer', 'approver') && <button type="button" aria-pressed={managerView === 'operator'} onClick={() => setManagerView('operator')}>Operator</button>}
              {hasRole('manager') && <button type="button" aria-pressed={managerView === 'manager'} onClick={() => setManagerView('manager')}>Manager metrics</button>}
              {hasRole('manager', 'technician', 'reliability_engineer') && <button type="button" aria-pressed={managerView === 'technician'} onClick={() => setManagerView('technician')}>{hasRole('technician') ? 'My work' : 'Technician assignments'}</button>}
              {hasRole('manager', 'reliability_engineer') && <button type="button" aria-pressed={managerView === 'knowledge'} onClick={() => setManagerView('knowledge')}>Knowledge</button>}
            </div>}
            <button className="button button-primary" type="button" onClick={refreshAll} disabled={refreshing} title="Refresh plant data">
              <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />{refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </section>
        {refreshing && <div className="header-progress" role="progressbar" aria-label="Refreshing plant data" />}

        {error && (
          <div className="api-error" role="alert">
            <AlertCircle size={15} aria-hidden="true" />
            <span>Fieldnote API request failed. {error}{lastUpdated ? ` Showing last data from ${timeLabel(lastUpdated)}.` : ' Check API availability and request limits.'}</span>
            <button className="button button-quiet" type="button" onClick={refreshAll} disabled={refreshing}>Retry</button>
          </div>
        )}
        {scenarioError && <p className="scenario-load-error" role="status">{scenarioError}</p>}

        {managerView === 'manager' && <div className="manager-metrics-status" role="status">
          {managerMetricsLoading
            ? 'Updating manager metrics...'
            : managerMetricsError ? (managerMetricsScope === selectedPlantId ? 'Manager metrics are stale.' : 'Manager metrics unavailable.')
              : managerMetricsScope !== selectedPlantId ? 'Updating manager metrics...' : `Metrics updated ${dateTimeLabel(managerMetricsUpdatedAt)} (${ageLabel(managerMetricsUpdatedAt)})`}
        </div>}
        {managerView === 'manager' && <section className="manager-action-queue" aria-labelledby="manager-queue-title" aria-busy={refreshing}>
          <header className="manager-queue-heading">
            <div><h2 id="manager-queue-title"><Clock3 size={17} />Approval priorities</h2><p>{dashboardScope === selectedPlantId ? `${overdueCount} overdue · ${pending.length} awaiting approval · all orders, including demo` : 'Loading selected plant approvals...'}</p></div>
            <button className="button button-quiet" type="button" onClick={() => setApprovalsOpen(true)} disabled={dashboardScope !== selectedPlantId}><ShieldCheck size={14} />Review queue</button>
          </header>
          {dashboardScope === selectedPlantId && (pending.length ? <ol className="manager-approval-list">
            {pending.slice(0, 8).map((workflow) => {
              const asset = assets.find((item) => item.id === workflow.asset_id)
              const state = workflow.workflow_state || {}
              const assetTag = asset?.asset_tag || state.asset_tag || 'Asset'
              return <li className={`manager-approval-row ${state.approval_overdue ? 'is-overdue' : ''}`} key={workflow.id}>
                <div className="manager-approval-asset"><strong>{assetTag} · {asset?.name || state.asset_name || 'Unknown asset'}</strong><span>{workflow.summary}</span></div>
                <div className="manager-approval-priority"><span>Criticality</span><strong>{asset?.criticality || 'Not recorded'}</strong>{state.simulated_only && <small>Demo order</small>}</div>
                <div className="manager-approval-age">{state.approval_overdue ? <strong className="manager-overdue"><AlertCircle size={13} />Overdue</strong> : <strong>Awaiting approval</strong>}<span>Requested {relativeTimeLabel(workflow.created_at)}</span></div>
                <button className="button button-quiet" type="button" onClick={() => navigateToWorkflow(workflow.id)} aria-label={`Review approval for ${assetTag}`}><ShieldCheck size={14} />Review</button>
              </li>
            })}
          </ol> : <p className="manager-queue-empty"><Check size={15} />No approvals waiting in this plant scope.</p>)}
          {dashboardScope === selectedPlantId && pending.length > 8 && <button className="button button-quiet manager-queue-more" type="button" onClick={() => setApprovalsOpen(true)}>View all {pending.length} approvals</button>}
        </section>}
        {managerView === 'manager' && <div className="manager-performance-heading"><h2>Maintenance performance</h2><span>All time · non-simulated orders</span></div>}
        {managerView === 'manager' && managerMetricsScope === selectedPlantId && managerMetrics && (
          <section className="manager-metrics-panel" aria-label="Manager maintenance metrics">
            <div className={`manager-metric ${managerMetrics.usefulness_sample_count > 0 && managerMetrics.usefulness_sample_count < 5 ? 'manager-metric-preliminary' : ''}`}><span>Alerts confirmed useful</span><strong>{managerMetrics.useful_alert_rate == null ? 'Not measured' : `${(managerMetrics.useful_alert_rate * 100).toFixed(0)}%`}</strong><small>{managerMetrics.usefulness_sample_count ? `${managerMetrics.usefulness_sample_count} responses${managerMetrics.usefulness_sample_count < 5 ? ' · preliminary' : ''}` : 'No close-out feedback'} · target ≥{Math.round(managerMetrics.targets.useful_alert_rate * 100)}%</small></div>
            <div className={`manager-metric ${managerMetrics.root_cause_sample_count > 0 && managerMetrics.root_cause_sample_count < 5 ? 'manager-metric-preliminary' : ''}`}><span>Top-3 root-cause accuracy</span><strong>{managerMetrics.top_three_root_cause_accuracy == null ? 'Not measured' : `${(managerMetrics.top_three_root_cause_accuracy * 100).toFixed(0)}%`}</strong><small>{managerMetrics.root_cause_sample_count ? `${managerMetrics.root_cause_sample_count} evaluated close-outs${managerMetrics.root_cause_sample_count < 5 ? ' · preliminary' : ''}` : 'No evaluated close-outs'} · target ≥{Math.round(managerMetrics.targets.top_three_root_cause_accuracy * 100)}%</small></div>
            <div className={`manager-metric ${managerMetrics.dispatch_sample_count > 0 && managerMetrics.dispatch_sample_count < 5 ? 'manager-metric-preliminary' : ''}`}><span>Approval to CMMS acceptance · p95</span><strong>{managerMetrics.approval_to_dispatch_p95_seconds == null ? 'Not measured' : `${(managerMetrics.approval_to_dispatch_p95_seconds / 60).toFixed(1)} min`}</strong><small>{managerMetrics.dispatch_sample_count ? `${managerMetrics.dispatch_sample_count} real dispatches${managerMetrics.dispatch_sample_count < 5 ? ' · preliminary' : ''}` : 'No real CMMS acceptances'} · target ≤{(managerMetrics.targets.approval_to_dispatch_p95_seconds / 60).toFixed(0)} min</small><small>{managerMetrics.simulated_dispatch_count} simulated dispatches excluded</small></div>
            <div className="manager-metric manager-lifecycle"><span>Non-simulated lifecycle</span><div>{Object.entries(managerMetrics.status_counts || {}).map(([status, count]) => <span key={status}>{status.replaceAll('_', ' ')} <b>{count}</b></span>)}</div>{!Object.keys(managerMetrics.status_counts || {}).length && <small>No non-simulated orders</small>}</div>
          </section>
        )}
        {managerView === 'manager' && managerMetricsError && <p className="manager-metrics-error" role="alert">{managerMetricsError}</p>}

        {managerView === 'technician' && <TechnicianWorkView
          workOrders={myWorkOrders}
          candidates={workflows.filter((workflow) => OPEN_WORKFLOW_STATUSES.has(workflow.status)).slice(0, 20)}
          canAssign={hasRole('manager', 'reliability_engineer')}
          isTechnician={hasRole('technician')}
          onOpenWorkflow={navigateToWorkflow}
          onAssign={assignWork}
        />}
        {managerView === 'knowledge' && <KnowledgeLibrary plantId={selectedPlantId} />}

        <section className="metrics-strip status-strip" aria-label="Plant status">
          <button className={`metric metric-button ${attentionCount ? 'alert' : ''}`} type="button" aria-label={`Filter to ${attentionCount} assets needing attention`} onClick={() => setStatusFilter(statusFilter === 'attention' ? 'all' : 'attention')}>
            <span className="metric-label"><AlertCircle size={13} /> Needing attention</span>
            <span className="metric-value">{attentionCount}<small>{attentionCount === 1 ? 'asset' : 'assets'}</small></span>
            <div className="metric-note">{attentionCount ? 'Open condition alerts' : 'No open condition alerts'}</div>
          </button>
          <button className="metric metric-button" type="button" aria-label={`Open ${pending.length} pending approvals`} onClick={() => setApprovalsOpen(true)}>
            <span className="metric-label"><ShieldCheck size={13} /> Pending approvals</span>
            <span className="metric-value">{pending.length}<small>orders</small></span>
            <div className="metric-note">{pending.length ? 'Oldest waiting first' : 'No approvals waiting'}</div>
          </button>
          <div className="metric"><span className="metric-label"><Clock3 size={13} /> Stale telemetry assets (est.)</span><span className="metric-value">{staleCount}<small>assets</small></span><div className="metric-note">{staleCount ? 'Readings may need refresh' : 'No assets estimated stale'}</div></div>
        </section>

        <section className="dashboard-grid">
          <div className="asset-column">
            <section className="panel assets-panel" aria-labelledby="asset-health-title">
              <div className="panel-header">
                <div className="panel-title"><div><h2 id="asset-health-title">Asset Health</h2><p>{selectedPlant ? `${selectedPlant.code} · ${selectedPlant.location}` : 'Portfolio · all plant locations'}</p></div></div>
                <span className="section-label mono">{visibleAssets.length} of {assets.length} shown</span>
              </div>
              <div className="asset-toolbar">
                <label className="asset-search"><Search size={14} aria-hidden="true" /><input value={assetSearch} onChange={(event) => setAssetSearch(event.target.value)} placeholder="Search asset ID or location" aria-label="Search asset ID, name, or location" /></label>
                <label className="asset-filter"><span className="sr-only">Condition</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter by condition"><option value="all">Condition: All</option><option value="attention">Attention: Open alerts</option><option value="operational">Condition: Normal</option><option value="offline">Condition: Stale (est.)</option></select></label>
                <label className="asset-filter"><span className="sr-only">Criticality</span><select value={criticalityFilter} onChange={(event) => setCriticalityFilter(event.target.value)} aria-label="Filter by criticality"><option value="all">Criticality: All</option><option value="critical">Criticality: Critical</option><option value="high">Criticality: High</option><option value="medium">Criticality: Medium</option><option value="low">Criticality: Low</option></select></label>
                <label className="asset-filter asset-sort"><span className="sr-only">Sort assets</span><select value={assetSort} onChange={(event) => setAssetSort(event.target.value)} aria-label="Sort assets"><option value="condition">Sort: Condition</option><option value="criticality">Sort: Criticality</option><option value="vibration">Sort: Vibration</option><option value="temperature">Sort: Temperature</option><option value="id">Sort: ID</option></select></label>
              </div>
              <div className="asset-column-head" aria-hidden="true"><span>ID</span><span>Equipment</span><span>Vib · mm/s</span><span>Temp · °C</span><span>Condition</span></div>
              {refreshing && assets.length === 0
                ? <div className="asset-skeleton" aria-label="Loading assets"><i /><i /><i /></div>
                : <AssetGrid
                    assets={visibleAssets}
                    selectedId={selectedAsset?.id}
                    scenarios={[...activeScenarios, ...(activeReading?.source?.startsWith('scenario:') ? [{ asset_id: selectedAsset?.id, scenario: activeReading.source.slice(9) }] : [])]}
                    onSelect={(asset) => { setSelectedAsset(asset); setActiveWorkflow(null); setTraceWorkflowId(null); navigateToAsset(asset.id) }}
                    activeFilters={activeFilters}
                    onClearFilters={clearFilters}
                  />}
            </section>
                  </div>

          <div className="detail-column">
            <section className="panel chart-panel" aria-labelledby="selected-asset-heading">
              <div className="selected-asset-header">
                <div className="selected-asset-copy">
                  <span className="selected-kicker mono">SELECTED ASSET</span>
                  <div className="selected-title-row">
                    <h2 id="selected-asset-heading">{selectedAssetTitle}</h2>
                    {selectedAsset && <StatusPill status={selectedAsset.status} />}
                    {activeReading?.source === 'edge-simulator' && <span className="synthetic-source-chip" title="Latest displayed reading was generated by the synthetic telemetry simulator">Synthetic reading</span>}
                    {selectedAsset?.alert_active && <span className="attention-suggestion">Open condition alert</span>}
                    {(selectedScenario || activeReading?.source?.startsWith('scenario:')) && <span className="scenario-chip">{selectedScenario?.scenario.replaceAll('_', ' ') || activeReading.source.slice(9)} · demo</span>}
                    {hasRole('operator', 'reliability_engineer') && <button className="button button-primary button-investigate" type="button" onClick={investigate} disabled={!selectedAsset || busy || Boolean(assetOpenWorkflow)} title={assetOpenWorkflow ? 'An investigation is already open for this asset.' : undefined}>
                      <Zap size={14} />{busy ? 'Working…' : 'Run diagnosis'}
                    </button>}
                    <button className="button button-quiet asset-chat-button" type="button" onClick={() => setChatOpen(true)} disabled={!selectedAsset} title="Ask about this asset"><MessageCircle size={14} />Ask about this asset</button>
                    {assetOpenWorkflow && <button className="button button-quiet asset-pending-link" type="button" onClick={() => navigateToWorkflow(assetOpenWorkflow.id)}>{assetOpenWorkflow.status === 'awaiting_approval' ? 'An investigation is already awaiting approval · Review' : `Investigation ${assetOpenWorkflow.status.replaceAll('_', ' ')} · View`}</button>}
                  </div>
                  {selectedAsset && <>
                    <p className="selected-asset-meta">{selectedAsset.location} · Criticality {selectedAsset.criticality} · Last reading {dateTimeLabel(activeReading?.timestamp)} ({ageLabel(activeReading?.timestamp)})</p>
                    {selectedAsset.alert_active && <div className="asset-alert-strip" role="status">
                      <AlertCircle size={15} aria-hidden="true" />
                      <div className="asset-alert-copy"><strong>{selectedAsset.alert_acknowledged_at ? 'Acknowledged · recovery pending' : 'Awaiting acknowledgement'}</strong><span>Opened {dateTimeLabel(selectedAsset.alert_started_at)} · {Date.now() - new Date(selectedAsset.alert_last_reading_at).getTime() > 60000 ? 'Recovery readings stale' : `Recovery: ${selectedAsset.alert_normal_readings}/3 consecutive normal readings`}{selectedAsset.alert_snoozed_until && new Date(selectedAsset.alert_snoozed_until) > new Date() ? ` · Snoozed until ${dateTimeLabel(selectedAsset.alert_snoozed_until)}` : ''}</span></div>
                      {hasRole('operator', 'reliability_engineer') && <button className="button button-quiet" type="button" onClick={snoozeAlert} disabled={snoozingAlert}><Clock3 size={14} />{snoozingAlert ? 'Snoozing...' : 'Snooze 30 min'}</button>}
                      {hasRole('operator', 'reliability_engineer') && !selectedAsset.alert_acknowledged_at && <button className="button button-quiet" type="button" onClick={acknowledgeAlert} disabled={acknowledgingAlert}><Check size={14} />{acknowledgingAlert ? 'Acknowledging...' : 'Acknowledge alert'}</button>}
                    </div>}
                    <div className="asset-quality-row" aria-live="polite">
                      <span className={telemetryQuality?.stale ? 'quality-warning' : 'quality-good'}><Clock3 size={13} />Freshness: {telemetryQuality?.freshness || 'establishing (est.)'}</span>
                      <span className={telemetryQuality?.rangeIssue ? 'quality-warning' : 'quality-good'}>{telemetryQuality?.rangeIssue ? <AlertCircle size={13} /> : <Check size={13} />}{telemetryQuality?.rangeIssue ? `Range check failed: ${telemetryQuality.rangeIssue}` : 'Range check passed'}</span>
                    </div>
                  </>}
                </div>
                <label className="reading-limit-control"><span>Window</span><select value={readingLimit} onChange={(event) => setReadingLimit(Number(event.target.value))} aria-label="Telemetry window in readings"><option value={30}>30 readings</option><option value={60}>60 readings</option><option value={120}>120 readings</option><option value={500}>500 readings</option></select><small>about {windowDurationMinutes || '—'} min</small></label>
              </div>
              <div className="asset-tabs" role="tablist" aria-label="Selected asset views">
                {[["signals", "Signals"], ["workflows", `Workflows (${assetWorkflows.length})`], ["history", "History"], ["limits", "Limits"], ["demo-tools", "Demo tools"]].map(([value, label]) => <button key={value} id={`asset-tab-${value}`} role="tab" type="button" aria-selected={assetTab === value} aria-controls={`asset-panel-${value}`} onClick={() => setAssetTab(value)}>{label}</button>)}
              </div>
              {assetTab === 'signals' && <div id="asset-panel-signals" role="tabpanel" aria-labelledby="asset-tab-signals"><TelemetryChart
                data={telemetry}
                events={assetWorkflows}
                assetTag={selectedAsset?.asset_tag || 'selected asset'}
                onQuality={setTelemetryQuality}
                showRecentEvents={false}
                thresholds={selectedAsset ? {
                  vibration_mm_s: selectedAsset.vibration_threshold_mm_s,
                  vibration_threshold_source: selectedAsset.vibration_threshold_source,
                  temperature_c: selectedAsset.temperature_threshold_c,
                  temperature_threshold_source: selectedAsset.temperature_threshold_source,
                } : {}}
              /></div>}
              {assetTab === 'workflows' && <div className="asset-tab-panel" id="asset-panel-workflows" role="tabpanel" aria-labelledby="asset-tab-workflows">
                {assetWorkflows.length ? <>
                  {(showAllAssetWorkflows ? assetWorkflows : assetWorkflows.slice(0, 8)).map((item) => <button className="asset-workflow-row" type="button" key={item.id} onClick={() => navigateToWorkflow(item.id)}><span><strong>{item.summary}</strong><small>{dateTimeLabel(item.created_at)} · {relativeTimeLabel(item.created_at)}</small></span><StatusPill status={item.status} cmmsReference={item.cmms_reference} /></button>)}
                  {assetWorkflows.length > 8 && <button className="button button-quiet asset-view-all" type="button" onClick={() => setShowAllAssetWorkflows((value) => !value)}>{showAllAssetWorkflows ? 'Show fewer' : `View all (${assetWorkflows.length})`}</button>}
                </> : <p className="asset-tab-empty">No workflows recorded for this asset.</p>}
              </div>}
              {assetTab === 'history' && selectedAsset && <div id="asset-panel-history" className="asset-tab-panel" role="tabpanel" aria-labelledby="asset-tab-history"><AssetHistoryPanel assetId={selectedAsset.id} onOpenWorkflow={navigateToWorkflow} /></div>}
              {assetTab === 'limits' && <div id="asset-panel-limits" role="tabpanel" aria-labelledby="asset-tab-limits"><AssetThresholdEditor asset={selectedAsset} canEdit={hasRole('reliability_engineer', 'manager')} onSaved={async () => { await refreshAll(); setToast({ message: 'Asset advisory thresholds updated.', type: 'success' }) }} /></div>}
              {assetTab === 'demo-tools' && <div id="asset-panel-demo-tools" role="tabpanel" aria-labelledby="asset-tab-demo-tools"><SimulatorPanel asset={selectedAsset} enabled={Boolean(healthStatus?.simulator_enabled)} canControl={hasRole('operator', 'reliability_engineer')} activeScenario={selectedScenario} onChanged={refreshAll} /></div>}
            </section>
          </div>
        </section>
        <footer className="footer-note"><span><ShieldCheck size={13} /> Recommendations are advisory. A qualified human must authorize every work-order dispatch.</span><span className="mono">EDGE EG-04A · API v1.0</span></footer>
      </main>
      <ApprovalsDrawer
        open={approvalsOpen}
        pending={pending}
        recentlyDecided={recentlyDecided}
        onClose={() => setApprovalsOpen(false)}
        onReview={(workflow) => navigateToWorkflow(workflow.id)}
      />
      <ApprovalModal
        workflow={activeWorkflow}
        auditEvents={auditEvents}
        activity={selectedTrace}
        currentAsset={reviewAsset}
        plantId={reviewAsset?.plant_id}
        latestReading={reviewTelemetry.at(-1)}
        onClose={closeWorkflow}
        onApprove={(id, comment, acknowledgeStale) => decide(id, comment, 'approve', acknowledgeStale)}
        onReject={(id, comment) => decide(id, comment, 'reject')}
        onLifecycle={updateLifecycle}
        onCloseOut={closeOut}
        canApprove={hasRole('approver')}
        canWorkOrder={hasRole('operator', 'reliability_engineer', 'technician')}
        busy={busy}
      />
      {toast && <div className={`toast ${toast.type === 'error' ? 'error' : ''}`} role="status">{toast.message}</div>}
    </div>
  )
}
