import { useCallback, useEffect, useRef, useState } from 'react'
import './Prospects.css'
import FindFirms from './FindFirms'

const base = '/api/v1/firms'
const labels = { new: 'Not researched', researching: 'Researching', ready: 'Brief ready', needs_review: 'Needs review', failed: 'Research failed' }
const formatLabels = { pptx: 'PowerPoint proposal', docx: 'Word proposal', pdf: 'Welcome PDF' }

async function request(path = '', options) {
  const res = await fetch(base + path, options)
  if (!res.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Cannot reach the firms API. Check the FastAPI server on port 9001.')
  }
  const result = await res.json()
  if (!res.ok) {
    const message = Array.isArray(result.detail) ? result.detail.map(e => e.msg).join('; ') : result.detail
    throw new Error(typeof message === 'string' ? message : `Request failed (${res.status}).`)
  }
  if (/^\/\d+$/.test(path) && (!Array.isArray(result.outputs) ||
      !Array.isArray(result.proposals) || result.proposals.some(p => !p.snapshot))) {
    throw new Error('The API is running an older workspace version. Restart FastAPI on port 9001, then refresh.')
  }
  return result
}

export default function Prospects() {
  const [firms, setFirms] = useState([])
  const [id, select] = useState(() => {
    const value = Number(new URLSearchParams(window.location.search).get('firm'))
    return Number.isInteger(value) && value > 0 ? value : null
  })
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
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
        setFirms(rows); setDetail(selected)
        if (rows.some(row => row.status === 'researching') ||
            selected?.proposals.some(p => p.status === 'building') ||
            selected?.outputs?.some(job => ['queued', 'running'].includes(job.status))) {
          timer = setTimeout(load, 2000)
        }
      } catch (e) { if (active) setError(e.message) }
    }
    load()
    return () => { active = false; clearTimeout(timer) }
  }, [id, version])

  function chooseFirm(value) {
    if (dirty && !window.confirm('Discard unsaved research edits?')) return
    setDirty(false); select(value); setError('')
    window.history.replaceState(null, '', `/prospects?firm=${value}`)
  }
  async function mutate(path, body, method = 'POST') {
    setBusy(true); setError('')
    try {
      const result = await request(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      reload()
      return result
    } catch (e) { setError(e.message); return null } finally { setBusy(false) }
  }
  async function add(event) {
    event.preventDefault()
    if (dirty && !window.confirm('Discard unsaved research edits and add another firm?')) return
    const form = event.currentTarget
    const result = await mutate('', Object.fromEntries(new FormData(form)))
    if (result) {
      form.reset(); setDirty(false); select(result.id)
      window.history.replaceState(null, '', `/prospects?firm=${result.id}`)
    }
  }
  const matches = firms.filter(f => (f.name + ' ' + f.domain).toLowerCase().includes(query.toLowerCase()))
  return <main className="prospects">
    <header className="directory-header"><small>ACCTIVITY.AI / INSTANT SALES ENGINE</small><h1>Prospect workspace</h1>
      <p>Research the firm. Review its people. Build a sales pack that feels personal.</p></header>
    <form className="intake" onSubmit={add}><h2>Add a firm</h2><label>Firm website<input name="website" required maxLength={2048} placeholder="firm.com.au" /></label>
      <label>Display name (optional)<input name="display_name" maxLength={160} /></label><button disabled={busy}>Save firm</button></form>
    <FindFirms onAdded={reload} />
    {error && <div role="alert" className="error-banner">{error} <button onClick={() => { setError(''); reload() }}>Refresh</button></div>}
    <div className="prospect-grid"><aside className="directory"><h2>Firms <span className="badge">{firms.length}</span></h2>
      <label>Filter directory<input value={query} onChange={e => search(e.target.value)} placeholder="Name or website" /></label>
      {matches.map(f => <button disabled={busy} className="firm-row" aria-pressed={id === f.id} key={f.id} onClick={() => chooseFirm(f.id)}>
        <strong>{f.name}</strong><span>{f.domain}</span><small>{labels[f.status]}</small></button>)}
      {!matches.length && <p>{firms.length ? 'No matching firms.' : 'No firms saved yet.'}</p>}</aside>
    <section className="workspace">{detail && detail.id === id
      ? <FirmWorkspace key={id} detail={detail} busy={busy} dirty={dirty} mutate={mutate} onDirty={setDirty} />
      : <><h2>Firm workspace</h2><p>{id ? 'Loading firm…' : 'Select a firm or save a website to get started.'}</p></>}</section></div>
  </main>
}

