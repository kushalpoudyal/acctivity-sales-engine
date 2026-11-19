# Hackathon: Acctivity Instant Sales Engine

Shared team context for the hackathon. Everything related to it gets recorded here.
Decisions and progress go in `LOG.md` (append-only).

## Brief (slide 07, `brief-slide-07.png`)

> **Instant sales engine**: Enter a prospect's website and it researches the firm,
> builds a tailored demo environment and writes a sales proposal slide deck.

## Goal (Kushal 18/11)

- **Input:** companies / law firms / lawyers we want to pitch Acctivity to.
- **Output:** personalised material per prospect:
  - docs (proposal)
  - images
  - "prospects" (meaning still to confirm: prospect profile/research brief, or a prospectus doc)
  - videos
- Purpose: our own sales automation.

## Input feed: 2 possible approaches (Kushal 18/11, verbatim)

1. Research the law firm's website to identify its areas of specialisation (family law, commercial law, etc.)
   and the right person to contact (partner, etc.) only from the website (we are limited to that information)
   and / or their LinkedIn profile linked to the company.
2. By integrating Apollo as an additional external integration, we could do everything mentioned above, plus
   access the right contact details (email address, phone number, and LinkedIn profile). We could also gather
   additional information, such as the technology the firm is already using.

## Stack

- Front end: **React** interface to feed in the data (Kushal 18/11). As built: Vite + React in `Hack_A_CLIENT/`.
- Back end (holds API keys, runs research + generation): **Python + FastAPI** (Kushal 18/11). As built: `Hack_A_API/api/`.
- As built (Paul 18/11): **OpenAI** for research + deck generation, **SQLite** (`prospects.sqlite3`, gitignored) for storage.

## What's built (Paul, 18/11: commits 1f68148, 19549a9, ed2fa2a)

Local prototype, single process, **no auth** (keep both dev servers on loopback). Details in `Hack_A_API/api/README.md`.

**Flow = input approach 1 (website only):** add firm → research its website → review + edit + approve records → generate branded PPTX / PDF and a personalised AI video, or a demo export.

**API** (`Hack_A_API/api/`, FastAPI on `127.0.0.1:9001`, docs at `/docs`):

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/health`, `/openai/test` | health check; OpenAI connection test |
| POST | `/api/v1/firms` | save a firm (website + optional display name) |
| GET | `/api/v1/firms`, `/api/v1/firms/{id}` | list firms; firm detail (records, logos, approvals, output runs, status) |
| POST | `/api/v1/firms/{id}/research` | crawl the site, then a **paid** OpenAI extraction; `{"reuse_pages": true}` re-extracts saved pages without crawling; async (202), poll detail until `status` ≠ `researching` |
| PUT | `/api/v1/firms/{id}/brief` | save rep edits to the firm name, preferred logo and records |
| GET | `/api/v1/firms/{id}/brand-assets/{asset_id}` | saved logo |
| POST | `/api/v1/firms/{id}/approvals` | approve the selected records → frozen snapshot (no paid call) |
| POST | `/api/v1/firms/{id}/outputs` | **paid** copy call + brand templates → PPTX / PDF, and/or a Sendspark `video` (async, 202); download from `.../outputs/{job}/files/{file}` |
| DELETE | `/api/v1/firms/{id}/outputs/{job_id}` | hard delete one output run and its files (409 while running) |
| DELETE | `/api/v1/firms/{id}` | hard delete the firm and everything stored for it (409 while research or a run is in progress) |
| POST | `/api/v1/firms/{id}/proposals/{proposal_id}/build` | build the `.pptx` (async, 202) |
| GET | `/api/v1/firms/{id}/proposals/{proposal_id}/download` | download the `.pptx` |
| GET | `/api/v1/firms/{id}/proposals/{proposal_id}/demo-export` | JSON staging payload of approved records + logo links |
| POST / GET / PUT | `/api/v1/firms/{id}/demo-plan` | generate (optional **paid** tailoring call) / read / edit + re-validate the firm's demo plan (#18) |
| POST / GET | `/api/v1/firms/{id}/demo-seed` | seed the demo firm from the plan as a background job (resumes a failed run) / latest run's progress (#19) |

- **Research (`firm_research.py`):** obeys robots.txt; firm's own domain (+www) only; prioritises staff/about/contact/service pages;
  stops at 32 pages / 240k chars / 150 s; no login, JavaScript rendering or PDFs (so Cloudflare-challenged sites such as
  mallesons.com return nothing). Logos (PNG/JPEG/WebP, same domain, ≤1.5 MB) are saved in `brand_assets`, not hotlinked.
- **Research data (normalised, Kushal 18/11):** SQLite tables `pages` (crawled text, reused for re-extraction),
  `practice_areas` (+ `services`), `people`, `person_practice_areas`, `offices` and `facts` (overview / history / credential /
  clients / approach). Every record cites its page + a verbatim passage checked against it; rep edits keep the extracted
  values in `original`. Approvals freeze the selected records as a JSON snapshot (`version: 2`).
- **Outputs (`outputs.py` + `documents.py`, Kushal 18/11):** one paid OpenAI call writes short copy (title, summary,
  proposed opportunities, pilot, next steps, demo agenda) from the approved records; our own templates build the PPTX
  (16:9 proposal) and PDF (A4 welcome guide); Word (DOCX) was removed 19/11. No Code Interpreter container. The older
  `proposals/{id}/build` endpoint uses the same PPTX template.
- **Brand kit (`Hack_A_API/api/brand/`, Kushal 18/11):** Acctivity logo + "Acc" mark, palette (Slate Grey `#3D443D`,
  Steel Blue `#4396BA`, accents Light Pink/Gold/Rouge/Light Grey), Lora headings + Inter body, watercolour illustrations
  and background. `brand/BRAND.md` is the guide the templates implement. Generated material uses Acctivity colours only
  (prospect logo optional, cover only). PDF embeds the fonts; PPTX needs Lora + Inter installed, else serif/sans fallback.
