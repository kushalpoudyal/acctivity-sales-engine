# Apollo: enrichment for input approach 2

Researched 18/11 from Apollo's docs; probed against the live API 18/11 (answers under "Unknowns to probe").

## Verdict

Fits approach 2 well. One firm costs roughly **2 credits** (1 to enrich the firm + 1 per contact's email),
or **10 per contact** if we also reveal a mobile number. Finding the right people is free, and
contact details are a separate paid lookup.

## Basics

- Base URL: `https://api.apollo.io/api/v1`
- Auth header: `x-api-key: <key>` (OAuth bearer also supported)
- Keys: Settings → Integrations → API Keys. **Scoped** keys (only the endpoints you tick; others return 403)
  vs **master** key (everything). Use a scoped key for: org enrich, people api_search, people match.
- Plan: docs say "all pricing plans include at least basic access to the Apollo API". **Conflict:** the pricing
  page says API access is on Custom plans. Free accounts must register with a **work email**, and some
  search/enrichment endpoints are blocked for Free. → **Probe before relying on Free.**
- Rate limits are per team (all keys share them):

| Plan | Standard (min/hr/day) | Enrichment (min/hr/day) | Search (min/hr/day) |
|------|----------------------|-------------------------|---------------------|
| Free | 50 / 200 / 600 | 50 (bulk 20) / 200 (bulk 100) / 600 | 50 / 200 / 600 |
| Basic, Professional | 200 / 400 / 2,000 | 1,000 / none / none | 200 / 6,000 / 50,000 |
| Organization | 200 / 600 / 6,000 | 1,000 / none / none | 200 / 6,000 / 50,000 |

Plenty for a hackathon even on Free.

## The 3 calls we'd make per firm

### 1. Firm: `GET /organizations/enrich?domain=<firm domain>`, 1 credit

Identify by `domain` (no www), `website`, or `linkedin_url`; `name` optional.
Useful fields: `name`, `website_url`, `linkedin_url`, `primary_phone`, `raw_address`/`city`/`country`,
`industry`, `industries`, `keywords`, `short_description`, `estimated_num_employees`,
**`current_technologies`** (`uid`, `name`, `category`) / **`technology_names`** (string array).

### 2. Right person: `POST /mixed_people/api_search`, **0 credits**

Filters: `q_organization_domains_list[]`, `person_titles[]` (e.g. "Partner", "Managing Partner",
"Practice Manager"), `person_seniorities[]`, `page`, `per_page` (max 100).
Returns `id`, `first_name`, `last_name_obfuscated` ("Do***e"), `title`, flags `has_email`,
`has_direct_phone`. **No emails or phones**, so pick the person here, then call 3.

### 3. Contact details: `POST /people/match` (or `/people/bulk_match`, ≤10 people), 1–9 credits

Identify by `id` (from call 2), or `name` + `domain`, or `linkedin_url`.
Returns `email`, `email_status`, `linkedin_url`, `title`, `employment_history`, `organization`,
`phone_numbers`, `match_confidence` (high/medium/low/none).
- Cost: 1 credit for the person/email; **+8 credits if a mobile is returned**; nothing if no match.
- `reveal_personal_emails`: optional; we want the work email, so leave it off.
- `reveal_phone_number`: phones arrive **asynchronously** via `webhook_url` (public HTTPS) or
  `poll_only: true` + the response's `request_id_str`. For the demo: skip phones or poll.

## Fit with the sales engine

- Gives Sendspark what it needs (`contactName`, **`contactEmail`**, `company`, `jobTitle`), which approach 1 often can't.
- Gives the person's LinkedIn URL legitimately, with no LinkedIn scraping.
- `industries`/`keywords` complement website research for practice areas.
- Python: plain `httpx` calls from FastAPI; key in `.env` (gitignored, public repo).

## Unknowns to probe

**Answered by the probe run on 2026-11-18** (`apollo_probe.py`, our key, run once, **4 credits** spent).
The firms came from Apollo's own firm search (keywords "law firm"/"legal"/"lawyers", NZ + AU, 5–200 staff),
and the probe checked the first 3 domains. Only aggregates are recorded here (public repo).

1. Does a Free account (work email) actually allow these 3 endpoints, or do we need Basic?
   - **Answer:** our plan **allows** firm search (`mixed_companies/search`), org enrich
     (`organizations/enrich`) and people search (`mixed_people/api_search`). None returned a 403.
     The probe did **not call** bulk match (`people/bulk_match`) because no candidate came back, so it is still unconfirmed.
2. Coverage of NZ/AU law firms and their partners (`match_confidence`, `has_email` rates).
   - **Answer (thin, n=3):** firm search reports ~11.9k matches, but 2 of the first 3 were **legal
     recruiters**, not law firms (industry "staffing & recruiting" / "human resources"). NAICS 5411 doesn't
     exclude them (23 of 25 search results had it), so filter on the enriched industry `law practice` /
     `legal services`. Enrich matched **3/3** domains (all AU; none of the 3 was NZ).
     The people search by title (Managing Partner, Partner, Practice Manager, General Manager, Operations Manager,
     Finance Manager, CFO) returned **0 people at all 3 firms**. So the `has_email` rate, `match_confidence`
     and `email_status` were **not measured**. Apollo has few people at small AU firms: the per-firm totals
     for any single seniority were 0–9.
3. Does `technology_names` include legal software (practice management, accounting, e.g. what Acctivity
   would replace), or only website tech (analytics, CMS, Microsoft 365)? This decides whether the
   "technology they already use" pitch works.
   - **Answer:** **website and IT tech only.** Each firm had 41–58 technologies: CDN, analytics, tag manager,
     WordPress plugins, HubSpot, Microsoft 365/Exchange, Google Workspace and AWS. None of the 3 showed
     practice-management, legal or accounting software (LEAP, Actionstep, Clio, Smokeball, Xero, MYOB, etc.).
     So the "technology they already use" pitch can only mention their office and web tools, not legal software.
4. Exact `person_seniorities` values (e.g. whether `partner` is one).
   - **Answer:** Apollo accepted all 8 values we tried (no 400): `owner`, `founder`, `c_suite`, `partner`, `vp`,
     `head`, `director`, `manager`. Only `head`, `director`, `manager` and `founder` returned anyone.
     `partner` returned 0 at all 3 firms (one was a real law practice), so it is **accepted but unproven**.
     Don't rely on it alone. Combine it with `director`/`founder`/`owner`.
5. (Issue #3) Which fields do firm-search results carry?
   - **Answer:** `id`, `name`, `primary_domain`, `website_url`, `linkedin_url`, `phone`/`primary_phone`,
     `naics_codes`, `sic_codes`, `founded_year`, `organization_revenue`(`_printed`), headcount growth
     (6/12/24 months), `languages`, social URLs, `logo_url`, `publicly_traded_*`, `owned_by_*`, intent flags.
     Search results have **no employee count, industry or location**: `estimated_num_employees` is absent
     (`None` for all 10 shown). Those come from enrich (1 credit each).

**What this means for the build:** "Find firms" (#6) can ship because firm search is allowed, but it needs a
law-practice filter to drop recruiters. Contacts (#5) should fall back to staff found on the firm's website,
because Apollo's people search often finds no partners at small AU firms. Email reveal (`bulk_match`) needs
one real test before we rely on it.

**Cheap probe (original plan, done 18/11 via `apollo_probe.py`):** sign up with a work email, create a scoped key, and with curl run calls 1 → 2 → 3 on 2–3 real NZ firm
domains (≈ 4–6 credits). That answers all four.

## Sources

- https://docs.apollo.io/reference/organization-enrichment
- https://docs.apollo.io/reference/people-api-search
- https://docs.apollo.io/reference/people-enrichment
- https://docs.apollo.io/reference/bulk-people-enrichment
- https://docs.apollo.io/reference/rate-limits
- https://docs.apollo.io/docs/developer-faqs
- https://docs.apollo.io/docs/create-api-key
- https://www.apollo.io/pricing
