# Hack A Client

A simple, responsive React website built with Vite. Includes a landing page,
in-page navigation, and an interactive checklist with a progress indicator.
Checklist state is kept in memory and resets when the page reloads.
No backend or environment variables are required.

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
