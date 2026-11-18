# FastAPI server

## Live prospect directory

With the API and Vite running, visit `http://127.0.0.1:5173/prospects`.
`POST /api/v1/firms` saves a website and optional display name. `GET /api/v1/firms`
lists firms; append `/{id}` to read the firm's research records. `POST /api/v1/firms/{id}/research`
with JSON `{}` crawls the firm's public website then starts a paid OpenAI
extraction; `{"reuse_pages": true}` re-extracts from the saved pages without crawling
again (still a paid call). Poll the firm detail until `status` is no longer `researching`.
The crawler respects robots.txt, follows only the firm's domain (and www),
prioritises staff/about/contact/service pages, and stops at 12 pages, 240,000
text characters or 150 seconds. No login, JavaScript rendering or PDF parsing, so
sites behind a JavaScript bot challenge (e.g. Cloudflare) return no pages.
Coverage and skipped pages are shown in the UI. This is bounded research, not a
guarantee that every lawyer or every page on a site has been found.

### Research data (SQLite)

Research is stored as normalised records, each citing the saved page and a verbatim
passage verified against that page's text:

| Table | Holds |
|---|---|
| `pages` | every crawled page's URL, title and text (re-extraction reads these) |
| `practice_areas` | one row per top-level area: name, description, `services` list |
| `people` | name, published position, biography, qualifications, public work email/phone |
| `person_practice_areas` | links people to the firm's practice areas |
| `offices` | name, address, phone, email |
| `facts` | `overview`, `history`, `credential`, `clients` or `approach`, with title and detail |
| `brand_assets` | the firm's public website logos (PNG/JPEG/WebP, same domain, up to 1.5 MB) |

Positions and contact details are dropped unless they appear on the cited page.
Each record's `original` column keeps the extracted values once a rep edits it.
Pages saved by earlier versions (JSON on the firm) move to `pages` at startup.
SVG, external CDN logos and browser screenshots are not collected. Review permission
before using a firm's branding outside a private demo.

After approval, `GET /api/v1/firms/{id}/proposals/{approval_id}/demo-export`
downloads a JSON staging payload with only the approved records, plus links to
current saved logos. This is our export schema, not yet an Acctivity import
contract. It does not create live tenants/users or send invites. The deployment
API, required fields and safe demo-account policy must be agreed before
connecting a live Acctivity system.

## Research and Create outputs tabs

- **Research:** edit the firm display name and each record (practice areas, people
  and their linked practice areas, offices, facts), and choose a preferred saved logo.
  Saving uses `PUT /api/v1/firms/{id}/brief` with the latest `brief_token` and every
  record. Stale edits return `409`. Source links and evidence stay read-only;
  corrections are labelled as rep edits. Research reruns replace current records,
  not existing approvals.
- **Approve & create outputs:** `POST /api/v1/firms/{id}/approvals` with the selected
  record ids per table copies them into an immutable snapshot. Later edits do not
  change it. No paid call is made at approval.
- **Create outputs:** choose an approval, PowerPoint (`pptx`), Word (`docx`),
  welcome PDF (`pdf`), optional saved logo and sales instructions. One paid OpenAI
  call writes short copy (titles, summary, proposed opportunities, pilot, next steps,
  demo agenda) from the approved records; `documents.py` then builds each file from
  our Acctivity brand templates. Each file is validated and saved in SQLite, and reports its
  stage and timings; the streamed copy call shows a live draft.

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
formats only. Jobs survive page refreshes, but this local single-process
prototype does not resume interrupted work after a server restart;
already saved downloads remain available.

### Live streaming and document progress

The browser opens `GET /api/v1/firms/{id}/events` as an SSE connection. It receives
current firm snapshots as persisted progress changes and heartbeat events when
idle, with periodic polling retained as a fallback. Reconnecting returns the
latest saved state; leaving the page does not cancel paid work.

OpenAI Responses calls for web search, structured extraction and new output runs
use the Python SDK's streaming interface. Search requests explicitly enable
`tools=[{"type":"web_search"}]`. Draft search text is shown before completion and
is not an approved finding; final cited results become available per source even
while other source tasks are running.

Each output file tracks its own stage, timestamps and a bounded preview of the
streamed copy call (the structured copy as it arrives). The UI uses an
indeterminate activity indicator with actual stages and elapsed/last-activity
times. File downloads appear as each file is saved, without waiting for the
others. Older runs cannot gain event history retroactively.

