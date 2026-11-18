# Apollo: enrichment for input approach 2

Researched 18/11 from Apollo's docs. Nothing built or tested yet.

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

1. Does a Free account (work email) actually allow these 3 endpoints, or do we need Basic?
2. Coverage of NZ/AU law firms and their partners (`match_confidence`, `has_email` rates).
3. Does `technology_names` include legal software (practice management, accounting, e.g. what Acctivity
   would replace), or only website tech (analytics, CMS, Microsoft 365)? This decides whether the
   "technology they already use" pitch works.
4. Exact `person_seniorities` values (e.g. whether `partner` is one).

**Cheap probe:** sign up with a work email, create a scoped key, and with curl run calls 1 → 2 → 3 on 2–3 real NZ firm
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
