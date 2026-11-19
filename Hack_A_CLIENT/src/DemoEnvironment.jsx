import { useCallback, useEffect, useState } from 'react'
import './DemoEnvironment.css'

// Demo environment tab (issue #21): rep login, demo plan review, seed / reseed and live progress.
// The Acctivity (test) web app URL comes from VITE_ACCTIVITY_APP_URL; the link is hidden when it is unset.
const appUrl = import.meta.env.VITE_ACCTIVITY_APP_URL
const firms = '/api/v1/firms'
const demo = '/api/v1/demo'
const ACTIVE = ['queued', 'running']
const AU_STATES = ['NSW', 'VIC', 'QLD', 'WA', 'SA', 'ACT', 'TAS', 'NT']
const seniorityLabels = { managing_partner: 'Managing partner', partner: 'Partner', senior_associate: 'Senior associate', associate: 'Associate', paralegal: 'Paralegal' }
const loginLabels = { found: 'Found from the tray app', pasted: 'Pasted token', expired: 'Expired', missing: 'No rep login' }
const loginStatus = { found: 'ready', pasted: 'ready', expired: 'needs_review', missing: 'new' }
const runLabels = { queued: 'Queued', running: 'Running', failed: 'Failed', done: 'Done' }
const runStatus = { queued: 'researching', running: 'researching', failed: 'failed', done: 'ready' }
const countLabels = { matters: 'matters', contacts: 'contacts', activities: 'time entries', fees: 'fixed fees', cost_recoveries: 'disbursements', invoices: 'invoices' }

class ApiError extends Error {
  constructor(message, status, detail) { super(message); this.status = status; this.detail = detail }
}

// Like Prospects' request(), but keeps the status and a structured `detail` (plan validation errors).
async function api(path, { method = 'GET', body } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  try {
    const res = await fetch(path, {
      method, signal: controller.signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    })
    if (!res.headers.get('content-type')?.includes('application/json')) {
      throw new ApiError(res.status === 404 || res.status === 405 ? `Not available on this API version (HTTP ${res.status}).` : 'Cannot reach the API. Check the FastAPI server on port 9001.', res.status)
    }
    const result = await res.json()
    if (!res.ok) {
      const detail = result.detail
      const message = typeof detail === 'string' ? detail : detail?.message ||
        (Array.isArray(detail) ? detail.map(e => e.msg).join('; ') : `Request failed (${res.status}).`)
      throw new ApiError(message, res.status, detail)
    }
    return result
  } catch (error) {
    if (error.name === 'AbortError') throw new ApiError('The API did not respond within 15 seconds; refresh to check the current state.', 0)
    throw error
  } finally { clearTimeout(timer) }
}

const money = (amount, currency) => `${currency || ''} ${Number(amount || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`.trim()
const when = iso => iso ? new Date(iso).toLocaleString() : ''

export default function DemoEnvironment({ detail }) {
  const [login, setLogin] = useState(null)
  const [loginError, setLoginError] = useState('')
  const [saved, setSaved] = useState(undefined) // undefined loading · null no plan · {plan, approval_id, …}
  const [planDirty, setPlanDirty] = useState(false)
  const loadLogin = useCallback(() => api(`${demo}/rep-login`)
    .then(result => { setLogin(result); setLoginError('') }, e => setLoginError(e.message)), [])
  useEffect(() => {
    api(`${demo}/rep-login`).then(setLogin, e => setLoginError(e.message))
    const timer = setInterval(loadLogin, 30000) // the token expires while the tab is open
    return () => clearInterval(timer)
  }, [loadLogin])
  useEffect(() => {
    let active = true
    api(`${firms}/${detail.id}/demo-plan`)
      .then(result => { if (active) setSaved(result) })
      .catch(e => { if (active) setSaved(e.status === 404 ? null : { error: e.message }) })
    return () => { active = false }
  }, [detail.id])
  const loginUsable = ['found', 'pasted'].includes(login?.status)
  return <div className="demo-env">
    <div className="demo-intro"><div><h3>Demo environment</h3>
      <p>Seed the rep's Acctivity test firm with a plan built from the approved research: the prospect's people as staff, fictional clients and matters, time, fees and invoices.</p></div>
      {appUrl && <a className="demo-app-link" href={appUrl} target="_blank" rel="noreferrer">Open Acctivity (test) ↗</a>}</div>
    <RepLogin login={login} error={loginError} onLogin={setLogin} reload={loadLogin} />
    {saved === undefined ? <p className="muted">Loading the demo plan…</p>
      : saved?.error ? <p role="alert">{saved.error}</p>
      : <DemoPlan key={saved?.updated_at || 'none'} detail={detail} saved={saved} onSaved={setSaved} onDirty={setPlanDirty} loginUsable={loginUsable} />}
    {saved !== undefined && !saved?.error && <SeedPanel firmId={detail.id} detail={detail} plan={saved} planDirty={planDirty} loginUsable={loginUsable} />}
  </div>
}

