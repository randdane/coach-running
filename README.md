# Homelab Running Coach

Local, self-hosted port of the PyTexas [ai-running-coach](https://github.com/)
demo. Runs as a Docker Compose stack in your homelab; receives Strava
webhooks via a Tailscale Funnel and posts coaching messages to a
self-hosted ntfy topic.

## Stack

- `coach-app` — FastAPI + APScheduler, the coaching logic + web UI
- `litellm` — OpenAI-compatible proxy; route to cloud or local models
- `ollama` (optional, commented in compose) — local model runtime
- `ntfy` — self-hosted push notifications

## Setup

1. `cp .env.example .env` and fill in secrets. Generate a 32-char
   `WEBHOOK_SECRET` with `openssl rand -hex 16`.
2. Create a Strava API app; note client id / secret / refresh token.
3. Configure Tailscale Funnel on the host to forward
   `https://<host>.ts.net/webhook/strava/*` → `http://localhost:8000`.
4. Configure your Strava subscription callback URL to
   `https://<host>.ts.net/webhook/strava/<WEBHOOK_SECRET>`.
5. `docker compose up -d`.
6. Open `http://localhost:8000` (via tailnet) to see the dashboard.

## Manual triggers

- Click "Run morning check-in" on the dashboard.
- Click "Re-review" on any listed activity.

## Restore from backup

```bash
# nightly bundles live in data/backups/coach-YYYYMMDD.tar.gz
cd data && rm -rf memory && sqlite3 coach.db ".exit"
tar -xzf backups/coach-20260419.tar.gz
mv coach-20260419.db coach.db
```

## Operations

- Logs: `docker compose logs -f coach-app`
- Health: `curl http://localhost:8000/healthz`
- Scheduler status: visit `/settings`

## Development

```bash
uv sync --all-groups
uv run pytest
```

## Design

See `docs/superpowers/specs/2026-04-19-homelab-running-coach-design.md`.
