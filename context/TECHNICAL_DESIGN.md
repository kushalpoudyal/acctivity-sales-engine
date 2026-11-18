# Instant Sales Engine: API and file generation design

**Working draft, 18 Nov 2026.** Product scope: [requirements](./REQUIREMENTS.md).

## Implemented prototype status

The original design below is retained as a proposal; the current prototype differs:

- The working Research and Create outputs tabs live at `/prospects?firm={id}`.
- Storage is local SQLite, selected for the prototype, rather than PostgreSQL.
- Research uses a 12-page first-party crawl and structured extraction alongside
  optional OpenAI web and public reputation searches, with separate statuses,
  timings and citations. Crawl results are normalised into `pages`,
  `practice_areas`, `people` (+ `person_practice_areas`), `offices` and `facts`
  tables; every record cites its page and a verified verbatim passage. Search
  observations are cited `web_findings`; reputation ones are not verified Google
  reviews and are excluded from approval by default. Permitted same-domain logo
  images are saved, and saved pages can be re-extracted without crawling again.
- `PUT /api/v1/firms/{id}/brief` saves rep edits with a concurrency token.
  `POST /api/v1/firms/{id}/approvals` freezes the selected records.
- `POST /api/v1/firms/{id}/outputs` accepts an approval, selected `pptx`/`docx`/`pdf`
  formats, optional saved logo, instructions and an idempotent `request_key`.
  Firm detail returns output-job progress and per-file download routes.
- One paid, streamed OpenAI call writes short copy from the approved records; our
  own brand templates (`documents.py`, rules in `Hack_A_API/api/brand/BRAND.md`)
  build each file. This replaced the Code Interpreter containers below so that
  branding is enforced in code. Validated downloads are persisted in SQLite; a
  failing format does not lose the others. Source and model streaming events are
  relayed through SSE snapshots, with per-file stages and timestamps.
- The default model is `gpt-6.1-sol`. A live CBD Law run generated all three
  formats; 76 research, output, Apollo and streaming tests and the client
  lint/build passed.
- Optional Apollo firm/contact enrichment and approval are implemented; revealed
  contacts live in `apollo_contacts`, so website research preserves them. CRM,
  live tenant provisioning, authentication and a resumable external worker remain
  unimplemented. Keep this prototype on loopback.

The API and client READMEs contain the current operational contract.

## Original proposed architecture

## One firm, two independent jobs

1. **Research:** given a firm website, find evidence and save a draft brief. Apollo is an optional additional source, off by default.
2. **Review:** the rep edits or approves the brief. Approval freezes a revision: regenerating a deck tomorrow should not silently change its facts.
3. **Build:** given that revision and selected formats, create actual presentation files in an OpenAI Code Interpreter container. Download and validate each file before reporting it ready.

React calls FastAPI, not the providers. FastAPI records jobs in local PostgreSQL; a worker claims them and calls the providers. Keep file bytes in private application storage, outside Git and the frontend's public directory. PostgreSQL holds their metadata. A **demo workspace** (fictional firm, users and matters) belongs to the application, not the temporary OpenAI container.

## FastAPI contract (proposed `/api/v1` prefix)

| Method and route | Input | Result |
|---|---|---|
| `POST /firms` | `website`, optional `display_name` | `201` firm ID and normalised domain. For an existing domain, `409` with the existing firm ID. |
| `GET /firms` / `GET /firms/{id}` | Optional list search and pagination | Directory rows, or firm detail with current brief, runs and assets. Neither triggers paid work. |
| `POST /firms/{id}/research` | `{"use_apollo":false}` | `202` job ID and status URL; the website is read from the firm record. |
| `GET /jobs/{id}` | Job ID | State, stage, per-source or per-file results, and actionable errors. UI polls until terminal. |
| `PUT /firms/{id}/brief` | Base revision, edited findings and approval flag | New immutable revision; `409` if the rep edited a stale revision. |
| `POST /firms/{id}/builds` | `{"brief_revision":2,"formats":["pptx","pdf"]}` | `202` build job. Reject an unapproved revision (`409`) or unsupported/empty formats (`422`). |
| `GET /assets/{id}/download` | Application asset ID | Authorised download with filename and MIME type; never a sandbox path. |

Use an `Idempotency-Key` on job creation. Repeat submissions with the same key and payload return the original job; changed payloads require a new key. Polling, refreshing a page or downloading an asset must never start another search or build.

## Research worker: website first

Use OpenAI `POST /v1/responses` with the hosted `web_search` tool. Example request shape (the model is a configuration placeholder):

```json
{
  "model": "<web-search-capable model>",
  "tools": [{"type":"web_search","filters":{"allowed_domains":["firm.example"]}}],
  "tool_choice": "required",
  "include": ["web_search_call.action.sources"],
  "input": "Research the firm's practice areas, offices, public staff roles and founding date. Cite each fact. Leave unsupported facts unknown."
}
```

