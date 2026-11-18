import { useCallback, useEffect, useState } from 'react'
import './Prospects.css'

const base = '/api/v1/firms'
const labels = { new: 'Not researched', researching: 'Researching', ready: 'Brief ready', needs_review: 'Needs review', failed: 'Research failed' }

async function request(path = '', options) {
  const res = await fetch(base + path, options)
  if (!res.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Cannot reach the firms API. Check the FastAPI server on port 9001.')
  }
  const result = await res.json()
  if (!res.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Request failed (${res.status}).`)
  return result
}

export default function Prospects() {
  const [firms, setFirms] = useState([])
  const [id, select] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [version, refresh] = useState(0)
  const [query, search] = useState('')
  const reload = useCallback(() => refresh(v => v + 1), [])
  useEffect(() => {
    let active = true
    let timer
    async function load() {
      try {
        const [rows, selected] = await Promise.all([request(), id ? request('/' + id) : null])
        if (!active) return
        setFirms(rows)
        setDetail(selected)
        if (rows.some(row => row.status === 'researching') || selected?.proposals.some(p => p.status === 'building')) timer = setTimeout(load, 2000)
      } catch (e) { if (active) setError(e.message) }
    }
    load()
    return () => { active = false; clearTimeout(timer) }
  }, [id, version])

  async function add(event) {
    event.preventDefault()
    const form = event.currentTarget
    setBusy(true); setError('')
    try {
      const row = await request('', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(form))) })
      form.reset(); select(row.id); reload()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function research() {
    setBusy(true); setError('')
    try {
      await request(`/${id}/research`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      setDetail(prev => ({ ...prev, status: 'researching' })); reload()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const matches = firms.filter(f => (f.name + ' ' + f.domain).toLowerCase().includes(query.toLowerCase()))
  async function submitProposal(path, body) {
    setBusy(true); setError('')
    try {
      await request(`/${id}/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      reload()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <main className="prospects">
    <header><small>ACCTIVITY / SALES ENGINE</small><h1>Prospect directory</h1><p>Save a firm, then research its website when you are ready.</p></header>
    <form onSubmit={add}><h2>Add a firm</h2><label>Firm website<input name="website" required maxLength={2048} placeholder="firm.com.au" /></label><label>Display name (optional)<input name="display_name" maxLength={160} /></label><button disabled={busy}>Save firm</button></form>
    {error && <p role="alert">{error} <button onClick={() => { setError(''); reload() }}>Refresh</button></p>}
    <div className="prospect-grid"><section><h2>Firms ({firms.length})</h2><label>Filter directory<input value={query} onChange={e => search(e.target.value)} /></label>
      {matches.map(f => <button className="firm-row" aria-pressed={id === f.id} key={f.id} onClick={() => {select(f.id); setError('')}}><strong>{f.name}</strong><span>{f.domain} · {labels[f.status]}</span></button>)}
      {!matches.length && <p>{firms.length ? 'No matching firms.' : 'No firms saved yet.'}</p>}</section>
    <section aria-live="polite">{detail && detail.id === id ? <>
      <small>FIRM WORKSPACE</small><h2>{detail.name}</h2><p>{detail.domain} · {labels[detail.status]}</p>
      <p>Search this firm's public website using OpenAI. Each run may incur charges.</p>
      <button disabled={busy || detail.status === 'researching'} onClick={research}>{detail.status === 'researching' ? 'Researching…' : 'Run website research'}</button>
      {detail.error && <p role="alert">{detail.error}</p>}
      {detail.brief.length > 0 && <BriefReview key={`${id}:${detail.brief_token}`} detail={detail} busy={busy} submit={submitProposal} />}
      {detail.proposals?.map(p => <div key={p.id}><h3>Approved brief #{p.id}</h3>
        {p.status === 'ready' ? <a href={`${base}/${id}/proposals/${p.id}/download`}>Download PowerPoint (.pptx)</a> :
          <><p>{p.error || (p.status === 'building' ? 'Building PowerPoint in the OpenAI container…' : 'Approval saved. Generating a PowerPoint uses OpenAI credits.')}</p>
            <button disabled={busy || p.status === 'building'} onClick={() => submitProposal(`proposals/${p.id}/build`, {})}>{p.status === 'failed' ? 'Retry PowerPoint' : 'Generate PowerPoint'}</button></>}</div>)}
    </> : <><h2>Firm workspace</h2><p>{id ? 'Loading firm…' : 'Select a firm to see its research and sources.'}</p></>}</section></div>
  </main>
}

function BriefReview({ detail, busy, submit }) {
  const [selected, setSelected] = useState(detail.brief.map((_, i) => i))
  return <><h3>Review cited findings</h3><small>{detail.researched_at} UTC · Uncheck anything you do not want in the proposal.</small>
    <ul>{detail.brief.map((item, i) => <li key={i}><label><input type="checkbox" checked={selected.includes(i)}
      onChange={e => setSelected(e.target.checked ? [...selected, i] : selected.filter(n => n !== i))} /> {item.text}</label>
      <div>{item.sources.map(s => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a>)}</div></li>)}</ul>
    <button disabled={busy || !selected.length || detail.status === 'researching'} onClick={() => submit('approvals', { brief_token: detail.brief_token, findings: selected })}>Approve selected findings</button></>
}
