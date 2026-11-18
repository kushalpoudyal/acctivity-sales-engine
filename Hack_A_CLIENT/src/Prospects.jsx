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
  async function submit(path, body) {
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
      <p>Read the firm's website, including team profiles, services and contact pages, then extract a detailed firm brief with OpenAI. Each run may incur charges.</p>
      <button disabled={busy || detail.status === 'researching'} onClick={research}>{detail.status === 'researching' ? 'Researching…' : 'Run website research'}</button>
      {detail.research_meta?.pages_read > 0 && <p>{detail.status === 'researching' ? detail.research_meta.stage : 'Website research'} · {detail.research_meta.pages_read} pages read</p>}
      {detail.research_meta?.warnings?.length > 0 && <details><summary>Research coverage notes ({detail.research_meta.warnings.length})</summary><ul>{detail.research_meta.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
      {detail.error && <p role="alert">{detail.error}</p>}
      <ApolloPanel detail={detail} busy={busy} submit={submit} />
      {detail.brief.length > 0 && !detail.research_meta?.pages_read && <p>This brief used the earlier quick search. Run website research again to populate detailed staff records and saved logos.</p>}
      {detail.staff?.length > 0 && <div><h3>Staff records ({detail.staff.length})</h3>
        <p>Public website records saved for demo preparation. These are not active Acctivity accounts.</p>
        <div className="staff-table"><table><thead><tr><th>Name</th><th>Position</th><th>Public work contact</th></tr></thead>
          <tbody>{detail.staff.map(person => <tr key={person.id}><td>{person.full_name}</td><td>{person.position || 'Not published'}</td>
            <td>{[person.public_work_email, person.public_work_phone].filter(Boolean).join(' · ') || 'Not published'}</td></tr>)}</tbody></table></div></div>}
      {detail.brand_assets?.length > 0 && <div><h3>Saved firm branding</h3>
        <p>Published website logos for private demo preparation; review permission before distributing.</p>
        {detail.brand_assets.map(asset => <figure key={asset.id}><img className="firm-logo" alt={`${detail.name} published logo`}
          src={`${base}/${id}/brand-assets/${asset.id}`} /><figcaption><a href={`${base}/${id}/brand-assets/${asset.id}`} download>Download logo</a>
          {' · '}<a href={asset.source_url} target="_blank" rel="noreferrer">Website source</a></figcaption></figure>)}</div>}
      {detail.brief.length > 0 && <BriefReview key={`${id}:${detail.brief_token}`} detail={detail} busy={busy} submit={submit} />}
      {detail.proposals?.map(p => <div key={p.id}><h3>Approved brief #{p.id}</h3>
        <p><a href={`${base}/${id}/proposals/${p.id}/demo-export`}>Download approved firm and staff data (JSON)</a></p>
        {p.status === 'ready' ? <a href={`${base}/${id}/proposals/${p.id}/download`}>Download PowerPoint (.pptx)</a> :
          <><p>{p.error || (p.status === 'building' ? 'Building PowerPoint in the OpenAI container…' : 'Approval saved. Generating a PowerPoint uses OpenAI credits.')}</p>
            <button disabled={busy || p.status === 'building'} onClick={() => submit(`proposals/${p.id}/build`, {})}>{p.status === 'failed' ? 'Retry PowerPoint' : 'Generate PowerPoint'}</button></>}</div>)}
    </> : <><h2>Firm workspace</h2><p>{id ? 'Loading firm…' : 'Select a firm to see its research and sources.'}</p></>}</section></div>
  </main>
}

function ApolloPanel({ detail, busy, submit }) {
  const org = detail.apollo_org
  if (!detail.apollo_configured) return <div className="apollo-panel"><h3>Apollo</h3>
    <p>Apollo not configured. Add <code>APOLLO_API_KEY</code> to the server <code>.env</code> to enrich firms from Apollo.</p></div>
  const tech = org?.technology_names?.length ? org.technology_names : (org?.current_technologies || []).map(t => t.name)
  const industries = org?.industries?.length ? org.industries : [org?.industry].filter(Boolean)
  return <div className="apollo-panel"><h3>Apollo</h3>
    {org ? <>
      <p>Enriched on {detail.apollo_enriched_at?.slice(0, 10)} · {org.name}</p>
      <dl>
        <dt>Headcount</dt><dd>{org.estimated_num_employees ?? 'Not listed'}</dd>
        <dt>Industries</dt><dd>{industries.join(', ') || 'Not listed'}</dd>
        <dt>Tech stack ({tech.length})</dt><dd>{tech.join(', ') || 'Not listed'}</dd>
      </dl>
      <button disabled={busy} onClick={() => submit('apollo/enrich', { refresh: true })}>Refresh enrichment (1 credit)</button>
    </> : <>
      <p>Look up this firm's headcount, industries and technology in Apollo. Uses 1 Apollo credit.</p>
      <button disabled={busy} onClick={() => submit('apollo/enrich', {})}>Enrich firm (1 credit)</button>
    </>}</div>
}

function BriefReview({ detail, busy, submit }) {
  const [selected, setSelected] = useState(detail.brief.map((_, i) => i))
  const categories = [...new Set(detail.brief.map(item => item.category || 'Website findings'))]
  return <><h3>Review cited findings</h3><small>{detail.researched_at} UTC · Uncheck anything you do not want in the proposal.</small>
    {categories.map(category => <div key={category}><h3>{category}</h3>
      <ul className="finding-list">{detail.brief.map((item, i) => (item.category || 'Website findings') === category && <li key={i}>
        <label className="finding-choice"><input type="checkbox" checked={selected.includes(i)}
          onChange={e => setSelected(e.target.checked ? [...selected, i] : selected.filter(n => n !== i))} />
          <span>{item.title && <strong>{item.title}<br /></strong>}{item.text}</span></label>
        <div>{item.sources.map(s => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a>)}</div>
        {item.evidence && <details><summary>Supporting website passage</summary><blockquote>{item.evidence}</blockquote></details>}
      </li>)}</ul></div>)}
    <button disabled={busy || !selected.length || detail.status === 'researching'} onClick={() => submit('approvals', { brief_token: detail.brief_token, findings: selected })}>Approve selected findings</button></>
}
