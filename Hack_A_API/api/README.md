# FastAPI server

## Live prospect directory

With the API and Vite running, visit `http://127.0.0.1:5173/prospects`.
`POST /api/v1/firms` saves a website and optional display name. `GET /api/v1/firms`
lists firms; append `/{id}` to read the saved brief. `POST /api/v1/firms/{id}/research`
with JSON `{}` crawls the firm's public website then starts a paid OpenAI
extraction; poll the firm detail until `status` is no longer `researching`.
The crawler respects robots.txt, follows only the firm's domain (and www),
prioritises staff/about/contact/service pages, and stops at 12 pages, 240,000
text characters or 150 seconds. No login, JavaScript rendering or PDF parsing.
Coverage and skipped pages are shown in the UI. Each extracted finding includes
a source page and a verbatim supporting passage verified against fetched text.
Profiles and findings are stored in SQLite. This is bounded research, not a
guarantee that every lawyer or every page on a site has been found.

Named staff are stored in the `staff` table, linked to the firm: name, published
position, public work contacts, biography, qualifications, practice areas and
supporting source. Public website logos (PNG/JPEG/WebP, same domain, up to 1.5 MB
each) are saved in `brand_assets`, not hotlinked. SVG, external CDN logos and
browser screenshots are not collected in this slice. Robots rules apply.
Review permission before using branding outside a private demo.

After approval, `GET /api/v1/firms/{id}/proposals/{approval_id}/demo-export`
downloads a JSON staging payload with only the approved findings and staff,
plus links to current saved logos. This is our export schema, not yet an
Acctivity import contract. It does not create live tenants/users or send invites.
The deployment API, required fields and safe demo-account policy must be agreed
before connecting a live Acctivity system.

## Research and Create outputs tabs

- **Research:** edit the firm display name, individual findings and structured
  staff profiles, and choose a preferred saved logo. Saving uses
  `PUT /api/v1/firms/{id}/brief` with the latest `brief_token` and the complete
  indexed findings list. Stale edits return `409`. Original source links and
  evidence remain read-only; corrections are labelled as rep edits. Research
  reruns replace current research, not existing approvals.
- **Approve & create outputs:** selected findings are copied into an immutable
  approval. Later edits do not change that snapshot. No paid call is made at
  approval.
- **Create outputs:** choose an approval, PowerPoint (`pptx`), Word (`docx`),
  welcome PDF (`pdf`), optional saved logo and sales instructions. A single
  OpenAI container creates the requested formats. Each file is validated and
  saved independently in SQLite before container deletion.

`POST /api/v1/firms/{id}/outputs` accepts:

```json
{
  "approval_id": 1,
  "formats": ["pptx", "docx", "pdf"],
  "instructions": "Partner-level proposal for a proposed family law pilot.",
  "logo_id": null,
  "request_key": "fe829ab4-abcc-4cd7-97ba-9a7f5484b9e2"
}
```

It returns `202` with the job ID and status. Reusing the same request key and
payload returns the existing job, without starting another paid run. A changed
payload requires a new key. Poll firm detail for `outputs`: each run has a stage,
status and per-file results. Ready files are downloaded from
`GET /api/v1/firms/{id}/outputs/{job_id}/files/{file_id}`.
The UI shows partial failures and lets the rep prepare a new run for failed
formats only. Container jobs survive page refreshes, but this local single-process
prototype does not resume interrupted provider work after a server restart;
already saved downloads remain available.

The shared model defaults to `gpt-6.1-sol`. A selected logo is copied into the job
so later research cannot change that run's branding. No live tenant/user creation,
email sending or invitations occur. Keep this unauthenticated prototype on
loopback, not a public network.