function FirmWorkspace({ detail, busy, dirty, mutate, onDirty }) {
  const [tab, setTab] = useState('research')
  const [approvalId, setApprovalId] = useState(null)
  // Approval sends the ticked website findings (brief indices) and the ticked Apollo finding keys.
  async function approve(indices, apolloKeys) {
    const result = await mutate(`/${detail.id}/approvals`, { brief_token: detail.brief_token, findings: indices, apollo_findings: apolloKeys })
    if (result) { setApprovalId(result.id); setTab('outputs') }
  }
  const submit = (path, body) => mutate(`/${detail.id}/${path}`, body)
  // Apollo changes the review token, which resets the research editor, so Apollo actions wait for saved edits.
  const apolloBusy = busy || dirty || detail.status === 'researching'
  const apollo = <>
    <ApolloPanel detail={detail} busy={apolloBusy} submit={submit} />
    {detail.apollo_configured && <ApolloContacts detail={detail} busy={apolloBusy} submit={submit} />}
    {dirty && detail.apollo_configured && <p className="coverage">Save or discard research edits before using Apollo.</p>}
    <StaffRecords detail={detail} />
  </>
  return <>
    <div className="workspace-heading"><div><small>FIRM WORKSPACE</small><h2>{detail.name}</h2><p>{detail.domain}</p></div>
      <span className="badge">{labels[detail.status]}</span></div>
    <div className="workspace-tabs" role="tablist" aria-label="Firm workspace">
      <button id="research-tab" role="tab" aria-selected={tab === 'research'} aria-controls="research-panel" onClick={() => setTab('research')}>01 &nbsp; Research</button>
      <button id="outputs-tab" role="tab" aria-selected={tab === 'outputs'} aria-controls="outputs-panel" onClick={() => setTab('outputs')}>02 &nbsp; Create outputs</button>
    </div>
    <div id="research-panel" role="tabpanel" aria-labelledby="research-tab" hidden={tab !== 'research'}>
      <ResearchEditor key={detail.brief_token} detail={detail} busy={busy} mutate={mutate} approve={approve} onDirty={onDirty} apollo={apollo} />
    </div>
    <div id="outputs-panel" role="tabpanel" aria-labelledby="outputs-tab" hidden={tab !== 'outputs'}>
      <OutputStudio detail={detail} busy={busy} mutate={mutate} approvalId={approvalId} onApproval={setApprovalId} />
    </div>
  </>
}

