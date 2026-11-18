# Acctivity Instant Sales Engine: product requirements

Working draft | 18 Nov 2026 | Audience: hackathon team

**Demo goal:** A sales rep enters a law firm's website, reviews what the engine found, and opens a tailored demo workspace and proposal deck. The rep can see which facts shaped both outputs. A website is enough: the demo must work without Apollo, a contact email, or a connected CRM.

## Proposed hackathon scope

| ID | What the rep needs | Acceptance check |
|---|---|---|
| F1 | Add a firm by website from the Prospects screen. See the firm, key contact if known, asset progress and outreach status. | Submitting a website creates a visible research job. Reopening the firm shows its saved brief and assets without starting another paid job. |
| R1 | Research the firm's website using OpenAI web search. Capture practice areas, offices, publicly listed staff and roles, and founding year or time in business where supported. | Each finding links to its source. Listed staff are not presented as total headcount. Missing facts stay unknown. The rep can correct the brief before generating assets. |
| R2 | Choose whether to use Apollo for this research run. | The per-run checkbox behaves as specified below. Website research and asset generation do not require an Apollo match. |
| D1 | Create a demo firm, users and sample matters suited to a confirmed practice area. | A rep can open a seeded workspace and demonstrate a relevant workflow. Users and matters are fictional; no invitations go to the prospect. |
| A1 | Select outputs rather than generate everything. Prioritise the proposal slide deck and demo environment; allow skipped assets to be generated later. | The deck and demo use the same reviewed brief. Selecting only a deck does not provision users, generate a video or send an email. |
| A2 | Generate downloadable presentation files inside an OpenAI sandbox container. | The rep can download and open the generated PowerPoint after the container session ends. A failed asset is marked failed and can be retried without repeating the research. |
| S1 | Keep research and sales progress between sessions. | FastAPI stores firms, research sources, rep edits, job status and asset references in local PostgreSQL. Provider keys stay on the server; neither keys nor real prospect records go into the public repo. |

## Apollo checkbox: proposed behaviour

Place **Use Apollo to find firm and contact details** beneath the website field, under *Research sources*, not under *What to generate*. Helper text: **Optional. Uses Apollo credits for enrichment.** Proposed default: **off** for each new run.

| State | Expected result |
|---|---|
| Off | Research the submitted website. Make **zero Apollo requests** and consume no Apollo credits. Show a public contact if the site identifies one; do not guess an email. |
| On | Supplement website research with an organisation lookup by domain and a people search for relevant roles. Label Apollo findings separately. Show candidate contacts; let the rep choose one before a paid work-email lookup. Do not request mobile numbers. |
| Unavailable or no match | If not configured, disable the option with an explanation. If a lookup fails or finds nothing, distinguish those outcomes, preserve the website results and let the rep continue. |

The checkbox concerns **one known firm**. **Find firms for me** is a separate proposed discovery action: preview a shortlist and let the rep select firms before researching them. Checking Apollo must not trigger bulk discovery, outreach or CRM sync.

## Backlog and decisions

| Candidate | Decision needed before building |
|---|---|
| More input methods | Website first. Choose next between CSV import, firm name, LinkedIn URL and Apollo discovery. A profile link published by the firm can already be recorded as a reference. |
| More assets | Prioritise Word proposal, PDF one-pager or welcome pack, social images, email draft and personalised video. Sendspark is a candidate for video; missing email should block only the video. Generating an email draft is not permission to send it. |
| Branding | Decide which assets should use the firm's colours. Keep Acctivity identifiable as the author of the proposal. |
| CRM after the first demo | Select a CRM and fields to send: firm, approved contact, meeting notes and asset links. Start with a rep-reviewed push; automatic sync on booking is a separate decision. |
| Dashboard | Define fit score, filters and pipeline metrics before showing them. A generated email is not an email sent; a generated demo is not a meeting booked. |
| Apollo feasibility | Check account permissions, credit budget and contact quality on a small AU/NZ sample before making enrichment part of the live presentation. |

## Capturing changes

Add a row with a stable ID, the rep's need and an observable acceptance check. Mark new ideas **Proposed** until agreed; record decisions, owner and date in the [team log](./LOG.md). Background: [original brief and input options](./CONTEXT.md), [Apollo research](./APOLLO.md), [Sendspark research](./SENDSPARK.md).