function RepLogin({ login, error, onLogin, reload }) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  async function send(event) {
    event.preventDefault()
    const value = token
    setToken('') // never kept or shown after sending
    setBusy(true); setNotice('')
    try { onLogin(await api(`${demo}/rep-login`, { method: 'PUT', body: { access_token: value, source: 'pasted' } })); setNotice('Token sent. It is kept in the API\'s memory only.') }
    catch (e) { setNotice(e.message) } finally { setBusy(false) }
  }
  async function forget() {
    setBusy(true); setNotice('')
    try { onLogin(await api(`${demo}/rep-login`, { method: 'DELETE' })); setNotice('Rep login forgotten.') }
    catch (e) { setNotice(e.message) } finally { setBusy(false) }
  }
  const status = login?.status
  return <section className="demo-card" aria-labelledby="demo-login-title">
    <div className="demo-card-heading"><h3 id="demo-login-title">Rep login</h3>
      {login && <span className="badge" data-status={loginStatus[status]}>{loginLabels[status] || status}</span>}</div>
    {error && <p role="alert">{error}</p>}
    {login && <p>{status === 'found' || status === 'pasted'
      ? <>Writes to the demo firm use this login until <time dateTime={login.expires_at}>{when(login.expires_at)}</time>. Firm <code>{login.firm_id}</code>.</>
      : login.message || (status === 'expired' ? 'The rep login has expired: open the Acctivity tray app (test) to refresh, then send it again.' : 'No rep login yet.')}</p>}
    <p className="muted">On the host, run <code>Hack_A_API/tools/Send-RepLogin.ps1</code> to send the Acctivity tray app's test login here.
      If the token has expired, open the Acctivity tray app (test) to refresh it, then run the helper again. Or paste an access token below.</p>
    <form className="demo-inline-form" onSubmit={send}>
      <label>Paste access token<input type="password" autoComplete="off" spellCheck={false} value={token} disabled={busy}
        onChange={e => setToken(e.target.value)} placeholder="eyJ…" /></label>
      <button disabled={busy || !token.trim()}>Send token</button>
      <button type="button" className="secondary" disabled={busy || !login || status === 'missing'} onClick={forget}>Forget login</button>
      <button type="button" className="secondary" disabled={busy} onClick={reload}>Check again</button>
    </form>
    {notice && <p role="status">{notice}</p>}
  </section>
}