- Validate the website as a public HTTP(S) domain. Use the normalised domain for the search allow-list. A LinkedIn link published on a bio is a reference, not an instruction to scrape LinkedIn. Treat retrieved pages as evidence, never as instructions to call tools or reveal data.
- Verify that the response contains a search call. Parse message `url_citation` annotations, not just `output_text`. The list of consulted sources is useful for audit but does **not** prove any particular claim.
- Store each finding as `{field, value, source_ids, status}`; store sources as `{id, url, title, retrieved_at}`. Render clickable citations beside the findings. Keep rep-entered corrections labelled separately. No founding date means unknown, not an inferred firm age. A team page lists people; it does not necessarily establish total headcount.
- If no usable evidence comes back, mark the brief **needs review**, not ready to build. Keep conflicting evidence visible. Do not send unreviewed web content to the file builder.

**Optional Apollo branch:** when off, make zero Apollo calls. When on, enrich the organisation by domain and search for roles; only look up a work email after the rep selects a person and confirms. Store Apollo provenance separately. If unavailable, return a source warning and keep the website brief. No mobile lookup, email sending or CRM write is part of research. See [Apollo endpoints and access uncertainties](./APOLLO.md).

## Build worker: OpenAI containers

*Superseded in the prototype by template rendering (see the status above); kept for reference.*

| Stage | Provider operation | Completion check |
|---|---|---|
| Allocate | `POST /v1/containers` with a job-specific name. Use one explicit container per build so files cannot carry over between firms. | Save returned container ID on the job. |
| Generate | `POST /v1/responses` with `tools: [{"type":"code_interpreter","container":"<container_id>"}]` and `tool_choice: "required"`. Supply the approved brief, a supported demo scenario and requested formats. | Request a real `.pptx`, `.docx` or `.pdf` for each selected format, not a Markdown outline. Do not supply credentials, unnecessary contact data or arbitrary URLs to execute. |
| Collect | Read `container_file_citation` annotations for `container_id` and `file_id`. If necessary, list `GET /v1/containers/{container_id}/files` to reconcile expected files. | Match requested artifacts to the current job's container. Ignore intermediate files. A text response saying “done” is not a file. |
| Persist | Download `GET /v1/containers/{container_id}/files/{file_id}/content` immediately. Assign an application-owned storage name. | Verify size, file type and that the document opens; save byte count, checksum, format and brief revision. Only then mark that asset ready. |
| Release | Delete the container after collection or terminal failure. | Downloads use the saved copy. OpenAI documents expiry after 20 minutes of inactivity; do not rely on this as a storage or retrieval window. |

If PowerPoint succeeds but PDF fails, expose the deck and mark the build **partial**. Retry only the failed format in a new container using the same approved revision. Do not repeat research. Verify the supported model and document-generation libraries with a small live test before promising branded layouts or PDF conversion.

## State, cost and failure boundaries

- **Persist:** firms, jobs (stage, attempts, provider IDs), brief revisions, findings and sources, and one asset record per requested format. Associate every record with its firm; authorise job and file access on the server.
- **Job states:** `queued → running → succeeded | partial | failed | needs_review`. Distinguish provider failure from “no evidence found.” Keep a failed job visible after refresh; show safe error codes rather than raw provider errors.
- **Bound paid work:** configure search-call, output-size and run-time limits. Record model, usage and job ID without logging keys or full contact details. Retry transient rate limits with backoff; after an ambiguous timeout reconcile an existing provider response or container before starting paid work again.
- **Deferred:** demo provisioning should consume the same approved brief but use a separate application operation. CRM export, email delivery and Sendspark video are independent integrations, not side effects of a build.

## First implementation slice

| Prove first | Acceptance check |
|---|---|
| Website research with a fixture | One supported practice area has a clickable citation; an unsupported founding date is unknown. Apollo-off produces no Apollo requests. |
| Approval and one-file build | An unapproved brief is rejected. Approve it, request only a deck, and receive one valid PowerPoint. |
| Storage and recovery | The deck downloads after container deletion. A simulated second-format failure preserves the deck and can be retried without a new web search. |
| Live integration, later | On one authorised public firm, confirm the selected models support their tools, record duration and cost, open the generated file, and compare its claims against the approved brief. |

**Provider references:** [OpenAI web search and citations](https://developers.openai.com/api/docs/guides/tools-web-search), [Code Interpreter and container lifecycle](https://developers.openai.com/api/docs/guides/tools-code-interpreter), [Containers API](https://developers.openai.com/api/reference/resources/containers), [tool pricing](https://developers.openai.com/api/docs/pricing#built-in-tools).