- **Apollo (optional, click-to-spend; `apollo.py`, `find_firms.py`, `apollo_findings.py`):** firm enrichment (1 credit,
  stored on the firm), free people search, email reveal (1 credit per person, batches of 10, never twice) into an
  `apollo_contacts` table kept apart from website research and linked to `people` by name; industries, keywords, tech
  stack and contacts are approvable Apollo findings (`apollo` in the snapshot), shown in outputs as Apollo-reported.
  Revealed emails never go to OpenAI. **Find firms** searches Apollo for small/medium law firms (1 credit/page).
- **Demo export:** our own JSON schema, **not** an Acctivity import contract; creates no tenants/users and sends no invites.
- **Demo plan (`demo_plan.py` + `demo_templates.py`, #18, branch `demo-plan`):** what the seeder (#19) will write into a
  rep's test firm: 1–6 staff (real names + titles only), 15–25 matters with fictional clients, 3–4 weeks of time, fixed
  fees, disbursements and 2–3 invoices; deterministic from approval + date + seed, one optional OpenAI call tailors
  names/descriptions only; saved per firm in `demo_plans`. Rates and fees are **drafts to be checked by sales**. Matter
  types resolve against Docs Core through an injected provider that #17/#19 must supply (503 until then). Details in
  `Hack_A_API/api/README.md` → Demo plan.
- **Models:** research + deck default `gpt-6.1-sol`; override with `OPENAI_MODEL`, or per step `OPENAI_RESEARCH_MODEL` /
  `OPENAI_DECK_MODEL`. Only `OPENAI_API_KEY` is required (`.env`). The README and `.env.example` also mention
  `gpt-5.4-mini` as the default; in code that's only the `/openai/test` fallback.
- Tests: `test_firm_research.py`, `test_prospects.py`, `test_outputs.py`, `test_documents.py` (24 tests).

**Client** (`Hack_A_CLIENT/`, Vite dev server on `127.0.0.1:5173`, proxies `/api/v1` → `:9001`):
- `/prospects`: live directory (`Prospects.jsx`): add/filter firms, run or re-extract research, review/edit records by kind
  (practice areas, people with linked areas, offices, facts) with source links, approve, generate + download PPTX/PDF, delete output runs, delete the firm.
- Find firms panel (Apollo search, review list, add to directory) and Apollo panels in the Research tab
  (enrich, find people, reveal emails, Apollo contacts table, review Apollo findings).
- `/sales-studio.html`: clickable **mockup** of the full sales engine (browse firms, add one with optional **Apollo**
  enrichment, simulated research + output). Fictional data, no API calls.
- Landing page with a checklist (in-memory).

**Personalised AI video (19/11, branch `sendspark-ai-video`):** optional output that sends the approved firm name to
the Sendspark "Stalktivity" campaign and saves the video's share link. See `SENDSPARK.md` → Our setup.

**Demo environment logins and guards (19/11, branch `demo-logins`, issue #17):** `demo_logins.py` gives the seeder/wipe a
`DemoFirm(firm_id)` with `.service(user_id)` (shared client-credentials login acting as firm + staff, token cached) and
`.rep()` (the rep's own access token, memory only, handed over by the host-side `Hack_A_API/tools/Send-RepLogin.ps1`
from the tray app's DPAPI cache, or pasted; never refreshed). Every write checks the guards: `DEMO_ENVIRONMENT=test`,
`DEMO_FIRM_ALLOWLIST`, rep token current/test/same firm, Management `isTestFirm`. Endpoints: `GET|PUT|DELETE /api/v1/demo/rep-login`,
read-only smoke check `GET /api/v1/demo/firms/{id}/check`. Settings: `DEMO_*`, `ACCTIVITY_*_API_URL`, `ACCTIVITY_SERVICE_*`
(see `Hack_A_API/api/README.md`).

**Demo seeder (19/11, branch `demo-seeder`, issue #19):** `demo_seeder.py` writes the saved plan into the demo firm
(`DEMO_FIRM_ID`, staff `DEMO_STAFF_USER_IDS` in slot order): firm + staff rename, rates, matters, client contacts,
time entries, fees, disbursements, invoices, then each matter's description/responsible/client. Guards and id checks
before any write; only ids it created or read; no retries; bounded concurrency (`DEMO_SEED_CONCURRENCY`). Every
item's external id is kept in `seed_runs` / `seed_run_items`, so a failed run resumes. Progress is in the live
updates stream (`demo_seed`). It also registers the Docs Core matter-type provider for demo plans. Not run live yet.

**Not built yet:** per-prospect images (outputs use the brand illustrations), Acctivity product content for proposals, live Acctivity demo environment, auth.

## Notes on the feed (Claude, 18/11, unverified until probed)

- Sendspark keys each prospect by **email** (`contactEmail`, poll by `/prospects/{email}`). Approach 1 only gets an
  email if the firm's site publishes it; approach 2 (Apollo) reliably supplies one.
- LinkedIn pages generally can't be read without logging in, and scraping is against LinkedIn's terms, so
  "LinkedIn" in approach 1 likely means just the profile URL found on the firm's site.
- API keys (LLM, Apollo, Sendspark) can't live in the browser → the React front end needs the back end.
- Apollo API researched 18/11 → `APOLLO.md` (~2 credits per firm; email yes, phone async). Probed 18/11: firm search is allowed on our plan; `technology_names` lists web/IT tools only, no legal software; Apollo has few people at small AU firms.

## Parts

1. **Part 1:** create the GitHub repo + this context. (done 18/11)

## Repo

- https://github.com/kushalpoudyal/acctivity-sales-engine (**public**), default branch `main`.
- Public repo: keep prospect data, generated material and secrets out of git
  (`.gitignore` covers `.env*` and `output/`).

## Open questions

- Q1: ~~repo name, visibility~~ answered: acctivity-sales-engine, public
- Q2: what "prospects" means as an output
- Q3: input shape: website URL only (per brief), or also a name/LinkedIn/CSV list?
  As built: website + optional display name.
- Q4: ~~does "tailored demo environment" stay in scope?~~ answered 19/11: yes, seed a per-rep Acctivity test firm per prospect (design in TECHNICAL_DESIGN.md, platform facts in ACCTIVITY.md). Previously:
  a live Acctivity environment needs an agreed deployment API, required fields and a safe demo-account policy.
- Q5: LLM provider / image generation / slide-deck tool. As built: OpenAI for research + copy; our own python-pptx /
  reportlab templates for the files (Word removed 19/11).
  Images still open. Video candidate: **Sendspark** (see `SENDSPARK.md`)
- Q6: ~~approach 1 first or straight to Apollo?~~ answered 18/11: Apollo is an optional add-on (enrich + find firms), click-to-spend. Plan in LOG.md; issues #3–#7.

## Tools researched

- [Product requirements](./REQUIREMENTS.md): hackathon scope, acceptance checks and optional Apollo checkbox.
- [Technical design and implementation status](./TECHNICAL_DESIGN.md): research, approvals, normalised research records, branding and template-built PPTX/PDF downloads.

- **Sendspark** (videos): `SENDSPARK.md`. API + webhooks on Growth ($99/mo); fills variables into one recorded base video; async, poll or webhook.
- **Acctivity** (demo environment seeding): `ACCTIVITY.md`. Where firm/user/matter/time data lives, service-login probe results on test.
- **Apollo** (input approach 2): `APOLLO.md`. Org enrich (1 credit) → people search (free, names obfuscated) → people match (1 credit email, +8 mobile). Firm search: 1 credit/page, **allowed** on our plan (needs a law-practice filter to drop recruiters). Probe: `Hack_A_API/api/apollo_probe.py`.
