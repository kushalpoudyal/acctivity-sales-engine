# Acctivity Sales Engine

Hackathon project. The shared team context lives in `context/`.

- Read `context/CONTEXT.md` before starting any task, plus the matching research file
  (`context/APOLLO.md`, `context/SENDSPARK.md`) when the task touches that tool.
- Record every decision or progress step as a new line at the end of `context/LOG.md`
  (`- YYYY-MM-DD · <what> (<who>)`), and keep `context/CONTEXT.md` current (stack, open questions).
- New research on a tool or API goes in its own `context/<TOOL>.md`, linked from `CONTEXT.md`.
- The repo is public: never commit API keys (`.env` only), prospect data or generated material (`output/`).

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `kushalpoudyal/acctivity-sales-engine`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
