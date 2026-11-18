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

- Front end: **React** interface to feed in the data (Kushal 18/11).
- Back end (holds API keys, runs research + generation): **Python + FastAPI** (Kushal 18/11).

## Notes on the feed (Claude, 18/11, unverified until probed)

- Sendspark keys each prospect by **email** (`contactEmail`, poll by `/prospects/{email}`). Approach 1 only gets an
  email if the firm's site publishes it; approach 2 (Apollo) reliably supplies one.
- LinkedIn pages generally can't be read without logging in, and scraping is against LinkedIn's terms, so
  "LinkedIn" in approach 1 likely means just the profile URL found on the firm's site.
- API keys (LLM, Apollo, Sendspark) can't live in the browser → the React front end needs the back end.
- Apollo API researched 18/11 → `APOLLO.md` (~2 credits per firm; email yes, phone async; tech-stack coverage of legal software unknown).

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
- Q4: does "tailored demo environment" (in the brief) stay in scope?
- Q5: LLM provider / image generation / slide-deck tool. Video candidate: **Sendspark** (see `SENDSPARK.md`)
- Q6: build approach 1 first with Apollo optional, or go straight to Apollo?

## Tools researched

- [Product requirements](./REQUIREMENTS.md): hackathon scope, acceptance checks and optional Apollo checkbox.
- [Technical design and implementation status](./TECHNICAL_DESIGN.md): research, approvals, structured staff/branding and container-generated PPTX/DOCX/PDF downloads.

- **Sendspark** (videos): `SENDSPARK.md`. API + webhooks on Growth ($99/mo); fills variables into one recorded base video; async, poll or webhook.
- **Apollo** (input approach 2): `APOLLO.md`. Org enrich (1 credit) → people search (free, names obfuscated) → people match (1 credit email, +8 mobile).
