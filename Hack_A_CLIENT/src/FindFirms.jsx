import { useEffect, useState } from 'react'
import './FindFirms.css'

const searchUrl = '/api/v1/apollo/firm-search'
const fields = [
  ['keywords', 'Keyword tags'], ['naics', 'NAICS codes (checked on results)'],
  ['locations', 'Locations'], ['employees', 'Employee ranges (min,max; small and medium firms only, max 199)'],
]

async function call(url, options) {
  const res = await fetch(url, options)
  if (!res.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Cannot reach the API. Check the FastAPI server on port 9001.')
  }
  const result = await res.json()
  if (!res.ok) {
    const detail = Array.isArray(result.detail) ? result.detail.map(d => d.msg).join(' ') : result.detail
    throw new Error(detail || `Request failed (${res.status}).`)
  }
  return result
}

const split = text => text.split(';').map(s => s.trim()).filter(Boolean)
const tickedByDefault = row => !!row.domain && !row.already_added && !row.likely_recruiter && row.naics_match !== false

// Apollo "Find firms" (#6): one search page per click (1 credit), results reviewed here and never stored.
// Ticked firms go through the normal intake (status "new"); research is not started.
export default function FindFirms({ onAdded }) {
  const [info, setInfo] = useState(null)
  const [form, setForm] = useState(null)
  const [last, setLast] = useState(null)
  const [rows, setRows] = useState([])
  const [ticked, setTicked] = useState(new Set())
  const [spent, setSpent] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    call(searchUrl).then(data => {
      setInfo(data)
      setForm(Object.fromEntries(fields.map(([key]) => [key, data.defaults[key].join('; ')])))
    }).catch(e => setMessage(e.message))
  }, [])

  async function run(filters, page) {
    setBusy(true); setMessage('')
    try {
      const data = await call(searchUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...filters, page }) })
      setSpent(n => n + data.credits)
      setLast({ filters, page: data.page, total_pages: data.total_pages, total_entries: data.total_entries })
      const fresh = page === 1 ? [] : rows
      const known = new Set(fresh.map(r => r.domain || r.apollo_id))
      const added = data.results.filter(r => !known.has(r.domain || r.apollo_id))
      setRows([...fresh, ...added])
      setTicked(prev => new Set([...(page === 1 ? [] : prev), ...added.filter(tickedByDefault).map(r => r.domain)]))
    } catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }

  function search(event) {
    event.preventDefault()
    run(Object.fromEntries(fields.map(([key]) => [key, split(form[key])])), 1)
  }

  async function addTicked() {
    setBusy(true); setMessage('')
    const done = [], failed = []
    for (const row of rows.filter(r => ticked.has(r.domain))) {
      try {
        await call('/api/v1/firms', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ website: row.domain, display_name: row.name }) })
        done.push(row.domain)
      } catch (e) {
        if (e.message.includes('already in the directory')) done.push(row.domain)
        else failed.push(`${row.domain}: ${e.message}`)
      }
    }
    setRows(prev => prev.map(r => done.includes(r.domain) ? { ...r, already_added: true } : r))
    setTicked(prev => new Set([...prev].filter(d => !done.includes(d))))
    setMessage(`${done.length} firm${done.length === 1 ? '' : 's'} added as "Not researched".` + (failed.length ? ` Not added: ${failed.join('; ')}` : ''))
    setBusy(false)
    onAdded()
  }

  function toggle(domain, on) {
    setTicked(prev => { const next = new Set(prev); if (on) next.add(domain); else next.delete(domain); return next })
  }

  if (!info) return message ? <section className="find-firms"><h2>Find firms</h2><p role="alert">{message}</p></section> : null
  if (!info.configured) return <section className="find-firms"><h2>Find firms</h2>
    <p>Apollo not configured. Add <code>APOLLO_API_KEY</code> to the server <code>.env</code> to search Apollo for firms.</p></section>

  const more = last && (!last.total_pages || last.page < last.total_pages)
  return <section className="find-firms"><h2>Find firms (Apollo)</h2>
    <p>Each search returns one page of up to 100 organizations and spends <strong>1 Apollo credit</strong>. Separate values with “;”.
      Results are not saved; tick the firms to add to the directory. Research is not started.</p>
    <form onSubmit={search}>
      {fields.map(([key, label]) => <label key={key}>{label}<input value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} /></label>)}
      <button disabled={busy}>Search (1 credit)</button>
      {more && <button type="button" disabled={busy} onClick={() => run(last.filters, last.page + 1)}>Next page (1 credit)</button>}
    </form>
    {message && <p role="alert">{message}</p>}
    {spent > 0 && <p>Credits spent this session: {spent}. {last && <>Page {last.page}{last.total_pages ? ` of ${last.total_pages}` : ''} · about {last.total_entries?.toLocaleString() ?? '?'} matches.</>}</p>}
    {rows.length > 0 && <>
      <p className="find-firms-note">Apollo search results have no {info.missing_fields.join(', ')}. Matches include legal recruiters, so check each name and website.
        Rows flagged “likely recruiter” or “NAICS mismatch” start unticked.</p>
      <div className="find-firms-table"><table>
        <thead><tr><th>Add</th><th>Organization</th><th>Website</th><th>NAICS</th><th>Founded</th><th>Revenue</th><th>Phone</th><th>Flags</th></tr></thead>
        <tbody>{rows.map(row => {
          const flags = [row.already_added && 'already added', row.likely_recruiter && 'likely recruiter',
            row.naics_match === false && 'NAICS mismatch', !row.domain && 'no website'].filter(Boolean)
          return <tr key={row.domain || row.apollo_id} className={row.already_added ? 'muted' : row.likely_recruiter ? 'doubtful' : ''}>
            <td><input type="checkbox" aria-label={`Add ${row.name}`} disabled={!row.domain || row.already_added || busy}
              checked={ticked.has(row.domain)} onChange={e => toggle(row.domain, e.target.checked)} /></td>
            <td>{row.name}{row.linkedin_url && <> <a href={row.linkedin_url} target="_blank" rel="noreferrer">LinkedIn ↗</a></>}</td>
            <td>{row.domain ? <a href={row.website_url || `https://${row.domain}`} target="_blank" rel="noreferrer">{row.domain}</a> : 'Not listed'}</td>
            <td>{row.naics_codes.join(', ') || 'Not listed'}</td>
            <td>{row.founded_year ?? 'Not listed'}</td>
            <td>{row.revenue ?? 'Not listed'}</td>
            <td>{row.phone ?? 'Not listed'}</td>
            <td>{flags.join(' · ')}</td>
          </tr>
        })}</tbody></table></div>
      <button disabled={busy || !ticked.size} onClick={addTicked}>Add {ticked.size} ticked firm{ticked.size === 1 ? '' : 's'} to directory</button>
    </>}
  </section>
}
