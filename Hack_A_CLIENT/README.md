# Hack A Client

A simple, responsive React website built with Vite. Includes a landing page,
in-page navigation, and an interactive checklist with a progress indicator.
Checklist state is kept in memory and resets when the page reloads.
No backend or environment variables are required.

The separate live `/prospects` directory requires FastAPI on `127.0.0.1:9001`.
Vite proxies `/api/v1` to that server. Saving or viewing a firm is local;
research runs and output generation start paid OpenAI requests.

Firm URLs such as `/prospects?firm=1` reopen that firm's workspace. **Research**
supports editing saved firm/staff details and approving selected findings;
**Create outputs** selects a frozen approval, PPTX/DOCX/PDF formats, logo and sales
instructions, then tracks paid OpenAI container jobs and saved downloads.
Saving, editing and approving do not incur provider charges. Container generation
does. Run the current API version; an older API now produces an actionable
version warning rather than a blank workspace.

Completed research shows a fixed total duration and finish time, not live API or
progress-age counters. Live timers and the started notice appear only while a
research job is active. Refreshing or reconnecting never starts a paid run.

Open `/sales-studio.html` on the dev server for a separate, clickable sales-engine
mockup: browse firms, add one with optional Apollo enrichment, then simulate
research and sandbox output generation. It uses fictional data, makes no API
calls, and resets on refresh.

## Development

Use Node.js 22.12+ (or 20.19+).

```sh
npm install
npm run dev
```

Open the local URL printed by Vite.

## Checks and production build

```sh
npm run lint
npm run build
npm run preview
```

The production output is generated in `dist`. Google Fonts is optional;
the page uses system sans-serif fallbacks when it is unavailable.
