import { useCallback, useEffect, useRef, useState } from 'react'
import './Prospects.css'
import logo from './assets/logo.png'
import firmIcon from './assets/icon-firm.svg'
import FindFirms from './FindFirms'
import { requestKey } from './requestKey'

// Find firms (Apollo search) is hidden for now; set to true to show it again.
const SHOW_FIND_FIRMS = false
// Review Apollo findings is hidden for now. While hidden, approvals include no Apollo findings,
// so unreviewed Apollo data never reaches outputs. Set to true to show and approve them again.
const SHOW_APOLLO_REVIEW = false

const base = '/api/v1/firms'
const labels = { new: 'Not researched', researching: 'Researching', ready: 'Brief ready', needs_review: 'Needs review', failed: 'Research failed' }
const formatLabels = { pptx: 'PowerPoint proposal', docx: 'Word proposal', pdf: 'Welcome PDF', video: 'Personalised AI video' }
const formatHints = { pptx: '.pptx · Editable sales material', docx: '.docx · Editable sales material', pdf: '.pdf · Prospective demo welcome guide', video: 'Sendspark · the AI voice says the firm name' }
const sourceLabels = { website: 'Website crawl', website_crawl: 'Website crawl', web_search: 'OpenAI web search', reputation: 'Public reputation search', reputation_search: 'Public reputation search', apollo: 'Apollo enrichment and contacts' }

