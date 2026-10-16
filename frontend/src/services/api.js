const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'
const OIDC_AUTHORITY = import.meta.env.VITE_OIDC_AUTHORITY || ''
const OIDC_CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID || ''

async function request(path, options = {}) {
  let response
  try {
    let bearerToken = null
    if (OIDC_AUTHORITY && OIDC_CLIENT_ID) {
      const { getAccessToken } = await import('./auth.js')
      bearerToken = await getAccessToken()
    }
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        ...(typeof FormData === 'undefined' || !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {}),
        ...options.headers,
      },
    })
  } catch (error) {
    if (OIDC_AUTHORITY && OIDC_CLIENT_ID && /sign-in session expired/i.test(error.message || '')) {
      window.dispatchEvent(new Event('fieldnote:auth-expired'))
    }
    throw new Error(`Request to ${path} failed (${error.message || 'network error'})`)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401 && OIDC_AUTHORITY && OIDC_CLIENT_ID) {
      window.dispatchEvent(new Event('fieldnote:auth-expired'))
    }
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.message || response.statusText
    throw new Error(`Request to ${path} failed (${response.status}): ${detail}`)
  }
  return response.status === 204 ? null : response.json()
}

async function downloadFile(path, filename) {
  let bearerToken = null
  if (OIDC_AUTHORITY && OIDC_CLIENT_ID) {
    const { getAccessToken } = await import('./auth.js')
    bearerToken = await getAccessToken()
  }
  const response = await fetch(`${API_BASE}${path}`, {
    headers: bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {},
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401 && OIDC_AUTHORITY && OIDC_CLIENT_ID) {
      window.dispatchEvent(new Event('fieldnote:auth-expired'))
    }
    throw new Error(typeof body.detail === 'string' ? body.detail : response.statusText)
  }
  const objectUrl = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}

export const api = {
  health: async () => {
    const apiRoot = API_BASE.replace(/\/api\/?$/, '')
    const response = await fetch(`${apiRoot}/health`)
    if (!response.ok) throw new Error(`Request to /health failed (${response.status})`)
    return response.json()
  },
  latestTelemetry: () => request('/telemetry/latest'),
  plants: () => request('/plants'),
  assets: (plantId) => request(`/assets${plantId ? `?plant_id=${encodeURIComponent(plantId)}` : ''}`),
  asset: (assetId) => request(`/assets/${assetId}`),
  assetHistory: (assetId) => request(`/assets/${assetId}/history`),
  acknowledgeAssetAlert: (assetId) => request(`/assets/${assetId}/alert/acknowledge`, { method: 'POST' }),
  snoozeAssetAlert: (assetId, minutes, reason) => request(`/assets/${assetId}/alert/snooze`, {
    method: 'POST', body: JSON.stringify({ minutes, reason }),
  }),
  knowledgeChunk: (chunkId, plantId) => request(`/knowledge/chunks/${chunkId}${plantId ? `?plant_id=${encodeURIComponent(plantId)}` : ''}`),
  knowledgeDocuments: (plantId) => request(`/knowledge/documents${plantId ? `?plant_id=${encodeURIComponent(plantId)}` : ''}`),
  knowledgeDocumentChunks: (documentId, plantId, limit = 10, offset = 0) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (plantId) params.set('plant_id', plantId)
    return request(`/knowledge/documents/${documentId}/chunks?${params}`)
  },
  uploadKnowledgeDocument: (file, metadata) => {
    const body = new FormData()
    body.append('file', file)
    for (const [name, value] of Object.entries(metadata)) {
      if (name === 'allowed_roles') value.forEach((role) => body.append(name, role))
      else if (value !== undefined && value !== null && value !== '') body.append(name, value)
    }
    return request('/knowledge/documents', { method: 'POST', body })
  },
  updateAssetThresholds: (assetId, payload) => request(`/assets/${assetId}/thresholds`, {
    method: 'PATCH', body: JSON.stringify(payload),
  }),
  assetThresholdHistory: (assetId) => request(`/assets/${assetId}/threshold-history`),
  scenarios: () => request('/simulator/scenarios'),
  startScenario: (payload) => request('/simulator/scenarios', { method: 'POST', body: JSON.stringify(payload) }),
  stopScenario: (assetId) => request(`/simulator/scenarios/${assetId}`, { method: 'DELETE' }),
  chat: (payload) => request('/chat', { method: 'POST', body: JSON.stringify(payload) }),
  telemetry: (assetId, limit = 60) => request(`/telemetry/${assetId}?limit=${encodeURIComponent(limit)}`),
  workflows: (plantId) => request(`/workflows${plantId ? `?plant_id=${encodeURIComponent(plantId)}` : ''}`),
  workflow: (id) => request(`/workflows/${id}`),
  myWork: () => request('/workflows/my-work'),
  assignWorkflow: (id, assignedToSub) => request(`/workflows/${id}/assignment`, {
    method: 'PATCH', body: JSON.stringify({ assigned_to_sub: assignedToSub }),
  }),
  workflowEvents: (id) => request(`/workflows/${id}/events`),
  workOrderAuditPack: (id) => downloadFile(`/workflows/${id}/audit-pack.pdf`, `fieldnote-${id}-audit.pdf`),
  maintenanceMetrics: (plantId) => request(`/workflows/metrics/maintenance${plantId ? `?plant_id=${encodeURIComponent(plantId)}` : ''}`),
  startWorkflow: (payload) => request('/workflows', { method: 'POST', body: JSON.stringify(payload) }),
  approveWorkflow: (id, comment, acknowledgeStale = false) => request(`/workflows/${id}/approve`, {
    method: 'POST', body: JSON.stringify({ comment, acknowledge_stale: acknowledgeStale }),
  }),
  rejectWorkflow: (id, comment) => request(`/workflows/${id}/reject`, {
    method: 'POST', body: JSON.stringify({ comment }),
  }),
  advanceWorkflow: (id, status) => request(`/workflows/${id}/lifecycle`, {
    method: 'POST', body: JSON.stringify({ status }),
  }),
  closeOutWorkflow: (id, payload) => request(`/workflows/${id}/close-out`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
}
