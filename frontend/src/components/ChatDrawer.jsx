import { useEffect, useRef, useState } from 'react'
import { Bot, ChevronRight, LoaderCircle, Send, X, Zap } from 'lucide-react'
import { api } from '../services/api.js'

const starters = [
  'Why is this asset in attention?',
  'Is the data fresh?',
  'Which assets need approval?',
  'Summarise the last diagnosis for this asset.',
]

export default function ChatDrawer({ open, onClose, plant, asset, onSelectAsset, onOpenWorkflow, onRunDiagnosis }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [includePlant, setIncludePlant] = useState(true)
  const [includeAsset, setIncludeAsset] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [retryText, setRetryText] = useState('')
  const inputRef = useRef(null)
  const messagesRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const previousFocus = document.activeElement
    inputRef.current?.focus()
    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
      if (event.key !== 'Tab') return
      const controls = [...(document.querySelector('.chat-drawer')?.querySelectorAll('button:not(:disabled), textarea:not(:disabled), input:not(:disabled)') || [])]
      if (!controls.length) return
      if (event.shiftKey && document.activeElement === controls[0]) {
        event.preventDefault()
        controls.at(-1).focus()
      } else if (!event.shiftKey && document.activeElement === controls.at(-1)) {
        event.preventDefault()
        controls[0].focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement) previousFocus.focus()
    }
  }, [open, onClose])

  useEffect(() => {
    if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight
  }, [messages, busy])

  async function send(message) {
    const content = message.trim()
    if (!content || busy) return
    const history = messages.slice(-10).map(({ role, content: turn }) => ({ role, content: turn }))
    setMessages((current) => [...current, { role: 'user', content }])
    setInput('')
    setError('')
    setRetryText(content)
    setBusy(true)
    try {
      const response = await api.chat({
        message: content,
        plant_id: includePlant && plant?.id ? plant.id : undefined,
        asset_id: includeAsset && asset?.id ? asset.id : undefined,
        history,
      })
      setMessages((current) => [...current, { role: 'assistant', ...response }])
      setRetryText('')
    } catch (requestError) {
      setError(requestError.message || 'The assistant could not answer this request.')
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  function activateSource(source) {
    if (source.type === 'asset') onSelectAsset(source.id)
    if (source.type === 'workflow') onOpenWorkflow(source.id)
  }

  if (!open) return null

  return (
    <>
      <button className="chat-backdrop" type="button" aria-label="Close assistant" onClick={() => onClose()} />
      <aside className="chat-drawer" role="dialog" aria-modal="true" aria-labelledby="chat-title">
        <header className="chat-header">
          <div className="chat-title"><span><Bot size={17} /></span><div><h2 id="chat-title">Fieldnote assistant</h2><p>Live data · read only</p></div></div>
          {asset && onRunDiagnosis && <button className="icon-button chat-diagnose-button" type="button" onClick={onRunDiagnosis} aria-label={`Run diagnosis for ${asset.asset_tag}`} title={`Run diagnosis for ${asset.asset_tag}`}><Zap size={16} /></button>}
          <button className="icon-button" type="button" onClick={() => onClose()} aria-label="Close assistant" title="Close assistant"><X size={17} /></button>
        </header>
        <div className="chat-context" aria-label="Question context">
          {includePlant && plant && <button type="button" onClick={() => setIncludePlant(false)} title="Clear plant context">{plant.code} <X size={11} /></button>}
          {includeAsset && asset && <button type="button" onClick={() => setIncludeAsset(false)} title="Clear asset context">{asset.asset_tag} <X size={11} /></button>}
          {!includePlant && !includeAsset && <span>Fleet-wide context</span>}
        </div>
        <div className="chat-messages" ref={messagesRef} aria-live="polite" aria-relevant="additions">
          {!messages.length && <section className="chat-empty"><p>Ask about current telemetry, asset status, freshness, or work-order history.</p><div>{starters.map((starter) => <button type="button" key={starter} onClick={() => send(starter)} disabled={busy}>{starter}<ChevronRight size={13} /></button>)}</div></section>}
          {messages.map((entry, index) => (
            <article className={`chat-message chat-message-${entry.role}`} key={`${entry.role}-${index}`}>
              <p>{entry.content || entry.answer}</p>
              {entry.role === 'assistant' && <>
                <div className="chat-answer-meta"><span>Answered from live data at {new Date(entry.answered_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })}</span>{!entry.used_llm && <b>Rule-based answer</b>}</div>
                {entry.knowledge_status === 'unavailable' && <small className="chat-knowledge-status is-error" role="status">Knowledge search unavailable; this answer may not include maintenance-document evidence.</small>}
                {entry.knowledge_status === 'no_matches' && <small className="chat-knowledge-status" role="status">Knowledge search completed; no matching maintenance sources were found.</small>}
                {entry.sources?.length > 0 && <div className="chat-sources" aria-label="Answer sources">{entry.sources.map((source, sourceIndex) => <button type="button" key={`${source.type}-${source.id}-${sourceIndex}`} onClick={() => activateSource(source)} disabled={!['asset', 'workflow'].includes(source.type)} title={source.timestamp || source.label}>{source.type === 'note' ? 'Note' : source.type} · {source.label}</button>)}</div>}
                {entry.truncated && <small className="chat-truncated">Some result details were omitted.</small>}
              </>}
            </article>
          ))}
          {busy && <div className="chat-thinking"><LoaderCircle size={15} className="animate-spin" />Checking live Fieldnote data…</div>}
          {error && <div className="chat-error" role="alert"><span>{error}</span><button className="button button-quiet" type="button" onClick={() => send(retryText)} disabled={busy}>Retry</button></div>}
        </div>
        <footer className="chat-footer">
          <form onSubmit={(event) => { event.preventDefault(); send(input) }}>
            <label className="sr-only" htmlFor="fieldnote-chat-input">Ask Fieldnote about live data</label>
            <textarea id="fieldnote-chat-input" ref={inputRef} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(input) } }} maxLength={1000} rows={3} placeholder="Ask a live-data question…" disabled={busy} />
            <button className="button button-primary" type="submit" aria-label="Send question" title="Send question" disabled={busy || !input.trim()}><Send size={15} /></button>
          </form>
          <p>Advisory only. Answers use Fieldnote demo data and cannot approve or dispatch work.</p>
        </footer>
      </aside>
    </>
  )
}