async function request(path = '', options) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  try {
    const res = await fetch(base + path, { ...options, signal: controller.signal })
    if (!res.headers.get('content-type')?.includes('application/json')) {
      throw new Error('Cannot reach the firms API. Check the FastAPI server on port 9001.')
    }
    const result = await res.json()
    if (!res.ok) {
      const message = Array.isArray(result.detail) ? result.detail.map(e => e.msg).join('; ') : result.detail
      throw new Error(typeof message === 'string' ? message : `Request failed (${res.status}).`)
    }
    if (/^\/\d+$/.test(path) && (!Array.isArray(result.outputs) || !result.records ||
        !Array.isArray(result.proposals) || result.proposals.some(p => !p.snapshot))) {
      throw new Error('The API is running an older workspace version. Restart FastAPI on port 9001, then refresh.')
    }
    return result
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The API did not respond within 15 seconds. The background job may still be running; refresh to reconnect, rather than starting another paid run.')
    throw error
  } finally { clearTimeout(timer) }
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
  const [lastPollAt, setLastPollAt] = useState(null)
  const [liveState, setLiveState] = useState('connecting')
  const reload = useCallback(() => refresh(v => v + 1), [])
  useEffect(() => {
    if (!id) return
    let active = true
    const stream = new EventSource(`${base}/${id}/events`)
    stream.onopen = () => { if (active) setLiveState('live') }
    stream.onmessage = event => {
      try {
        const firm = JSON.parse(event.data)
        if (active && firm.id === id) {
          setDetail(firm); setLastPollAt(Date.now())
          setFirms(rows => rows.map(row => row.id === id ? { ...row, name: firm.name, status: firm.status } : row))
        }
      } catch {
        if (active) setError('Live updates could not be read. Periodic polling is still available; refresh to reconnect.')
        stream.close()
      }
    }
    stream.addEventListener('heartbeat', () => { if (active) setLastPollAt(Date.now()) })
    stream.onerror = () => { if (active) setLiveState('reconnecting') }
    return () => { active = false; stream.close() }
  }, [id])
  useEffect(() => {
    let active = true
    let timer
    async function load() {
      try {
        const [rows, selected] = await Promise.all([request(), id ? request('/' + id) : null])
        if (!active) return
        setFirms(rows); setDetail(selected); setLastPollAt(Date.now())
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
  return <div className="prospects-page">
  <main className="prospects">
    <header className="directory-header">
      <a className="app-brand" href="/prospects"><img src={logo} alt="" width="28" height="28" /><span>Stalktivity</span></a>
      <h1>Prospect workspace</h1>
      <p>Research the firm. Review its people. Build a sales pack that feels personal.</p></header>
    <form className="intake" onSubmit={add}><h2>Add a firm</h2><label>Firm website<input name="website" required maxLength={2048} placeholder="firm.com.au" /></label>
      <label>Display name (optional)<input name="display_name" maxLength={160} /></label><button disabled={busy}>Save firm</button></form>
    {SHOW_FIND_FIRMS && <FindFirms onAdded={reload} />}
    {error && <div role="alert" className="error-banner">{error} <button onClick={() => { setError(''); reload() }}>Refresh</button></div>}
    <div className="prospect-grid"><aside className="directory"><h2>Firms <span className="badge">{firms.length}</span></h2>
      <label>Filter directory<input value={query} onChange={e => search(e.target.value)} placeholder="Name or website" /></label>
      {matches.map(f => <button disabled={busy} className="firm-row" aria-pressed={id === f.id} key={f.id} onClick={() => chooseFirm(f.id)}>
        <strong>{f.name}</strong><span>{f.domain}</span><small data-status={f.status}>{labels[f.status]}</small></button>)}
      {!matches.length && <p>{firms.length ? 'No matching firms.' : 'No firms saved yet.'}</p>}</aside>
    <section className="workspace">{detail && detail.id === id
      ? <><p className="connection-status" role="status">{liveState === 'live' ? 'Live updates connected' : 'Live updates reconnecting · periodic polling available'}</p>
        <FirmWorkspace key={id} detail={detail} busy={busy} dirty={dirty} mutate={mutate} onDirty={setDirty} lastPollAt={lastPollAt} /></>
      : <div className="workspace-empty"><span className="empty-icon"><img src={firmIcon} alt="" width="24" height="24" /></span>
        <h2>Firm workspace</h2><p>{id ? 'Loading firm…' : 'Select a firm or save a website to get started.'}</p></div>}</section></div>
  </main>
  </div>
}

function FirmWorkspace({ detail, busy, dirty, mutate, onDirty, lastPollAt }) {
  const [tab, setTab] = useState('research')
  const [approvalId, setApprovalId] = useState(null)
  // Approval sends the ticked record ids per kind, Apollo finding keys and web finding ids.
  async function approve(selection, apolloKeys, webIds) {
    const result = await mutate(`/${detail.id}/approvals`, { brief_token: detail.brief_token, ...selection, apollo_findings: apolloKeys, web_findings: webIds })
    if (result) { setApprovalId(result.id); setTab('outputs') }
  }
  const submit = (path, body) => mutate(`/${detail.id}/${path}`, body)
  // Apollo changes the review token, which resets the research editor, so Apollo actions wait for saved edits.
  const apolloBusy = busy || dirty || detail.status === 'researching'
  const apollo = <>
    <ApolloPanel detail={detail} busy={apolloBusy} submit={submit} resultsOnly />
    {detail.apollo_configured && <ApolloContacts detail={detail} busy={apolloBusy} submit={submit}
      initialResult={detail.research_meta?.sources?.apollo?.contact_search} />}
    {dirty && detail.apollo_configured && <p className="coverage">Save or discard research edits before using Apollo.</p>}
    <ApolloContactRecords detail={detail} />
  </>
  return <>
    <div className="workspace-heading"><div><small>FIRM WORKSPACE</small><h2>{detail.name}</h2><p>{detail.domain}</p></div>
      <span className="badge" data-status={detail.status}>{labels[detail.status]}</span></div>
    <div className="workspace-tabs" role="tablist" aria-label="Firm workspace">
      <button id="research-tab" role="tab" aria-selected={tab === 'research'} aria-controls="research-panel" onClick={() => setTab('research')}>01 &nbsp; Research</button>
      <button id="outputs-tab" role="tab" aria-selected={tab === 'outputs'} aria-controls="outputs-panel" onClick={() => setTab('outputs')}>02 &nbsp; Create outputs</button>
    </div>
    <div id="research-panel" role="tabpanel" aria-labelledby="research-tab" hidden={tab !== 'research'}>
      <ResearchEditor key={detail.brief_token} detail={detail} busy={busy} mutate={mutate} approve={approve} onDirty={onDirty} apollo={apollo} lastPollAt={lastPollAt} />
    </div>
    <div id="outputs-panel" role="tabpanel" aria-labelledby="outputs-tab" hidden={tab !== 'outputs'}>
      <OutputStudio detail={detail} busy={busy} mutate={mutate} approvalId={approvalId} onApproval={setApprovalId} />
    </div>
  </>
}

const recordKinds = [['practice_areas', 'Practice areas'], ['people', 'People'], ['offices', 'Offices'], ['facts', 'Firm facts']]
const factKinds = { overview: 'Overview', history: 'History', credential: 'Credential', clients: 'Clients', approach: 'Approach' }
// Editable fields per record kind: [key, label, 'line' | 'long' | 'list', required]
const recordFields = {
  facts: [['title', 'Title', 'line', true], ['detail', 'Detail', 'long', true]],
  practice_areas: [['name', 'Practice area', 'line', true], ['description', 'What they handle', 'long', true], ['services', 'Services (one per line)', 'list']],
  people: [['full_name', 'Full name', 'line', true], ['position', 'Position', 'line'], ['public_work_email', 'Public work email', 'line'],
    ['public_work_phone', 'Public work phone', 'line'], ['biography', 'Biography', 'long'], ['qualifications', 'Qualifications (one per line)', 'list']],
  offices: [['name', 'Office', 'line', true], ['address', 'Address', 'line'], ['phone', 'Phone', 'line'], ['email', 'Email', 'line']],
}
const allIds = records => Object.fromEntries(recordKinds.map(([kind]) => [kind, records[kind].map(r => r.id)]))
const countRecords = records => recordKinds.reduce((n, [kind]) => n + records[kind].length, 0)
const snapshotCount = s => countRecords(s) + (s.apollo?.length || 0) + (s.web?.length || 0)
function recordLabel(kind, r) {
  if (kind === 'people') return `${r.full_name} · ${r.position || 'Position not published'}`
  return kind === 'facts' ? `${factKinds[r.kind]}: ${r.title}` : r.name
}
function recordSummary(kind, r, areas) {
  if (kind === 'facts') return r.detail
  if (kind === 'practice_areas') return r.description
  if (kind === 'offices') return [r.address, r.phone, r.email].filter(Boolean).join(' · ')
  return areas.filter(a => r.practice_area_ids.includes(a.id)).map(a => a.name).join(' · ')
}

function RecordField({ value, label, type, required, disabled, onChange }) {
  if (type === 'list') return <label>{label}<textarea rows={2} value={value.join('\n')} disabled={disabled} onChange={e => onChange(e.target.value.split('\n'))} /></label>
  const props = { required, disabled, value: value || '', onChange: e => onChange(e.target.value || null) }
  return <label>{label}{type === 'long' ? <textarea rows={3} maxLength={12000} {...props} /> : <input maxLength={320} {...props} />}</label>
}

function ResearchEditor({ detail, busy, mutate, approve, onDirty, apollo, lastPollAt }) {
  const savedSelection = detail.research_meta?.selection || (detail.research_meta?.sources
    ? Object.fromEntries(['website', 'web_search', 'reputation', 'apollo'].map(name => [name, Boolean(detail.research_meta.sources[name])]))
    : { website: true, web_search: true, reputation: true, apollo: false })
  const [name, setName] = useState(detail.name)
  const [logoId, setLogoId] = useState(detail.preferred_logo_id ?? '')
  const [records, setRecords] = useState(detail.records)
  const [selected, setSelected] = useState(() => allIds(detail.records))
  const [apolloSelected, setApolloSelected] = useState(SHOW_APOLLO_REVIEW ? (detail.apollo_findings || []).map(item => item.key) : [])
  const [notice, setNotice] = useState('')
  const dirty = name !== detail.name || String(logoId) !== String(detail.preferred_logo_id ?? '') ||
    JSON.stringify(records) !== JSON.stringify(detail.records)
  const locked = busy || detail.status === 'researching'
  const total = countRecords(detail.records)
  // Reputation observations are unverified, so they start unticked.
  const [webSelected, setWebSelected] = useState((detail.web_findings || []).filter(f => f.kind !== 'reputation_search').map(f => f.id))
  const [website, setWebsite] = useState(savedSelection.website)
  const [webSearch, setWebSearch] = useState(savedSelection.web_search)
  const [reputation, setReputation] = useState(savedSelection.reputation)
  // Apollo enrichment spends a credit, so it starts unticked.
  const [enrichApollo, setEnrichApollo] = useState(savedSelection.apollo)
  const visibleWeb = (detail.web_findings || []).filter(f => f.kind === 'web_search' ? webSearch : reputation)
  const visibleWebIds = webSelected.filter(id => visibleWeb.some(f => f.id === id))
  const visibleSelection = website ? selected : Object.fromEntries(recordKinds.map(([kind]) => [kind, []]))
  const visibleApolloKeys = SHOW_APOLLO_REVIEW && enrichApollo ? apolloSelected : []
  const count = countRecords(visibleSelection) + visibleApolloKeys.length + visibleWebIds.length
  const sourceSelection = { website, web_search: webSearch, reputation, apollo: enrichApollo }
  const progressMeta = detail.research_meta?.sources ? {
    ...detail.research_meta,
    sources: Object.fromEntries(Object.entries(detail.research_meta.sources).filter(([source]) => sourceSelection[source])),
  } : null
  useEffect(() => { onDirty(dirty) }, [dirty, onDirty])
  useEffect(() => {
    function warn(e) { if (dirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  function edit(kind, id, patch) {
    setRecords(previous => ({ ...previous, [kind]: previous[kind].map(r => r.id === id ? { ...r, ...patch } : r) }))
    setNotice('')
  }
  function toggle(kind, id, on) {
    setSelected(previous => ({ ...previous, [kind]: on ? [...previous[kind], id] : previous[kind].filter(n => n !== id) }))
  }
  async function save(event) {
    event.preventDefault()
    await mutate(`/${detail.id}/brief`, { brief_token: detail.brief_token, display_name: name, logo_id: logoId === '' ? null : Number(logoId), ...records }, 'PUT')
  }
  async function runResearch(reusePages) {
    const message = reusePages
      ? `Re-extract from the ${detail.pages_saved} saved pages? This paid run replaces current records without re-reading the website. Approved briefs stay unchanged.`
      : `Run the selected sources? ${enrichApollo ? 'Apollo enrichment uses 1 credit plus free contact search; email reveals are not included. ' : ''}${website ? 'Website records will be replaced. ' : ''}Approved briefs stay unchanged.`
    if ((total || (!reusePages && enrichApollo)) && !window.confirm(message)) return
    const result = await mutate(`/${detail.id}/research`, reusePages
      ? { website: true, reuse_pages: true, web_search: false, reputation: false, apollo: false }
      : { website, web_search: webSearch, reputation, apollo: enrichApollo })
    if (result && reusePages) {
      setWebsite(true); setWebSearch(false); setReputation(false); setEnrichApollo(false)
    }
    if (result) setNotice(reusePages ? 'Re-extraction started. Records will update here.' : 'Research started. Page coverage and results will update here.')
  }
  return <>
    <div className="research-toolbar"><div><h3>Research sources</h3><p>Selected sources run in parallel. Website research reads up to 12 priority pages; disabled sources make no calls and their results are hidden, not deleted.</p>
      <label className="choice"><input type="checkbox" checked={website} disabled={locked} onChange={e => setWebsite(e.target.checked)} /> Website crawl and record extraction</label>
      <label className="choice"><input type="checkbox" checked={webSearch} disabled={locked} onChange={e => setWebSearch(e.target.checked)} /> OpenAI wider web search</label>
      <label className="choice"><input type="checkbox" checked={reputation} disabled={locked} onChange={e => setReputation(e.target.checked)} /> Public reputation search (not a verified Google review sample)</label>
      <label className="choice"><input type="checkbox" checked={enrichApollo} disabled={locked || !detail.apollo_configured} onChange={e => setEnrichApollo(e.target.checked)} /> Enrich with Apollo (1 credit){!detail.apollo_configured && ' · Apollo not configured'}</label></div>
      <div className="toolbar-actions">
        {website && detail.pages_saved > 0 && <button type="button" className="secondary" disabled={locked || dirty} onClick={() => runResearch(true)}>Re-extract from {detail.pages_saved} saved pages</button>}
        <button type="button" disabled={locked || dirty || !(website || webSearch || reputation || enrichApollo)} onClick={() => runResearch(false)}>{detail.status === 'researching' ? 'Researching…' : 'Run research'}</button></div></div>
    {notice && detail.status === 'researching' && <p role="status">{notice}</p>}
    {website && detail.research_meta?.pages_read > 0 && <p className="coverage">{detail.status !== 'researching' && 'Last run: '}{detail.research_meta.pages_read} pages read · {detail.status === 'researching' ? detail.research_meta.stage : `${total} records · ${detail.records.people.length} people · ${visibleWeb.length} web findings`}</p>}
    {progressMeta && Object.keys(progressMeta.sources).length > 0 && <ResearchProgress meta={progressMeta} active={detail.status === 'researching'} lastPollAt={lastPollAt} />}
    {detail.research_meta?.warnings?.length > 0 && <details><summary>Research coverage notes ({detail.research_meta.warnings.length})</summary><ul>{detail.research_meta.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
    {detail.error && <p role="alert">{detail.error}</p>}
    {enrichApollo && apollo}
    <form className="research-form" onSubmit={save}>
      <label>Firm display name<input required maxLength={160} value={name} disabled={locked} onChange={e => setName(e.target.value)} /></label>
      {website && <><div className="section-title"><h3>Firm branding</h3><small>Choose the preferred saved logo.</small></div>
      <label className="choice"><input type="radio" name="logo" value="" checked={logoId === ''} disabled={locked} onChange={() => setLogoId('')} /> No preferred logo</label>
      <div className="logo-options">{detail.brand_assets.map(asset => <label className="logo-option" key={asset.id}>
        <input type="radio" name="logo" checked={String(logoId) === String(asset.id)} disabled={locked} onChange={() => setLogoId(asset.id)} />
        <img className="firm-logo" alt={`Saved firm logo ${asset.id}`} src={`${base}/${detail.id}/brand-assets/${asset.id}`} />
        <a href={`${base}/${detail.id}/brand-assets/${asset.id}`} download>Download logo</a></label>)}</div>
      {!detail.brand_assets.length && <p>No logo saved. Research will try to collect a published website logo.</p>}
      <div className="section-title"><h3>Review and edit research records</h3><small>Each record keeps its source page. Your corrections are labelled as rep edits.</small></div>
      {!total && <p>Run research to populate the firm's practice areas, people, offices and facts.</p>}
      {total > 0 && recordKinds.map(([kind, title]) => <details key={kind} className="record-category"><summary><h3 className="category-heading">{title} <span className="badge">{records[kind].length}</span></h3></summary>
        {!records[kind].length && <p className="muted">None found on the website.</p>}
        <div className="finding-list">{records[kind].map(record => <article className="finding-card" key={record.id}>
          <label className="choice"><input type="checkbox" checked={selected[kind].includes(record.id)} disabled={locked}
            onChange={e => toggle(kind, record.id, e.target.checked)} /><strong>Include {recordLabel(kind, record)}</strong></label>
          <p className="muted">{recordSummary(kind, record, records.practice_areas)}</p>
          <details className="staff-editor"><summary>Edit</summary>
            {kind === 'facts' && <label>Type<select value={record.kind} disabled={locked} onChange={e => edit(kind, record.id, { kind: e.target.value })}>
              {Object.entries(factKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
            <div className="field-grid">{recordFields[kind].filter(([, , type]) => type === 'line').map(([key, label, type, required]) =>
              <RecordField key={key} label={label} type={type} required={required} value={record[key]} disabled={locked} onChange={value => edit(kind, record.id, { [key]: value })} />)}</div>
            {recordFields[kind].filter(([, , type]) => type !== 'line').map(([key, label, type, required]) =>
              <RecordField key={key} label={label} type={type} required={required} value={record[key]} disabled={locked} onChange={value => edit(kind, record.id, { [key]: value })} />)}
            {kind === 'people' && records.practice_areas.length > 0 && <fieldset><legend>Practice areas</legend>{records.practice_areas.map(area =>
              <label className="choice" key={area.id}><input type="checkbox" checked={record.practice_area_ids.includes(area.id)} disabled={locked}
                onChange={e => edit(kind, record.id, { practice_area_ids: e.target.checked ? [...record.practice_area_ids, area.id].sort((a, b) => a - b) : record.practice_area_ids.filter(n => n !== area.id) })} />{area.name}</label>)}</fieldset>}
          </details>
          {record.original && <span className="badge">Rep edited · the source shows the original website record</span>}
          <div className="source-links"><a href={record.source.url} target="_blank" rel="noreferrer">{record.source.title || 'Website source'} ↗</a></div>
          <details><summary>Original supporting passage</summary><blockquote>{record.evidence}</blockquote></details>
        </article>)}</div></details>)}</>}
      <div className="review-actions"><button disabled={locked || !dirty}>Save research edits</button>
        <span role="status">{dirty ? 'Unsaved edits — save before approving or re-running research.' : 'All edits saved.'}</span></div>
    </form>
    {visibleWeb.length > 0 && <div className="section-title"><h3>Review web findings</h3>
      <small>Cited from public sources other than the firm's website. Not editable; reputation observations start unticked.</small></div>}
    <WebReview items={visibleWeb} selected={webSelected} setSelected={setWebSelected} />
    {SHOW_APOLLO_REVIEW && enrichApollo && detail.apollo_findings?.length > 0 && <div className="section-title"><h3>Review Apollo findings</h3>
      <small>From Apollo's database, not the firm's website. Not editable; untick anything that looks wrong.</small></div>}
    {SHOW_APOLLO_REVIEW && enrichApollo && <ApolloReview items={detail.apollo_findings || []} selected={apolloSelected} setSelected={setApolloSelected} />}
    <div className="approval-bar"><div><strong>{count} {count === 1 ? 'item' : 'items'} selected</strong><p>Approval saves a fixed copy for output generation. It makes no paid API call.</p></div>
      <button disabled={locked || dirty || !count} onClick={() => approve(visibleSelection, visibleApolloKeys, visibleWebIds)}>Approve &amp; create outputs →</button></div>
  </>
}

function ResearchProgress({ meta, active, lastPollAt }) {
  const running = active && !meta.finished_at
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  function duration(start, end) {
    const seconds = Math.max(0, Math.floor(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000))
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  }
  return <div>
    {running ? <p className="coverage">{meta.started_at && `Elapsed ${duration(meta.started_at)} · `}
      {lastPollAt && `API last responded ${Math.max(0, Math.floor((now - lastPollAt) / 1000))}s ago`}
      {meta.updated_at && ` · Last progress ${duration(meta.updated_at)} ago`}</p> :
      <p className="coverage">{meta.finished_at ? 'Research finished' : 'Research stopped'}
        {meta.started_at && (meta.finished_at || meta.updated_at) && ` · ${meta.finished_at ? 'Total duration' : 'Last recorded duration'} ${duration(meta.started_at, meta.finished_at || meta.updated_at)}`}
        {meta.finished_at && <> · Finished <time dateTime={meta.finished_at}>{new Date(meta.finished_at).toLocaleString()}</time></>}</p>}
    <div className="source-status" aria-live="polite">{Object.entries(meta.sources).map(([source, result]) => {
      const sourceRunning = running && result.status === 'running'
      const status = !running && result.status === 'running' ? 'interrupted' : result.status
      return (
      <div key={source}><strong>{sourceLabels[source]}</strong>
        <span>{sourceRunning ? result.stage || status : status}{result.findings != null && ` · ${result.findings} findings`}</span>
        {result.started_at && (sourceRunning || result.finished_at || meta.finished_at || meta.updated_at) &&
          <small>{sourceRunning ? 'Elapsed' : 'Duration'} {duration(result.started_at, result.finished_at || (!sourceRunning ? meta.finished_at || meta.updated_at : undefined))}</small>}
        {result.characters_received > 0 && <small> · {result.characters_received.toLocaleString()} response characters received</small>}
        {result.error && <p>{result.error}</p>}
        {sourceRunning && result.preview && <details open><summary>Live draft — not yet verified</summary><pre className="stream-preview">{result.preview}</pre></details>}
        {result.findings_preview?.length > 0 && <details><summary>Read sourced results now</summary>
          {result.findings_preview.map((finding, i) => <div key={i}><p>{finding.text}</p>
            {finding.sources.map(s => <a href={s.url} key={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a>)}</div>)}</details>}
      </div>)})}</div>
    {running && <small>Page reading, model extraction and logo retrieval are separate steps. An old progress timestamp does not prove a timeout; provider timeouts are reported explicitly per source.</small>}
  </div>
}

// Web findings: cited observations from other public sites. Reputation ones are unverified signals.
function WebReview({ items, selected, setSelected }) {
  return ['web_search', 'reputation_search'].map(kind => {
    const group = items.filter(item => item.kind === kind)
    if (!group.length) return null
    return <div key={kind}><h3>{sourceLabels[kind]} <small>({group.filter(item => selected.includes(item.id)).length} of {group.length} selected)</small></h3>
      {kind === 'reputation_search' && <p className="reputation-note">Unverified public reputation signals, not a Google review sample or established firm facts. Review before including in sales material.</p>}
      <ul className="apollo-list">{group.map(item => <li key={item.id}><label className="choice">
        <input type="checkbox" checked={selected.includes(item.id)}
          onChange={e => setSelected(e.target.checked ? [...selected, item.id] : selected.filter(n => n !== item.id))} />
        <span>{item.text}</span></label>
        <div className="source-links">{item.sources.map(s => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a>)}</div></li>)}</ul></div>
  })
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
  const olderApproval = approval && approval.snapshot.brief_token !== detail.brief_token
  const running = detail.outputs.some(job => ['queued', 'running'].includes(job.status))
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  function elapsed(file) {
    if (!file.started_at) return ''
    const seconds = Math.max(0, Math.floor(((file.finished_at ? Date.parse(file.finished_at) : now) - Date.parse(file.started_at)) / 1000))
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  }
  async function generate(event) {
    event.preventDefault()
    const body = { approval_id: Number(selectedId), formats, instructions, logo_id: logo === '' ? null : Number(logo) }
    const signature = JSON.stringify(body)
    if (pendingRequest.current?.signature !== signature) pendingRequest.current = { signature, key: requestKey() }
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
    <h3>Create a sales pack</h3><p>OpenAI builds each document in its own sandbox. The approved records, brand kit, fonts, images and selected firm logo are uploaded before generation starts.</p>
    {!detail.proposals.length ? <div className="empty-state">No approved brief yet. Review the records in Research, then choose “Approve &amp; create outputs”.</div> :
      <form className="output-form" onSubmit={generate}>
        <label>Approved brief<select value={selectedId} disabled={busy} onChange={e => onApproval(Number(e.target.value))}>
          {detail.proposals.map(p => <option key={p.id} value={p.id}>Brief #{p.id} · {p.snapshot.firm} · {snapshotCount(p.snapshot)} items</option>)}</select></label>
        {olderApproval && <p className="coverage">This approval predates the current research or edits. To include the updated records, approve them in Research first.</p>}
        {approval && <details><summary>Preview the approved data ({snapshotCount(approval.snapshot)} items)</summary>
          <p>Frozen snapshot for {approval.snapshot.firm}. Research edits do not change this approval.</p>
          {recordKinds.map(([kind, title]) => approval.snapshot[kind].length > 0 && <div key={kind}><strong>{title}</strong>
            <ul>{approval.snapshot[kind].map(r => <li key={r.id}>{recordLabel(kind, r)}</li>)}</ul></div>)}
          {approval.snapshot.apollo?.length > 0 && <div><strong>Apollo</strong>
            <ul>{approval.snapshot.apollo.map(item => <li key={item.key}>{item.category.replace('Apollo: ', '')}: {item.title}</li>)}</ul></div>}
          {approval.snapshot.web?.length > 0 && <div><strong>Web findings</strong>
            <ul>{approval.snapshot.web.map(item => <li key={item.id}>{sourceLabels[item.kind]}: {item.text}</li>)}</ul></div>}
          <a href={`${base}/${detail.id}/proposals/${approval.id}/demo-export`}>Download approved firm and staff JSON</a>
        </details>}
        <fieldset><legend>What should we create?</legend>{Object.entries(formatLabels).map(([format, label]) =>
          <label className="format-option" key={format}><input type="checkbox" checked={formats.includes(format)}
            disabled={busy || (format === 'video' && !detail.sendspark_configured)}
            onChange={e => setFormats(e.target.checked ? [...formats, format] : formats.filter(f => f !== format))} />
            <span><strong>{label}</strong><small>{format === 'video' && !detail.sendspark_configured ? 'Sendspark is not configured on the server' : formatHints[format]}</small></span></label>)}</fieldset>
        <label>Branding for this run<select value={logo} disabled={busy} onChange={e => setLogo(e.target.value)}>
          <option value="">No firm logo</option>{detail.brand_assets.map(asset => <option key={asset.id} value={asset.id}>Saved firm logo #{asset.id}</option>)}</select></label>
        <label>Sales instructions<textarea maxLength={6000} rows={5} value={instructions} disabled={busy} onChange={e => setInstructions(e.target.value)}
          placeholder="Who is the audience? What should we demonstrate? e.g. Prepare a partner-level discussion focused on family law and a proposed pilot. Do not invent pricing." /></label>
        <p className="muted">Each selected document uses a separate paid OpenAI request and sandbox; up to three build in parallel. Approved records and selected branding are uploaded to OpenAI. No emails or invitations are sent.</p>
        {running && <p role="status">A run is already active. Its stages and downloads are shown below; generation is disabled to prevent duplicate paid runs.</p>}
        {formats.includes('video') && <p className="muted">The video sends only the firm name and website to Sendspark and uses Sendspark video minutes (beyond the plan's allowance, Sendspark charges). Rendering can take several minutes.</p>}
        <button disabled={busy || running || !formats.length || !approval}>{running ? 'Generation in progress…' : 'Generate selected outputs'}</button>
      </form>}
    {notice && <p role="status">{notice}</p>}
    <div className="section-title"><h3>Output runs &amp; downloads</h3><small>Progress is tracked per file. Successful downloads remain available even if another format fails.</small></div>
    {!detail.outputs.length && <p>No output runs yet.</p>}
    {detail.outputs.map(job => <article className="output-run" key={job.id}><div className="run-heading"><h3>Run #{job.id} · Brief #{job.approval_id}</h3><span className="badge">{job.status}</span></div>
      <p>{job.stage} · {job.created_at} UTC</p>{job.error && <p role="alert">{job.error}</p>}
      {job.instructions && <details><summary>Sales instructions</summary><p>{job.instructions}</p></details>}
      <ul className="file-list">{job.files.map(file => <li key={file.id}><strong>{formatLabels[file.format]}</strong>
        {file.status === 'ready' && file.url ? <a href={file.url} target="_blank" rel="noreferrer">Watch video ↗</a>
          : file.status === 'ready' ? <a href={`${base}/${detail.id}/outputs/${job.id}/files/${file.id}`}>Download .{file.format} · {Math.ceil(file.size / 1024)} KB</a>
          : <span>{file.error || file.stage || file.status}</span>}
        {file.started_at && <small className="file-progress">{elapsed(file)} elapsed
          {file.status === 'running' && file.updated_at && ` · Last activity ${Math.max(0, Math.floor((now - Date.parse(file.updated_at)) / 1000))}s ago`}</small>}
        {file.status === 'running' && <progress className="file-progress" aria-label={`${formatLabels[file.format]} build in progress`} />}
        {file.preview && <details className="file-progress"><summary>Model response{file.status === 'running' ? ' · live draft' : ''}</summary><pre className="stream-preview">{file.preview}</pre></details>}
      </li>)}</ul>
      {['partial', 'failed'].includes(job.status) && <button type="button" disabled={busy || running} onClick={() => retry(job)}>Prepare retry of failed formats</button>}
    </article>)}
    {detail.proposals.some(p => p.status === 'ready') && <details><summary>Earlier PowerPoint downloads</summary>
      {detail.proposals.filter(p => p.status === 'ready').map(p => <p key={p.id}><a href={`${base}/${detail.id}/proposals/${p.id}/download`}>Brief #{p.id} · PowerPoint</a></p>)}</details>}
  </>
}

function ApolloPanel({ detail, busy, submit, resultsOnly = false }) {
  const org = detail.apollo_org
  if (resultsOnly && !org) return null
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
      {!resultsOnly && <button disabled={busy} onClick={() => submit('apollo/enrich', { refresh: true })}>Refresh enrichment (1 credit)</button>}
    </> : <>
      <p>Look up this firm's headcount, industries and technology in Apollo. Uses 1 Apollo credit.</p>
      <button disabled={busy} onClick={() => submit('apollo/enrich', {})}>Enrich firm (1 credit)</button>
    </>}</div>
}

const defaultTitles = 'Managing Partner, Partner, Practice Manager, General Manager, Operations Manager, Finance Manager, CFO'

function ApolloContact({ person }) {
  return <>{person.apollo_email || 'No email found'}{person.email_status && ` (${person.email_status})`}
    {person.linkedin_url && <> · <a href={person.linkedin_url} target="_blank" rel="noreferrer">LinkedIn ↗</a></>}
    {person.match_confidence && <><br /><small>Match confidence: {person.match_confidence}</small></>}</>
}

function ApolloContacts({ detail, busy, submit, initialResult }) {
  const [titles, setTitles] = useState(defaultTitles)
  const [seniorities, setSeniorities] = useState('')
  const [resultOverride, setResult] = useState(null)
  const result = resultOverride || initialResult || null
  const [selected, setSelected] = useState([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const revealed = new Set(detail.apollo_contacts.filter(c => c.apollo_email).map(c => c.apollo_id))
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
    <details><summary>Refine contact search (optional, free)</summary><form className="apollo-search" onSubmit={find}>
      <label>Job titles (comma-separated)<input value={titles} onChange={e => setTitles(e.target.value)} /></label>
      <label>Seniorities (optional, e.g. owner, director)<input value={seniorities} onChange={e => setSeniorities(e.target.value)} /></label>
      <button disabled={searching}>{searching ? 'Searching…' : 'Find people (free)'}</button>
    </form></details>
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

// Revealed Apollo contacts, kept apart from website research; each links to the website person with the same name.
function ApolloContactRecords({ detail }) {
  if (!detail.apollo_contacts?.length) return null
  const people = Object.fromEntries(detail.records.people.map(p => [p.id, p]))
  return <div><div className="section-title"><h3>Apollo contacts ({detail.apollo_contacts.length})</h3>
    <small>Revealed in Apollo and saved for demo preparation. Re-running website research keeps them. These are not active Acctivity accounts.</small></div>
    <div className="staff-table"><table><thead><tr><th>Name</th><th>Apollo title</th><th>Website profile</th><th>Apollo contact</th></tr></thead>
      <tbody>{detail.apollo_contacts.map(contact => <tr key={contact.id}><td>{contact.full_name}</td><td>{contact.position || 'Not listed'}</td>
        <td>{people[contact.person_id] ? `${people[contact.person_id].full_name} · ${people[contact.person_id].position || 'Position not published'}` : 'Not on the website'}</td>
        <td><ApolloContact person={contact} /></td></tr>)}</tbody></table></div></div>
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
