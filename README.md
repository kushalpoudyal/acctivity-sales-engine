# Acctivity Sales Engine

Hackathon project: Acctivity's instant sales engine.

Enter a prospect's website and it researches the firm, builds a tailored demo
environment and writes a sales proposal slide deck.

## Goal

- **Input:** the law firms / lawyers we want to pitch Acctivity to.
- **Output:** personalised sales material for each prospect: docs, images, prospect profiles and videos.

## Run locally (Docker)

Needs Docker Desktop running. From the repo root:

```sh
cp Hack_A_API/api/.env.example Hack_A_API/api/.env   # then add your OPENAI_API_KEY
docker compose up --build
```

- Client: http://localhost:5173 (calls to `/api/v1/*` are proxied to the API); prospects at http://localhost:5173/prospects
- API: http://localhost:9001, docs at http://localhost:9001/docs

Source folders are mounted, so edits reload live. Stop with Ctrl+C, or
`docker compose down`. After changing `requirements.txt` or `package.json`, re-run with
`--build` (for the client, `docker compose up --build --renew-anon-volumes`).
To run without Docker, see `Hack_A_API/api/README.md` and `Hack_A_CLIENT/README.md`.

## Context

Decisions, research and open questions live in [`context/`](context/CONTEXT.md).
Log every decision in [`context/LOG.md`](context/LOG.md).

## Status

Part 1: repo set up.