The shared model defaults to `gpt-6.1-sol`. A selected logo is copied into the job
so later research cannot change that run's branding; it is used by our templates
only and never sent to OpenAI. No live tenant/user creation, email sending or
invitations occur. Keep this unauthenticated prototype on loopback, not a public network.

### Acctivity brand kit and templates

`brand/BRAND.md` is the brand guide; `documents.py` implements it. The templates use
the Acctivity logo and mark, the palette (Slate Grey `#3D443D` and Steel Blue `#4396BA`,
with Light Pink, Gold and Light Grey accents), Lora headings and Inter body text, and
the watercolour background and illustrations from `brand/`.

- **PPTX** (16:9): cover, firm at a glance, what stands out, practice areas, people,
  proposed opportunities and pilot, next steps, sources.
- **DOCX** (A4): the same proposal as an editable document with tables.
- **PDF** (A4): a welcome guide for a prospective demo, with the brand fonts embedded.

python-pptx and python-docx cannot embed fonts, so Word and PowerPoint need Lora and
Inter installed (both free Google Fonts) to show the exact typography; otherwise they
fall back to a serif for headings and a sans serif for body text. Inter and Lora are
SIL Open Font License fonts (`brand/fonts/*-OFL.txt`). Images are 1600px JPEG copies
of the brand pack. The older `POST .../proposals/{id}/build` endpoint uses the same
PowerPoint template.

### Focused checks

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_prospects test_firm_research test_outputs test_documents test_apollo test_find_firms test_apollo_contacts test_apollo_findings test_web_research test_model_stream
```
Use `OPENAI_RESEARCH_MODEL` to override the research model.
The default model for research and file generation is `gpt-6.1-sol`; `OPENAI_MODEL`
overrides the shared default, with `OPENAI_RESEARCH_MODEL` and `OPENAI_DECK_MODEL`
(the copy call) overriding individual operations. API model access depends on the
OpenAI account.

### Parallel research sources

Research now runs the website crawl/extraction alongside two optional OpenAI
`web_search` requests: wider public firm research and public reputation research.
`POST /api/v1/firms/{id}/research` accepts
`{"web_search":true,"reputation":true}` (both default on). Disable either in the UI
or request body to avoid that source's paid model/tool calls. Re-extraction
(`{"reuse_pages": true}`) runs the website source only, from the saved pages. There are up to
three simultaneous source tasks, not three serial searches. Each source gets
its own status, count and error in `research_meta.sources`; one failed search
does not discard a successful crawl. Partial coverage is marked `needs_review`.

The website source fills the research records (and is the only one that saves logos).
Search observations are saved in the `web_findings` table with `kind` `web_search` or
`reputation_search`, their citations and retrieval time; a search that runs replaces that
kind's earlier findings, while a failed or disabled search keeps them. Approve them by id
(`"web_findings": [ids]`); approved ones go into the snapshot's `web` list, appear in the
Word proposal under "From the wider web" or "Public reputation (unverified)", and their
text (never personal contact data) informs the copy call.
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
Revealed people are saved in the `apollo_contacts` table (name, Apollo title, `apollo_email`,
`email_status`, `linkedin_url`, `match_confidence`, reveal date), apart from website research,
so re-running research never drops them. Each contact's `person_id` is the website person with
the same name (case-insensitive), or null. Contacts revealed by earlier versions into the old
`staff` table are copied over at startup.

Apollo findings: the firm detail's `apollo_findings` turns the stored Apollo data into
reviewable findings (`apollo_findings.py`): one per industry, keyword and technology, and one
per revealed contact (email, LinkedIn). Each has a stable `key` (e.g. `apollo:tech:cloudflare`,
`apollo:contact:<apollo id>`), `source: "Apollo"`, `retrieved_at` (enrich or reveal date), a
"Source: Apollo, retrieved …" source label and no evidence passage. Approve with
`{"brief_token", "people": [ids], ..., "apollo_findings": [keys]}`; only the selected ones go
into the snapshot's `apollo` list, so the demo export and outputs see nothing that was unticked.
Outputs label them as Apollo-reported: approved contacts join the people lists (the Word
proposal shows their email and LinkedIn), and industries, keywords and technology appear as
"Reported by Apollo". Revealed emails and LinkedIn URLs are never sent to OpenAI.
`brief_token` also covers the Apollo findings, so a new enrich or reveal asks for a fresh review.

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