### Focused checks

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_prospects test_firm_research test_outputs test_web_research
```
Use `OPENAI_RESEARCH_MODEL` to override the research model.
The default model for research and file generation is `gpt-6.1-sol`; `OPENAI_MODEL`
overrides the shared default, with `OPENAI_RESEARCH_MODEL` and `OPENAI_DECK_MODEL`
overriding individual operations. API model access depends on the OpenAI account.

### Parallel research sources

Research now runs the website crawl/extraction alongside two optional OpenAI
`web_search` requests: wider public firm research and public reputation research.
`POST /api/v1/firms/{id}/research` accepts
`{"web_search":true,"reputation":true}` (both default on). Disable either in the UI
or request body to avoid that source's paid model/tool calls. There are up to
three simultaneous source tasks, not three serial searches. Each source gets
its own status, count and error in `research_meta.sources`; one failed search
does not discard a successful crawl. Partial coverage is marked `needs_review`.

Findings carry `source_kind` (`website_crawl`, `web_search` or
`reputation_search`) and a retrieval timestamp. Only the first-party extraction
populates staff and logos. Search findings remain separately grouped and cited.
Reputation observations are not a verified Google review sample and are unchecked
for approval by default. They must not be presented as verified reviews, ratings,
testimonials or established firm facts in output material. No Google Maps scraping
or Google Places API is implemented. Twelve observations are an upper bound, not
a promise to retrieve twelve reviews.

Research progress records start, last-progress and completion timestamps. The
website source reports page reading, structured extraction and logo retrieval
as separate stages. The UI shows elapsed time and when its last API poll succeeded.
API requests from the browser stop waiting after 15 seconds and offer reconnection;
that does not cancel a background provider job. OpenAI network timeouts have
`provider_timeout` errors; malformed structured model output is reported separately.
The website's OpenAI call has a 180-second network timeout; search calls use 150
seconds. These are network timeouts, not guarantees of a total end-to-end deadline.

Review the findings, deselect anything unsuitable, then approve. Approval saves a
snapshot without a paid request. **Generate PowerPoint** builds from that snapshot
in a private OpenAI Code Interpreter container and saves the validated `.pptx`
in local SQLite before offering a download. A failed build can be retried without
re-running research. Set `OPENAI_DECK_MODEL` to override the build model.

Apollo is optional. Without `APOLLO_API_KEY` in `.env` everything above works and the
firm detail reports `apollo_configured: false` ("Apollo not configured" in the UI).
With a key, `POST /api/v1/firms/{id}/apollo/enrich` spends **1 Apollo credit** to store
the firm's Apollo organization (headcount, industries, tech stack) in `firms.apollo_org`;
enriching again requires `{"refresh": true}`. `apollo.py` is the client (no retries; the
key is sent only to Apollo). Tests mock HTTP and never spend credits.

Contacts: `GET /api/v1/firms/{id}/apollo/candidates?titles=Partner,CFO` (free) searches
Apollo people at the firm by title, or by `seniorities=` if given. A title-only search that
finds nobody is retried by seniority (owner, founder, c_suite, partner, head, director,
manager); `search`/`fallback` in the response say which search produced the list.
`POST /api/v1/firms/{id}/apollo/reveal {"ids": [...]}` spends **1 credit per matched person**
(work email only, no phones), in batches of 10, and refuses ids already stored with an email.
Revealed people merge into `staff` by name (case-insensitive): `source` is `website`,
`apollo` or `both`; Apollo fills `apollo_email`, `email_status`, `linkedin_url`,
`match_confidence`. Re-running research replaces only website-only staff rows.

Apollo findings: the firm detail's `apollo_findings` turns the stored Apollo data into
reviewable findings (`apollo_findings.py`): one per industry, keyword and technology, and one
per revealed contact (email, LinkedIn). Each has a stable `key` (e.g. `apollo:tech:cloudflare`,
`apollo:contact:<apollo id>`), `source: "Apollo"`, `retrieved_at` (enrich or reveal date), a
"Source: Apollo, retrieved …" source label and no evidence passage. Approve with
`{"brief_token", "findings": [website indices], "apollo_findings": [keys]}`; only the selected
ones are appended to the snapshot, so the demo export and deck see nothing that was unticked.
`brief_token` also covers the Apollo findings (a new enrich or reveal asks for a fresh review);
without Apollo data it is the brief digest, as before.

This single-process local prototype uses the ignored `prospects.sqlite3` database
next to the API. A restart marks interrupted searches failed, without automatic
paid retries. Keep both dev servers on loopback; there is no user authentication yet.

Run these PowerShell commands from this folder.

## Initial setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## OpenAI configuration

Put your OpenAI API key in `.env`:

```dotenv
OPENAI_API_KEY=your-api-key
```

The server loads `.env` at startup. `.env` is ignored by Git; `.env.example`
shows the required variable without a secret. Set `OPENAI_MODEL` to override the
default model, `gpt-6.1-sol`.

## Start the development server

```powershell
.\.venv\Scripts\python.exe main.py
```

The explicit Python path uses this server's virtual environment without requiring
activation. The server runs on port 9001 with auto-reload. Stop it with Ctrl+C.

- API: http://127.0.0.1:9001/
- Health check: http://127.0.0.1:9001/health
- OpenAI connection test: http://127.0.0.1:9001/openai/test
- Interactive documentation: http://127.0.0.1:9001/docs
- OpenAPI schema: http://127.0.0.1:9001/openapi.json
