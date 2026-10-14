import { AlertCircle, FileSearch, LoaderCircle, RefreshCw, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../services/api.js'

const roleOptions = [
  ['operator', 'Operator'],
  ['reliability_engineer', 'Reliability engineer'],
  ['approver', 'Approver'],
  ['manager', 'Manager'],
  ['technician', 'Technician'],
]

function metadataLabel(document) {
  return [document.document_type, document.manufacturer, document.asset_type, document.revision && `Rev ${document.revision}`]
    .filter(Boolean).join(' · ')
}

export default function KnowledgeLibrary({ plantId }) {
  const fileRef = useRef(null)
  const [documents, setDocuments] = useState([])
  const [file, setFile] = useState(null)
  const [documentType, setDocumentType] = useState('manual')
  const [manufacturer, setManufacturer] = useState('')
  const [assetType, setAssetType] = useState('')
  const [revision, setRevision] = useState('')
  const [sourceUri, setSourceUri] = useState('')
  const [allowedRoles, setAllowedRoles] = useState(roleOptions.map(([role]) => role))
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [expandedDocument, setExpandedDocument] = useState(null)
  const [documentChunks, setDocumentChunks] = useState({})
  const [chunkLoadingId, setChunkLoadingId] = useState(null)

  async function refreshDocuments() {
    setLoading(true)
    setError('')
    try {
      setDocuments(await api.knowledgeDocuments(plantId && plantId !== 'all' ? plantId : undefined))
    } catch (requestError) {
      setError(requestError.message || 'Knowledge documents could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refreshDocuments()
  }, [plantId])

  async function submitUpload(event) {
    event.preventDefault()
    if (!file || !allowedRoles.length || uploading) return
    setUploading(true)
    setError('')
    setMessage('')
    try {
      const document = await api.uploadKnowledgeDocument(file, {
        document_type: documentType,
        manufacturer,
        asset_type: assetType,
        revision,
        source_uri: sourceUri,
        plant_id: plantId === 'all' ? undefined : plantId,
        allowed_roles: allowedRoles,
      })
      setMessage(`${document.filename} indexed (${document.chunk_count} chunks).`)
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      await refreshDocuments()
    } catch (requestError) {
      const uploadError = requestError.message || 'Document ingestion failed. Re-select the same file to retry if it was recorded as failed.'
      await refreshDocuments()
      setError(uploadError)
    } finally {
      setUploading(false)
    }
  }

  function toggleRole(role) {
    setAllowedRoles((current) => current.includes(role)
      ? current.filter((item) => item !== role)
      : [...current, role])
  }

  async function loadDocumentChunks(document, reset = false) {
    const current = documentChunks[document.id] || { items: [], total: document.chunk_count, error: '' }
    const offset = reset ? 0 : current.items.length
    setChunkLoadingId(document.id)
    setDocumentChunks((existing) => ({ ...existing, [document.id]: { ...current, error: '' } }))
    try {
      const page = await api.knowledgeDocumentChunks(
        document.id,
        plantId && plantId !== 'all' ? plantId : undefined,
        10,
        offset,
      )
      setDocumentChunks((existing) => {
        const previous = existing[document.id] || { items: [] }
        return {
          ...existing,
          [document.id]: {
            items: reset ? page.items : [...previous.items, ...page.items],
            total: page.total,
            error: '',
          },
        }
      })
    } catch (requestError) {
      setDocumentChunks((existing) => ({
        ...existing,
        [document.id]: { ...existing[document.id], error: requestError.message || 'Indexed passages are unavailable.' },
      }))
    } finally {
      setChunkLoadingId(null)
    }
  }

  function toggleDocumentPreview(document) {
    if (expandedDocument === document.id) {
      setExpandedDocument(null)
      return
    }
    setExpandedDocument(document.id)
    if (!documentChunks[document.id]?.items?.length) void loadDocumentChunks(document, true)
  }

  return (
    <section className="knowledge-workspace" aria-labelledby="knowledge-title">
      <header className="knowledge-heading">
        <div><p className="eyebrow">MAINTENANCE KNOWLEDGE</p><h2 id="knowledge-title">Knowledge</h2><p>Approved maintenance references available to Fieldnote retrieval.</p></div>
        <button className="button button-quiet" type="button" onClick={() => void refreshDocuments()} disabled={loading || uploading} aria-label="Refresh knowledge documents" title="Refresh knowledge documents"><RefreshCw size={14} />Refresh</button>
      </header>

      <div className="knowledge-layout">
        <section className="knowledge-upload-section" aria-labelledby="knowledge-upload-title">
          <h3 id="knowledge-upload-title">Upload a document</h3>
          <form className="knowledge-upload-form" onSubmit={submitUpload}>
            <label className="knowledge-file-field">Document file
              <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.markdown" onChange={(event) => setFile(event.target.files?.[0] || null)} required />
              <small>PDF, DOCX, TXT, or Markdown · 10 MB maximum</small>
            </label>
            <div className="knowledge-form-grid">
              <label>Document type<input value={documentType} onChange={(event) => setDocumentType(event.target.value)} maxLength={40} required /></label>
              <label>Manufacturer<input value={manufacturer} onChange={(event) => setManufacturer(event.target.value)} maxLength={160} /></label>
              <label>Equipment type<input value={assetType} onChange={(event) => setAssetType(event.target.value)} maxLength={120} /></label>
              <label>Revision<input value={revision} onChange={(event) => setRevision(event.target.value)} maxLength={80} /></label>
              <label className="knowledge-source-uri">Source URI<input type="url" value={sourceUri} onChange={(event) => setSourceUri(event.target.value)} maxLength={2000} placeholder="https://…" /></label>
            </div>
            <fieldset className="knowledge-role-fieldset">
              <legend>Visible to roles</legend>
              <div>{roleOptions.map(([role, label]) => <label key={role}><input type="checkbox" checked={allowedRoles.includes(role)} onChange={() => toggleRole(role)} />{label}</label>)}</div>
            </fieldset>
            <p className="knowledge-scope-note">{plantId === 'all' ? 'Available to authorized roles across plants.' : 'Scoped to the selected plant and authorized roles.'}</p>
            {error && <p className="knowledge-feedback is-error" role="alert"><AlertCircle size={14} />{error}</p>}
            {message && <p className="knowledge-feedback" role="status">{message}</p>}
            <button className="button button-primary" type="submit" disabled={!file || !allowedRoles.length || uploading}>{uploading ? <LoaderCircle size={15} className="animate-spin" /> : <Upload size={15} />}{uploading ? 'Indexing…' : 'Upload and index'}</button>
          </form>
        </section>

        <section className="knowledge-list-section" aria-labelledby="knowledge-list-title">
          <div className="knowledge-list-heading"><div><h3 id="knowledge-list-title">Documents</h3><p>{documents.length} visible in this scope</p></div></div>
          {loading ? <p className="knowledge-empty" role="status">Loading documents…</p> : error && !documents.length ? <p className="knowledge-empty is-error" role="alert">Document list unavailable.</p> : documents.length ? (
            <ul className="knowledge-document-list">
              {documents.map((document) => <li key={document.id}>
                <div className="knowledge-document-copy"><strong>{document.filename}</strong><small>{metadataLabel(document) || 'Maintenance reference'}</small><small>{document.allowed_roles.join(', ')}{document.plant_id ? ` · plant ${document.plant_id}` : ' · shared across plants'}</small></div>
                <span className={`knowledge-status is-${document.ingestion_status}`}>{document.ingestion_status}</span>
                {document.ingestion_status === 'indexed' && <>
                  <small className="knowledge-chunk-count">{document.chunk_count} chunks</small>
                  <button className="knowledge-preview-toggle" type="button" aria-expanded={expandedDocument === document.id} onClick={() => toggleDocumentPreview(document)}><FileSearch size={14} />{expandedDocument === document.id ? 'Hide indexed passages' : 'Preview indexed passages'}</button>
                  {expandedDocument === document.id && <div className="knowledge-chunk-preview" aria-label={`Indexed passages from ${document.filename}`}>
                    {chunkLoadingId === document.id && !documentChunks[document.id]?.items?.length && <p className="knowledge-empty" role="status">Loading extracted passages…</p>}
                    {documentChunks[document.id]?.error && <p className="knowledge-feedback is-error" role="alert">{documentChunks[document.id].error}</p>}
                    {documentChunks[document.id]?.items?.map((chunk, index) => <article className="knowledge-chunk-card" key={chunk.chunk_id}>
                      <header><strong>Passage {chunk.chunk_index == null ? index + 1 : chunk.chunk_index + 1}</strong><small>{[chunk.revision && `Rev ${chunk.revision}`, chunk.page_number && `Page ${chunk.page_number}`, chunk.section_title].filter(Boolean).join(' · ')}</small></header>
                      <p>{chunk.content}</p>
                    </article>)}
                    {documentChunks[document.id]?.items?.length < documentChunks[document.id]?.total && <button className="button button-quiet knowledge-load-more" type="button" onClick={() => void loadDocumentChunks(document)} disabled={chunkLoadingId === document.id}>{chunkLoadingId === document.id ? 'Loading…' : 'Load more passages'}</button>}
                  </div>}
                </>}
                {document.ingestion_status === 'failed' && <small className="knowledge-failure">{document.failure_code || 'ingestion_failed'} · re-select the same file to retry</small>}
              </li>)}
            </ul>
          ) : <p className="knowledge-empty">No knowledge documents in this scope yet.</p>}
        </section>
      </div>
    </section>
  )
}