function ResearchEditor({ detail, busy, mutate, approve, onDirty, apollo }) {
  const [name, setName] = useState(detail.name)
  const [logoId, setLogoId] = useState(detail.preferred_logo_id ?? '')
  const [findings, setFindings] = useState(detail.brief)
  const [selected, setSelected] = useState(detail.brief.map((_, i) => i))
  const [apolloSelected, setApolloSelected] = useState((detail.apollo_findings || []).map(item => item.key))
  const count = selected.length + apolloSelected.length
  const [notice, setNotice] = useState('')
  const dirty = name !== detail.name || String(logoId) !== String(detail.preferred_logo_id ?? '') ||
    JSON.stringify(findings) !== JSON.stringify(detail.brief)
  const locked = busy || detail.status === 'researching'
  useEffect(() => { onDirty(dirty) }, [dirty, onDirty])
  useEffect(() => {
    function warn(e) { if (dirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  function edit(index, patch) {
    setFindings(previous => previous.map((f, i) => i === index ? { ...f, ...patch } : f))
    setNotice('')
  }
  async function save(event) {
    event.preventDefault()
    await mutate(`/${detail.id}/brief`, {
      brief_token: detail.brief_token, display_name: name, logo_id: logoId === '' ? null : Number(logoId),
      findings: findings.map((f, index) => ({ index, title: f.title || '', text: f.text, person: f.person || null })),
    }, 'PUT')
  }
  async function runResearch() {
    if (detail.brief.length && !window.confirm('Re-read the website? This paid run replaces current research and staff records. Approved briefs stay unchanged.')) return
    const result = await mutate(`/${detail.id}/research`, {})
    if (result) setNotice('Research started. Page coverage and results will update here.')
  }
  const categories = [...new Set(findings.map(f => f.category || 'Website findings'))]
  return <>
    <div className="research-toolbar"><div><h3>Website research</h3><p>Read public team profiles, services and contact pages. Each run may incur charges.</p></div>
      <button type="button" disabled={locked || dirty} onClick={runResearch}>{detail.status === 'researching' ? 'Researching…' : 'Run website research'}</button></div>
    {notice && <p role="status">{notice}</p>}
    {detail.research_meta?.pages_read > 0 && <p className="coverage">{detail.research_meta.pages_read} pages read · {detail.status === 'researching' ? detail.research_meta.stage : `${detail.brief.length} findings · ${detail.staff.length} staff records`}</p>}
    {detail.research_meta?.warnings?.length > 0 && <details><summary>Research coverage notes ({detail.research_meta.warnings.length})</summary><ul>{detail.research_meta.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
    {detail.error && <p role="alert">{detail.error}</p>}
    {apollo}
    <form className="research-form" onSubmit={save}>
      <label>Firm display name<input required maxLength={160} value={name} disabled={locked} onChange={e => setName(e.target.value)} /></label>
      <div className="section-title"><h3>Firm branding</h3><small>Choose the preferred saved logo.</small></div>
      <label className="choice"><input type="radio" name="logo" value="" checked={logoId === ''} disabled={locked} onChange={() => setLogoId('')} /> No preferred logo</label>
      <div className="logo-options">{detail.brand_assets.map(asset => <label className="logo-option" key={asset.id}>
        <input type="radio" name="logo" checked={String(logoId) === String(asset.id)} disabled={locked} onChange={() => setLogoId(asset.id)} />
        <img className="firm-logo" alt={`Saved firm logo ${asset.id}`} src={`${base}/${detail.id}/brand-assets/${asset.id}`} />
        <a href={`${base}/${detail.id}/brand-assets/${asset.id}`} download>Download logo</a></label>)}</div>
      {!detail.brand_assets.length && <p>No logo saved. Research will try to collect a published website logo.</p>}
      <div className="section-title"><h3>Review and edit findings</h3><small>Sources remain unchanged. Your corrections are labelled as rep edits.</small></div>
      {!findings.length && <p>Run website research to populate the brief and staff records.</p>}
      {categories.map(category => <div key={category}><h3 className="category-heading">{category}</h3>
        <div className="finding-list">{findings.map((item, index) => (item.category || 'Website findings') === category &&
          <article className="finding-card" key={index}>
            <label className="choice"><input type="checkbox" checked={selected.includes(index)} disabled={locked}
              onChange={e => setSelected(e.target.checked ? [...selected, index] : selected.filter(n => n !== index))} />
              <strong>Include {item.title || `finding ${index + 1}`}</strong></label>
            <label>Finding title<input value={item.title || ''} maxLength={200} disabled={locked} onChange={e => edit(index, { title: e.target.value })} /></label>
            <label>Summary for sales material<textarea required rows={3} maxLength={12000} value={item.text} disabled={locked} onChange={e => edit(index, { text: e.target.value })} /></label>
            {item.person && <details className="staff-editor"><summary>Edit staff profile: {item.person.full_name} · {item.person.position || 'Position not published'}</summary>
              <p>Structured staff fields take precedence in outputs. Update the summary too if your correction changes it.</p>
              <div className="field-grid">{[['full_name', 'Full name'], ['position', 'Position'], ['public_work_email', 'Public work email'], ['public_work_phone', 'Public work phone']].map(([key, label]) =>
                <label key={key}>{label}<input required={key === 'full_name'} maxLength={320} value={item.person[key] || ''} disabled={locked}
                  onChange={e => edit(index, { person: { ...item.person, [key]: e.target.value || null } })} /></label>)}</div>
              <label>Biography<textarea rows={3} maxLength={8000} value={item.person.biography || ''} disabled={locked}
                onChange={e => edit(index, { person: { ...item.person, biography: e.target.value || null } })} /></label>
              {['practice_areas', 'qualifications'].map(key => <label key={key}>{key === 'practice_areas' ? 'Practice areas' : 'Qualifications'} (one per line)
                <textarea rows={2} value={item.person[key].join('\n')} disabled={locked}
                  onChange={e => edit(index, { person: { ...item.person, [key]: e.target.value.split('\n') } })} /></label>)}
            </details>}
            {item.rep_edited && <span className="badge">Rep edited · sources describe the original website record</span>}
            <div className="source-links">{item.sources.map(s => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title || 'Website source'} ↗</a>)}</div>
            {item.evidence && <details><summary>Original supporting passage</summary><blockquote>{item.evidence}</blockquote></details>}
          </article>)}</div></div>)}
      <div className="review-actions"><button disabled={locked || !dirty}>Save research edits</button>
        <span role="status">{dirty ? 'Unsaved edits — save before approving or re-running research.' : 'All edits saved.'}</span></div>
    </form>
    {detail.apollo_findings?.length > 0 && <div className="section-title"><h3>Review Apollo findings</h3>
      <small>From Apollo's database, not the firm's website. Not editable; untick anything that looks wrong.</small></div>}
    <ApolloReview items={detail.apollo_findings || []} selected={apolloSelected} setSelected={setApolloSelected} />
    <div className="approval-bar"><div><strong>{count} {count === 1 ? 'finding' : 'findings'} selected</strong><p>Approval saves a fixed copy for output generation. It makes no paid API call.</p></div>
      <button disabled={locked || dirty || !count} onClick={() => approve(selected, apolloSelected)}>Approve &amp; create outputs →</button></div>
  </>
}

function OutputStudio({ detail, busy, mutate, approvalId, onApproval }) {
  const [formats, setFormats] = useState(['pptx'])
  const [instructions, setInstructions] = useState('')
  const [logoOverride, setLogo] = useState(null)
  const [notice, setNotice] = useState('')
  const pendingRequest = useRef(null)
  const logo = logoOverride ?? detail.preferred_logo_id ?? ''
  const selectedId = approvalId ?? detail.proposals[0]?.id ?? ''
  const approval = detail.proposals.find(p => p.id === Number(selectedId))
  const olderApproval = approval && (approval.snapshot.firm !== detail.name ||
    approval.snapshot.findings.some(f => ![...detail.brief, ...(detail.apollo_findings || [])].some(current => JSON.stringify(current) === JSON.stringify(f))))
  const running = detail.outputs.some(job => ['queued', 'running'].includes(job.status))
  async function generate(event) {
    event.preventDefault()
    const body = { approval_id: Number(selectedId), formats, instructions, logo_id: logo === '' ? null : Number(logo) }
    const signature = JSON.stringify(body)
    if (pendingRequest.current?.signature !== signature) pendingRequest.current = { signature, key: crypto.randomUUID() }
    const result = await mutate(`/${detail.id}/outputs`, { ...body, request_key: pendingRequest.current.key })
    if (result) {
      setNotice(`Run #${result.id} accepted. You can leave this tab; downloads are saved to the firm.`)
      pendingRequest.current = null
    }
  }
  function retry(job) {
    onApproval(job.approval_id)
    setFormats(job.files.filter(f => f.status !== 'ready').map(f => f.format))
    setInstructions(job.instructions)
    setNotice('Failed formats selected for a new run. Check the branding and instructions, then generate.')
  }
  return <>
    <h3>Create a sales pack</h3><p>OpenAI creates files inside its sandbox container. Validated downloads are saved here before the container is deleted.</p>
    {!detail.proposals.length ? <div className="empty-state">No approved brief yet. Review findings in Research, then choose “Approve &amp; create outputs”.</div> :
      <form className="output-form" onSubmit={generate}>
        <label>Approved brief<select value={selectedId} disabled={busy} onChange={e => onApproval(Number(e.target.value))}>
          {detail.proposals.map(p => <option key={p.id} value={p.id}>Brief #{p.id} · {p.snapshot.firm} · {p.snapshot.findings.length} findings</option>)}</select></label>
        {olderApproval && <p className="coverage">This approval predates the current research or edits. To include the updated staff and findings, approve them in Research first.</p>}
        {approval && <details><summary>Preview the approved data ({approval.snapshot.findings.length} findings)</summary>
          <p>Frozen snapshot for {approval.snapshot.firm}. Research edits do not change this approval.</p>
          <ul>{approval.snapshot.findings.map((f, i) => <li key={i}>{f.title && <strong>{f.title}: </strong>}{f.text}</li>)}</ul>
          <a href={`${base}/${detail.id}/proposals/${approval.id}/demo-export`}>Download approved firm and staff JSON</a>
        </details>}
        <fieldset><legend>What should the container create?</legend>{Object.entries(formatLabels).map(([format, label]) =>
          <label className="format-option" key={format}><input type="checkbox" checked={formats.includes(format)} disabled={busy}
            onChange={e => setFormats(e.target.checked ? [...formats, format] : formats.filter(f => f !== format))} />
            <span><strong>{label}</strong><small>.{format} · {format === 'pdf' ? 'Prospective demo welcome guide' : 'Editable sales material'}</small></span></label>)}</fieldset>
        <label>Branding for this run<select value={logo} disabled={busy} onChange={e => setLogo(e.target.value)}>
          <option value="">No firm logo</option>{detail.brand_assets.map(asset => <option key={asset.id} value={asset.id}>Saved firm logo #{asset.id}</option>)}</select></label>
        <label>Sales instructions<textarea maxLength={6000} rows={5} value={instructions} disabled={busy} onChange={e => setInstructions(e.target.value)}
          placeholder="Who is the audience? What should we demonstrate? e.g. Prepare a partner-level discussion focused on family law and a proposed pilot. Do not invent pricing." /></label>
        <p className="muted">This sends the selected approved brief and optional logo to OpenAI. Each generation uses paid API and container resources. No emails or invitations are sent.</p>
        <button disabled={busy || running || !formats.length || !approval}>{running ? 'Container run in progress…' : 'Generate selected outputs'}</button>
      </form>}
    {notice && <p role="status">{notice}</p>}
    <div className="section-title"><h3>Output runs &amp; downloads</h3><small>Progress is tracked per file. Successful downloads remain available even if another format fails.</small></div>
    {!detail.outputs.length && <p>No output runs yet.</p>}
    {detail.outputs.map(job => <article className="output-run" key={job.id}><div className="run-heading"><h3>Run #{job.id} · Brief #{job.approval_id}</h3><span className="badge">{job.status}</span></div>
      <p>{job.stage} · {job.created_at} UTC</p>{job.error && <p role="alert">{job.error}</p>}
      {job.instructions && <details><summary>Sales instructions</summary><p>{job.instructions}</p></details>}
      <ul className="file-list">{job.files.map(file => <li key={file.id}><strong>{formatLabels[file.format]}</strong>
        {file.status === 'ready' ? <a href={`${base}/${detail.id}/outputs/${job.id}/files/${file.id}`}>Download .{file.format} · {Math.ceil(file.size / 1024)} KB</a>
          : <span>{file.error || file.status}</span>}</li>)}</ul>
      {['partial', 'failed'].includes(job.status) && <button type="button" disabled={busy || running} onClick={() => retry(job)}>Prepare retry of failed formats</button>}
    </article>)}
    {detail.proposals.some(p => p.status === 'ready') && <details><summary>Earlier PowerPoint downloads</summary>
      {detail.proposals.filter(p => p.status === 'ready').map(p => <p key={p.id}><a href={`${base}/${detail.id}/proposals/${p.id}/download`}>Brief #{p.id} · PowerPoint</a></p>)}</details>}
  </>
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

const defaultTitles = 'Managing Partner, Partner, Practice Manager, General Manager, Operations Manager, Finance Manager, CFO'

function ApolloContact({ person }) {
  if (!person.apollo_id) return 'Not revealed'
  return <>{person.apollo_email || 'No email found'}{person.email_status && ` (${person.email_status})`}
    {person.linkedin_url && <> · <a href={person.linkedin_url} target="_blank" rel="noreferrer">LinkedIn ↗</a></>}
    {person.match_confidence && <><br /><small>Match confidence: {person.match_confidence}</small></>}</>
}

function ApolloContacts({ detail, busy, submit }) {
  const [titles, setTitles] = useState(defaultTitles)
  const [seniorities, setSeniorities] = useState('')
  const [result, setResult] = useState(null)
  const [selected, setSelected] = useState([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const revealed = new Set(detail.staff.filter(p => p.apollo_email).map(p => p.apollo_id))
  const picks = selected.filter(i => !revealed.has(i))
  async function find(event) {
    event.preventDefault()
    setSearching(true); setError('')
    const params = new URLSearchParams()
    if (titles.trim()) params.append('titles', titles)
    if (seniorities.trim()) params.append('seniorities', seniorities)
    try { setResult(await request(`/${detail.id}/apollo/candidates?${params}`)); setSelected([]) }
    catch (e) { setError(e.message) } finally { setSearching(false) }
  }
  const searchNote = result && (result.fallback
    ? `No one matched those titles, so this list comes from a seniority search (${result.seniorities.join(', ')}).`
    : `Found by ${result.search === 'titles' ? 'title' : result.search === 'seniorities' ? 'seniority' : 'title and seniority'} search.`)
  return <div className="apollo-panel"><h3>Apollo contacts</h3>
    <form className="apollo-search" onSubmit={find}>
      <label>Job titles (comma-separated)<input value={titles} onChange={e => setTitles(e.target.value)} /></label>
      <label>Seniorities (optional, e.g. owner, director)<input value={seniorities} onChange={e => setSeniorities(e.target.value)} /></label>
      <button disabled={searching}>{searching ? 'Searching…' : 'Find people (free)'}</button>
    </form>
    <p>Searching is free and shows only first names. If no titles match, Apollo is searched by seniority instead.</p>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p>{searchNote} {result.candidates.length} of {result.total} shown.</p>
      {result.candidates.length > 0 && <ul className="candidate-list">{result.candidates.map(c => {
        const done = revealed.has(c.apollo_id)
        return <li key={c.apollo_id}><label className="choice">
          <input type="checkbox" disabled={done} checked={done || picks.includes(c.apollo_id)}
            onChange={e => setSelected(e.target.checked ? [...picks, c.apollo_id] : picks.filter(i => i !== c.apollo_id))} />
          <span><strong>{[c.first_name, c.last_name_obfuscated].filter(Boolean).join(' ') || 'Unnamed'}</strong> · {c.title || 'No title'}
            {' · '}{done ? 'Email saved' : c.has_email ? 'Apollo has an email' : 'Email unlikely'}</span></label></li>
      })}</ul>}
      {result.candidates.length > 0 && <button disabled={busy || !picks.length} onClick={() => submit('apollo/reveal', { ids: picks })}>
        Reveal {picks.length} {picks.length === 1 ? 'email' : 'emails'} ({picks.length} {picks.length === 1 ? 'credit' : 'credits'})</button>}
    </>}</div>
}

function StaffRecords({ detail }) {
  if (!detail.staff?.length) return null
  return <div><div className="section-title"><h3>Staff records ({detail.staff.length})</h3>
    <small>Website profiles and revealed Apollo contacts saved for demo preparation. These are not active Acctivity accounts.</small></div>
    <div className="staff-table"><table><thead><tr><th>Name</th><th>Position</th><th>Public work contact</th><th>Apollo</th></tr></thead>
      <tbody>{detail.staff.map(person => <tr key={person.id}><td>{person.full_name}</td><td>{person.position || 'Not published'}</td>
        <td>{[person.public_work_email, person.public_work_phone].filter(Boolean).join(' · ') || 'Not published'}</td>
        <td><ApolloContact person={person} /></td></tr>)}</tbody></table></div></div>
}

// Apollo findings: no website passage; each shows "Source: Apollo, retrieved <date>". Long lists (tech stack,
// keywords) render as compact checkboxes with a per-group Select all / Clear all.
function ApolloReview({ items, selected, setSelected }) {
  const categories = [...new Set(items.map(item => item.category))]
  return categories.map(category => {
    const group = items.filter(item => item.category === category)
    const keys = group.map(item => item.key)
    const chosen = keys.filter(k => selected.includes(k)).length
    const contacts = category === 'Apollo: contacts'
    return <div key={category}><h3>{category} <small>({chosen} of {group.length} selected)</small></h3>
      <p><small>{group[0].sources[0].title}. From Apollo's database, not the firm's website.
        {category === 'Apollo: tech stack' && ' Apollo mostly reports web and IT tools (CDN, analytics, Microsoft 365), not practice-management software. Untick anything that looks wrong.'}</small>
        {' '}<button type="button" onClick={() => setSelected(chosen < group.length ? [...new Set([...selected, ...keys])] : selected.filter(k => !keys.includes(k)))}>
          {chosen < group.length ? 'Select all' : 'Clear all'}</button></p>
      <ul className={contacts ? 'apollo-list' : 'apollo-list apollo-compact'}>{group.map(item => <li key={item.key}>
        <label className="choice"><input type="checkbox" checked={selected.includes(item.key)}
          onChange={e => setSelected(e.target.checked ? [...selected, item.key] : selected.filter(k => k !== item.key))} />
          <span title={contacts ? undefined : item.text}>{contacts ? <><strong>{item.title}<br /></strong>{item.text}</> : item.title}</span></label>
        {contacts && item.person?.linkedin_url && <div><a href={item.person.linkedin_url} target="_blank" rel="noreferrer">LinkedIn ↗</a></div>}
      </li>)}</ul></div>
  })
}