function DemoPlan({ detail, saved, onSaved, onDirty, loginUsable }) {
  const [approvalId, setApprovalId] = useState(saved?.approval_id ?? detail.proposals[0]?.id ?? '')
  const [country, setCountry] = useState('')
  const [state, setState] = useState('')
  const [tailor, setTailor] = useState(false) // one paid OpenAI call, so off by default
  const [draft, setDraft] = useState(saved?.plan ?? null)
  const [errors, setErrors] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dirty = Boolean(saved) && JSON.stringify(draft) !== JSON.stringify(saved.plan)
  useEffect(() => { onDirty(dirty) }, [dirty, onDirty])
  async function generate(event) {
    event.preventDefault()
    if (saved && !window.confirm(`Replace the current plan${saved.edited ? ', including your edits,' : ''} with a new one from Brief #${approvalId}?${tailor ? ' Tailoring makes one paid OpenAI call.' : ''}`)) return
    setBusy(true); setError(''); setErrors([])
    try {
      onSaved(await api(`${firms}/${detail.id}/demo-plan`, { method: 'POST', body: {
        approval_id: Number(approvalId), tailor, ...(country && { country }), ...(country === 'AU' && state && { state }) } }))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function save() {
    setBusy(true); setError(''); setErrors([])
    try { onSaved(await api(`${firms}/${detail.id}/demo-plan`, { method: 'PUT', body: { plan: draft } })) }
    catch (e) {
      setError(e.message)
      if (Array.isArray(e.detail?.errors)) setErrors(e.detail.errors)
    } finally { setBusy(false) }
  }
  function editStaff(index, patch) {
    setDraft(plan => ({ ...plan, staff: plan.staff.map((member, i) => i === index ? { ...member, ...patch } : member) }))
  }
  const staffErrors = index => errors.filter(e => e.path === `staff[${index}]` || e.path.startsWith(`staff[${index}].`))
  const otherErrors = errors.filter(e => !/^staff\[\d+\]/.test(e.path))
  const approvals = detail.proposals
  return <section className="demo-card" aria-labelledby="demo-plan-title">
    <div className="demo-card-heading"><h3 id="demo-plan-title">Demo plan</h3>
      {saved && <span className="badge" data-status={saved.edited ? 'needs_review' : 'ready'}>{saved.edited ? 'Rep edited' : 'Generated'}</span>}</div>
    <p>Templates per area of law set the structure; rates come from the draft rate table. Review and edit before seeding: nothing is written to Acctivity until you seed.</p>
    <form className="demo-generate" onSubmit={generate}>
      <label>Approved brief<select value={approvalId} disabled={busy} onChange={e => setApprovalId(e.target.value)}>
        {approvals.map(p => <option key={p.id} value={p.id}>Brief #{p.id} · {p.snapshot.firm}</option>)}</select></label>
      <label>Country<select value={country} disabled={busy} onChange={e => setCountry(e.target.value)}>
        <option value="">From the research</option><option value="AU">Australia</option><option value="NZ">New Zealand</option></select></label>
      {country === 'AU' && <label>State<select value={state} disabled={busy} onChange={e => setState(e.target.value)}>
        <option value="">From the research</option>{AU_STATES.map(s => <option key={s}>{s}</option>)}</select></label>}
      <label className="choice"><input type="checkbox" checked={tailor} disabled={busy} onChange={e => setTailor(e.target.checked)} /> Tailor clients and matters with OpenAI (1 paid call)</label>
      <button disabled={busy || !approvalId}>{busy ? 'Working…' : saved ? 'Regenerate plan' : 'Generate plan'}</button>
    </form>
    {!loginUsable && <p className="muted">Generating reads the demo firm's Docs Core matter types with the rep login, so send a current login first.</p>}
    {error && <div role="alert"><strong>{error}</strong>
      {otherErrors.length > 0 && <ul className="demo-errors">{otherErrors.map((e, i) => <li key={i}><code>{e.path || 'plan'}</code> {e.message}</li>)}</ul>}</div>}
    {!saved ? <div className="empty-state">No demo plan yet. Choose an approved brief and generate one.</div>
      : <PlanReview saved={saved} plan={draft} editStaff={editStaff} staffErrors={staffErrors} busy={busy} />}
    {saved && <div className="review-actions"><div className="toolbar-actions"><button type="button" disabled={busy || !dirty} onClick={save}>Save plan edits</button>
      <button type="button" className="secondary" disabled={busy || !dirty} onClick={() => { setDraft(saved.plan); setErrors([]); setError('') }}>Discard edits</button></div>
      <span role="status">{dirty ? 'Unsaved plan edits — save before seeding.' : `Saved ${when(saved.updated_at)}.`}</span></div>}
  </section>
}

function PlanReview({ saved, plan, editStaff, staffErrors, busy }) {
  const currency = plan.region?.currency
  const staffName = Object.fromEntries(plan.staff.map(m => [m.slot, m.full_name]))
  const hours = plan.time_entries.reduce((n, e) => n + (Number(e.hours) || 0), 0)
  const unbilled = plan.time_entries.filter(e => !e.billed).reduce((n, e) => n + (Number(e.hours) || 0), 0)
  const stats = [
    ['Matters', plan.matters.length, `${plan.matters.filter(m => m.invoiced).length} already invoiced`],
    ['Time entries', plan.time_entries.length, `${hours.toFixed(1)} h · ${hours ? Math.round(unbilled / hours * 100) : 0}% unbilled`],
    ['Fixed fees', plan.fees.length, money(plan.fees.reduce((n, f) => n + (Number(f.amount) || 0), 0), currency)],
    ['Disbursements', plan.disbursements.length, money(plan.disbursements.reduce((n, d) => n + (Number(d.amount) || 0), 0), currency)],
    ['Invoices', plan.invoices.length, plan.invoices.map(i => i.matter).join(', ') || 'None'],
  ]
  return <>
    <p className="coverage">Plan for {plan.firm?.name} · {plan.region?.key} · {currency} · {plan.time_entries.length ? `time up to ${plan.today}` : `dated ${plan.today}`} · Brief #{saved.approval_id}{plan.tailored ? ' · Tailored by OpenAI' : ''}</p>
    {plan.flags?.length > 0 && <div className="demo-flags"><strong>Check before seeding ({plan.flags.length})</strong>
      <ul>{plan.flags.map((flag, i) => <li key={i}>{flag.message}</li>)}</ul></div>}
    <div className="demo-stats">{stats.map(([label, value, note]) => <div key={label}><small>{label}</small><strong>{value}</strong><span>{note}</span></div>)}</div>
    <div className="section-title"><h3>Staff and rates</h3><small>{plan.rates_note} Names and titles only; slot N is the Nth staff account in the demo firm.</small></div>
    <div className="staff-table"><table className="demo-staff"><thead><tr><th>Slot</th><th>Name</th><th>Title</th><th>Seniority</th><th>Hourly rate ({currency})</th><th>Areas · source</th></tr></thead>
      <tbody>{plan.staff.map((member, i) => {
        const problems = staffErrors(i)
        return <tr key={member.slot} className={problems.length ? 'has-error' : undefined}><td>{member.slot}</td>
          <td><input aria-label={`Staff ${member.slot} name`} value={member.full_name || ''} disabled={busy} maxLength={50} onChange={e => {
            const [first = '', ...rest] = e.target.value.trim().split(/\s+/)
            editStaff(i, { full_name: e.target.value, first_name: first, last_name: rest.join(' ') })
          }} /></td>
          <td><input aria-label={`Staff ${member.slot} title`} value={member.title || ''} disabled={busy} maxLength={80} onChange={e => editStaff(i, { title: e.target.value })} /></td>
          <td>{seniorityLabels[member.seniority] || member.seniority}</td>
          <td><input aria-label={`Staff ${member.slot} hourly rate`} type="number" min={50} max={2000} step={5} value={member.rate ?? ''} disabled={busy}
            aria-invalid={problems.some(p => p.path.endsWith('.rate')) || undefined}
            onChange={e => editStaff(i, { rate: e.target.value === '' ? null : Number(e.target.value) })} />
            {problems.map((p, n) => <small key={n} className="field-error">{p.message}</small>)}</td>
          <td>{(member.areas || []).join(', ') || '—'}<br /><small>{member.source}</small></td></tr>
      })}</tbody></table></div>
    <div className="section-title"><h3>Matter mix</h3><small>Weighted by the firm's practice areas (1 + people linked to each area).</small></div>
    <div className="staff-table"><table><thead><tr><th>Template</th><th>Weight</th><th>Matters</th><th>Practice areas</th></tr></thead>
      <tbody>{plan.mix.map(m => <tr key={m.template}><td>{m.label}</td><td>{m.weight}</td><td>{m.matters ?? plan.matters.filter(x => x.template === m.template).length}</td>
        <td>{m.practice_areas?.join(', ') || '—'}</td></tr>)}</tbody></table></div>
    <details className="record-category"><summary><h3 className="category-heading">Matters <span className="badge">{plan.matters.length}</span></h3></summary>
      <div className="staff-table"><table><thead><tr><th>Key</th><th>Matter type</th><th>Client</th><th>Description</th><th>Responsible</th><th>Opened</th></tr></thead>
        <tbody>{plan.matters.map(m => <tr key={m.key}><td>{m.key}{m.invoiced && <> <span className="badge">Invoiced</span></>}</td>
          <td>{[m.matter_type?.folder, m.matter_type?.name].filter(Boolean).join(' › ')}</td><td>{m.client?.name}</td><td>{m.description}</td>
          <td>{staffName[m.responsible] || `Slot ${m.responsible}`}</td><td>{m.opened_on}</td></tr>)}</tbody></table></div></details>
  </>
}

function SeedPanel({ firmId, detail, plan, planDirty, loginUsable }) {
  const [fetched, setRun] = useState(undefined) // undefined loading · null never seeded
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState('')
  const [confirmName, setConfirmName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [check, setCheck] = useState(null)
  // The live stream (Prospects' EventSource) carries the latest run as `demo_seed`; plain firm reads do not.
  // Show whichever of the stream and our own reads is newer.
  const run = newerRun(fetched, 'demo_seed' in detail ? detail.demo_seed : undefined)
  const active = ACTIVE.includes(run?.status)
  const loadRun = useCallback(() => api(`${firms}/${firmId}/demo-seed`)
    .then(setRun, e => { if (e.status === 404) setRun(null); else setError(e.message) }), [firmId])
  useEffect(() => {
    api(`${firms}/${firmId}/demo-seed`).then(setRun, e => { if (e.status === 404) setRun(null); else setError(e.message) })
  }, [firmId])
  // Fallback while a run is active, in case the stream is reconnecting.
  useEffect(() => {
    if (!active) return
    const timer = setInterval(loadRun, 3000)
    return () => clearInterval(timer)
  }, [active, loadRun])
  // The preview (what a reseed would wipe) changes when a run finishes.
  useEffect(() => {
    if (!plan || active) return
    api(`${firms}/${firmId}/demo-reseed`)
      .then(result => { setPreview(result); setPreviewError('') }, e => { setPreview(null); setPreviewError(e.message) })
  }, [plan, active, firmId])

  async function start(path, body) {
    setBusy(true); setError('')
    try {
      const result = await api(`${firms}/${firmId}/${path}`, { method: 'POST', body })
      if (result?.steps) setRun(result); else loadRun()
      setConfirmName('')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function runCheck() {
    setBusy(true); setCheck(null)
    try { setCheck(await api(`${demo}/firms/${encodeURIComponent(preview.demo_firm_id)}/check`)) }
    catch (e) { setCheck({ refused: e.message, writable: false }) } finally { setBusy(false) }
  }
  const counts = preview?.counts || {}
  const items = Object.values(counts).reduce((n, v) => n + (Number(v) || 0), 0)
  const firmHasData = items > 0 || preview?.invitees?.length > 0
  const reseed = Boolean(run) || firmHasData
  const blocked = !plan ? 'Generate a demo plan first.' : planDirty ? 'Save or discard the plan edits first.'
    : !loginUsable ? 'Send a current rep login first.' : active ? 'A run is in progress.' : ''
  return <section className="demo-card" aria-labelledby="demo-seed-title">
    <div className="demo-card-heading"><h3 id="demo-seed-title">Seed the demo firm</h3>
      {run && <span className="badge" data-status={runStatus[run.status]}>{run.kind === 'reseed' ? 'Reseed' : 'Seed'} · {runLabels[run.status] || run.status}</span>}</div>
    {error && <p role="alert">{error}</p>}
    {run === undefined ? <p className="muted">Loading the latest run…</p> : run && <SeedProgress run={run} />}
    {run?.status === 'failed' && <div className="review-actions"><div><strong>Resume the failed run</strong>
      <p>Picks up where it stopped; items already created are skipped.</p></div>
      <button type="button" disabled={busy || Boolean(blocked)} onClick={() => start('demo-seed', { fresh: false })}>Resume run</button></div>}
    {run?.status === 'done' && <p className="coverage">Seeded {when(run.finished_at)}.{appUrl && <> <a href={appUrl} target="_blank" rel="noreferrer">Open Acctivity (test) ↗</a></>}</p>}
    {!reseed && run !== undefined && <div className="approval-bar"><div><strong>Seed {preview?.firm_name || 'the demo firm'}</strong>
      <p>Renames the firm and the 6 staff accounts, sets rates, then creates the plan's matters, contacts, time, fees and invoices.</p></div>
      <button type="button" disabled={busy || Boolean(blocked)} onClick={() => start('demo-seed', { fresh: false })}>Seed demo firm</button></div>}
    {reseed && run !== undefined && !active && <div className="demo-danger">
      <h3>Reseed: wipe and start again</h3>
      {preview ? <>
        <p>Everything in <strong>{preview.firm_name}</strong> is deleted{items > 0 && <>: {Object.entries(counts).filter(([, n]) => n > 0).map(([key, n]) => `${n} ${countLabels[key] || key}`).join(', ')}</>}, including what attendees created.
          The owner and the 6 staff accounts are kept and renamed for this prospect.</p>
        {preview.invitees?.length > 0 ? <><p><strong>These people lose access ({preview.invitees.length}):</strong></p>
          <ul>{preview.invitees.map(person => <li key={person.email}>{person.name && `${person.name} · `}{person.email} · {person.kind === 'invitation' ? 'pending invitation' : 'user'}</li>)}</ul></>
          : <p>No invited users will lose access.</p>}
        <label>Type the demo firm name <strong>{preview.firm_name}</strong> to confirm
          <input value={confirmName} disabled={busy} autoComplete="off" onChange={e => setConfirmName(e.target.value)} /></label>
        <div className="toolbar-actions">
          <button type="button" className="danger" disabled={busy || Boolean(blocked) || confirmName !== preview.firm_name}
            onClick={() => start('demo-reseed', { confirm_firm_name: confirmName })}>Wipe and reseed</button>
          <button type="button" className="secondary" disabled={busy} onClick={runCheck}>Check guards (read-only)</button></div>
        {check && <p className="muted">{check.writable ? 'Guards pass: writes to this firm are allowed.' : `Writes refused: ${check.refused}`}</p>}
      </> : <p>{previewError ? `The reseed preview is unavailable: ${previewError}` : 'Loading what a reseed would remove…'}</p>}
    </div>}
    {blocked && !active && plan && <p className="muted">{blocked}</p>}
  </section>
}

// The newer of two run snapshots (undefined = not known yet, null = no run).
function newerRun(a, b) {
  if (a === undefined) return b
  if (!b) return a ?? b
  if (!a) return b
  return b.id > a.id || (b.id === a.id && (b.updated_at || '') >= (a.updated_at || '')) ? b : a
}

function SeedProgress({ run }) {
  const active = ACTIVE.includes(run.status)
  return <div className="demo-run" aria-live="polite">
    <p className={active ? 'coverage live-timer' : 'coverage'}>Run #{run.id} · {runLabels[run.status] || run.status}
      {run.step && ` · ${run.steps.find(s => s.step === run.step)?.label || run.step}`} · updated {when(run.updated_at)}</p>
    {run.error && <p role="alert">{run.error}</p>}
    {run.status === 'done' ? <details><summary>All {run.steps.length} steps done</summary><Steps run={run} active={active} /></details>
      : <Steps run={run} active={active} />}
  </div>
}

function Steps({ run, active }) {
  return <ol className="demo-steps">{run.steps.map(step => {
      const current = step.step === run.step
      const state = step.failed > 0 || (current && run.status === 'failed') ? 'failed'
        : step.total > 0 && step.done >= step.total ? 'done' : current && active ? 'running' : 'waiting'
      return <li key={step.step} data-state={state}>
        <div><strong>{step.label}</strong><span>{step.total ? `${step.done} of ${step.total}` : 'Nothing to do'}
          {step.failed > 0 && ` · ${step.failed} failed`}{state === 'running' && ' · in progress'}{state === 'failed' && current && ' · stopped here'}</span></div>
        <progress value={step.total ? step.done : 0} max={step.total || 1} aria-label={`${step.label}: ${step.done} of ${step.total}`} />
      </li>
    })}</ol>
}
