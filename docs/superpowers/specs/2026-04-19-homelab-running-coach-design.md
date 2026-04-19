# Homelab Running Coach — Design

**Date:** 2026-04-19
**Status:** Draft for review

Local-only port of the `ai-running-coach` PyTexas demo
(`/home/r/Projects/Pythoneers/PyTexas-2026/ai-running-coach/`) for single-user
homelab deployment via Docker Compose. AWS-native services (Lambda,
DynamoDB, S3, EventBridge) and third-party Slack are replaced with
long-running containers and self-hosted components. The pedagogical core
(prompt assembly, single `save_observation` tool, markdown "memory without
RAG") is preserved.

## Goals

- Run entirely in my homelab via `docker compose up`, no cloud account required.
- Daily morning coaching message + per-run coaching message, same triggers as
  the original.
- Work with either a local LLM (via Ollama) or a cloud API, swappable at
  runtime.
- Provide a small local web UI for visibility and manual control.
- Keep the prompt + memory mechanism faithful to the talk's thesis.

## Non-goals

- Multi-user / multi-athlete support.
- Public internet exposure of anything except the Strava webhook endpoint.
- In-app authentication. Access is gated by the tailnet.
- Horizontal scaling, HA, zero-downtime deploys.
- Parity with the production `momentum` system's advanced metrics
  (CTL/ATL, zone calculation, durability, etc.).

## Assumptions

- The host runs Docker + Docker Compose and is reachable on the user's
  tailnet.
- Tailscale Funnel is configured on the host (outside of compose) and forwards
  `https://<host>.ts.net/webhook/strava` to the compose-exposed FastAPI port.
- The user has a Strava API app, an optional OpenAI/Anthropic API key, and
  (optionally) an Ollama-compatible GPU for local models.
- Backups of `./data/` are handled by an existing host-level backup tool.

## High-level architecture

```
┌─────────────────────────────────────────────────────────────┐
│  coach-app (FastAPI + APScheduler)                          │
│    • /webhook/strava   (Tailscale Funnel → here)            │
│    • /ui/*             (HTMX web UI, tailnet-only)          │
│    • /api/*            (trigger actions, read state)        │
│    • scheduler:        morning check-in, daily Strava poll  │
└────┬───────────────┬────────────────┬──────────────────┬────┘
     │               │                │                  │
     ▼               ▼                ▼                  ▼
  SQLite         athlete_         litellm             ntfy
  (volume)       context.md       (proxy)             (push)
                 (volume)             │
                                      ▼
                                  ollama │ cloud API
```

Four containers total: `coach-app`, `litellm`, `ollama` (optional), `ntfy`.
Tailscale Funnel runs on the host, not in compose.

## Technology choices

| Concern          | Choice                              | Why                                                           |
|------------------|-------------------------------------|---------------------------------------------------------------|
| App framework    | FastAPI                             | Serves webhook + UI + API from one process                    |
| Scheduling       | APScheduler with SQLite jobstore    | In-process cron; survives restarts for pending one-off jobs   |
| Storage          | SQLite on bind-mounted volume       | Single-user; trivial backups; no ops                          |
| Memory file      | Plain markdown on bind-mounted vol  | Faithful to original design; user-editable                    |
| LLM access       | LiteLLM proxy                       | OpenAI-compatible; route to Ollama or cloud per-request       |
| Local LLM        | Ollama                              | Well-supported tool calling on Llama 3.1+ / Qwen 2.5+         |
| Notifications    | ntfy (self-hosted)                  | Homelab-native push; no third-party account                   |
| Strava trigger   | Tailscale Funnel webhook + daily poll | Low-latency for normal case + reconciliation safety net     |
| Web UI           | Jinja2 + HTMX + Tailwind via CDN    | No build step; small surface area                             |
| Config           | pydantic-settings + `.env`          | Fail-fast on boot if keys missing                             |
| Logging          | structlog → JSON on stdout          | Docker log driver handles rotation                            |

## Code layout

```
homelab-running-coach/
├── docker-compose.yml
├── .env.example
├── Dockerfile                  # coach-app
├── pyproject.toml              # uv-managed
├── litellm/
│   └── config.yaml
├── data/                       # bind-mounted; gitignored
│   ├── coach.db
│   ├── memory/
│   │   ├── athlete_context.md
│   │   ├── training_plan.md
│   │   └── history/            # snapshots on edit
│   ├── backups/                # nightly SQLite VACUUM INTO
│   └── ntfy/
├── prompts/
│   └── coach_voice.md          # read-only, shipped in image
└── src/coach/
    ├── main.py                 # FastAPI app factory + scheduler startup
    ├── config.py               # pydantic-settings
    ├── scheduler.py            # APScheduler setup + jobs
    ├── llm.py                  # LiteLLM client + save_observation tool
    ├── memory.py               # read/append athlete_context.md
    ├── notify.py               # ntfy client
    ├── prompts.py              # pure prompt assembly
    ├── jobs.py                 # morning_checkin / post_run_review orchestration
    ├── strava/
    │   ├── client.py           # OAuth refresh, get_activity, list_recent
    │   └── webhook.py          # FastAPI router
    ├── storage/
    │   ├── db.py               # connection + migrations
    │   ├── activities.py       # repo
    │   ├── messages.py         # repo
    │   ├── tokens.py           # Strava token repo
    │   └── migrations/*.sql
    └── web/
        ├── routes.py           # HTMX endpoints
        └── templates/
```

### Module boundaries

- **`prompts.py` (pure):** `build_morning_prompt(...)` and
  `build_post_run_prompt(...)` take plain data, return a string. No I/O, no
  clock, no repos. Fully unit-testable. Absorbs what the original `context.py`
  did.
- **`jobs.py` (orchestration):** Composition root for each use case. Thin
  functions that call repos, `prompts.build_*`, LLM client, save the message,
  publish to ntfy. Not pure; not trying to be.
- **`storage/*`:** Only place that touches SQLite.
- **`memory.py`:** Only place that touches `athlete_context.md`.
- **`llm.py`:** Only place that talks to LiteLLM.
- **`scheduler.py`, `strava/webhook.py`, `web/routes.py`:** Entry points. Each
  is thin; all dispatch to `jobs.py`.

## Data model

Three SQLite tables. Migrations are versioned `.sql` files applied via
`PRAGMA user_version` at startup.

```sql
CREATE TABLE activities (
    id            INTEGER PRIMARY KEY,        -- Strava activity id
    athlete_id    TEXT    NOT NULL,
    start_date    TEXT    NOT NULL,           -- ISO8601
    name          TEXT    NOT NULL,
    type          TEXT    NOT NULL,
    distance_km   REAL,
    duration_min  INTEGER,
    avg_hr        INTEGER,
    raw_json      TEXT    NOT NULL,           -- full Strava payload
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_activities_athlete_date
    ON activities(athlete_id, start_date DESC);

CREATE TABLE messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL,           -- 'morning' | 'post_run' | 'manual'
    activity_id   INTEGER,                    -- nullable FK to activities
    model         TEXT    NOT NULL,
    prompt        TEXT    NOT NULL,
    response      TEXT    NOT NULL,
    tool_calls    TEXT,                       -- JSON
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_created ON messages(created_at DESC);

CREATE TABLE strava_tokens (
    athlete_id     TEXT PRIMARY KEY,
    access_token   TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,             -- Strava rotates on use
    expires_at     INTEGER NOT NULL
);
```

On first boot, `strava_tokens` is seeded from `.env` values
(`STRAVA_REFRESH_TOKEN`). Subsequent refreshes persist back to the table so
rotated tokens aren't lost.

## Entry points

### 1. Morning check-in (APScheduler cron)

- Cron from `MORNING_CRON` env (default `0 6 * * *`), timezone from `TZ`.
- Misfire grace = 1 hour so a restart near the firing time still runs it.
- Calls `jobs.morning_checkin()`.

### 2. Strava webhook

- `GET /webhook/strava?hub.challenge=...` → echo challenge (subscription
  handshake). Required by Strava.
- `POST /webhook/strava`:
  - If `object_type=activity`, `aspect_type=create`, and
    `owner_id == ATHLETE_ID` → enqueue an APScheduler one-off job that runs
    `jobs.post_run_review(activity_id)`.
  - Otherwise → return 200 with `ignored`.
- Handler returns 200 immediately; work runs async. Strava retries on
  non-2xx and has a short timeout, so we must not block on the LLM.
- No signature verification (Strava does not sign). The `owner_id` check
  and the fact that we always re-fetch from Strava with our own token limits
  the blast radius of a spoofed POST to "wasted Strava API call."

### 3. Daily reconciliation poll (APScheduler cron)

- Cron from `POLL_CRON` env (default `30 22 * * *`).
- Calls Strava `/athlete/activities?after=<most_recent_start_date_in_db>`.
- For each returned activity, `INSERT OR IGNORE` into `activities`, then
  invokes `jobs.post_run_review(activity_id)` only if no message with that
  `activity_id` already exists.
- Ensures a single coach message even if webhook + poll both see the same run.

### 4. Manual triggers (UI buttons / API)

- `POST /api/jobs/morning` → one-off `jobs.morning_checkin(kind='manual')`.
- `POST /api/jobs/post-run/{activity_id}` → one-off
  `jobs.post_run_review(activity_id, kind='manual')`.
- Manual invocations save messages with `kind='manual'` so they are
  distinguishable in history.

All four entry points dispatch into the same `jobs.*` functions; behavior is
identical regardless of trigger.

## LLM routing

`llm.py` uses the `openai` Python SDK with `base_url=http://litellm:4000`.
The model string chosen per-request selects the backend.

`litellm/config.yaml` (illustrative):

```yaml
model_list:
  - model_name: local-small
    litellm_params:
      model: ollama/llama3.1:8b
      api_base: http://ollama:11434
  - model_name: local-large
    litellm_params:
      model: ollama/qwen2.5:32b
      api_base: http://ollama:11434
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
  - model_name: claude
    litellm_params:
      model: anthropic/claude-opus-4-7
      api_key: os.environ/ANTHROPIC_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

### Model selection

- Default model: `COACH_MODEL` env, used by scheduled jobs.
- Manual triggers may pass an override, chosen from the UI model picker
  (populated from LiteLLM `/v1/models`).
- The chosen model is recorded in `messages.model` for after-the-fact quality
  comparison.

### Tool calling

`save_observation` is the single registered tool. Cloud models handle this
reliably; recent Ollama models (Llama 3.1+, Qwen 2.5+) are supported but
variable. Mitigations:

- Hard loop cap (default 5) on tool calls per invocation.
- If a model returns no usable content and no tool call, log a warning and
  surface in the UI; do not retry automatically.

### Ollama

Optional service. Commented GPU reservation block in `docker-compose.yml`.
If disabled, remove the `local-*` entries from `litellm/config.yaml`.

## Web UI

Served from `coach-app` at `/`. Jinja2 + HTMX + Tailwind CDN. No build step.
Tailnet-only; no in-app auth.

Pages:

- **`/` — Dashboard:** today's date + MVW progress, latest coach message,
  recent activities table with per-row "Re-review" button, buttons to run
  morning check-in / post-run review on demand, model picker for manual
  runs.
- **`/messages` — History:** paginated coach messages; detail view shows
  full prompt, response, tool calls, linked activity. Filter by kind and
  model.
- **`/memory` — Athlete context:** renders `athlete_context.md`; editable
  textarea. Save writes atomically and snapshots prior content to
  `data/memory/history/athlete_context-<ts>.md`.
- **`/plan` — Training plan:** same pattern for `training_plan.md`.
- **`/settings` — Status:** scheduler next-fire times, last poll result,
  Strava token expiry, LiteLLM health, ntfy health. Read-only.

HTMX endpoints return HTML fragments. Manual job triggers spawn APScheduler
one-off jobs; the UI polls `/api/jobs/{id}/status` until completion, then
swaps in a fragment.

## Notifications

`notify.py` posts to a self-hosted ntfy topic configured via env
(`NTFY_BASE_URL`, `NTFY_TOPIC`). Title is the message kind, body is the
coach response. No markdown rendering in ntfy — use the plain text. Failures
are logged but do not raise; a missed notification should never roll back a
saved message.

## Operations

### Health

- `GET /healthz`: SQLite reachable + scheduler running → 200.
- `GET /readyz`: adds LiteLLM ping + Strava token not-yet-expired.
- Compose healthcheck on `/healthz`.

### Logging

- structlog JSON on stdout.
- One event per job: `job_id`, `kind`, `model`, `duration_ms`,
  `tool_calls_count`, `activity_id`.
- Strava + LiteLLM HTTP calls at DEBUG; default INFO.

### Backups

- `./data/` is bind-mounted and included in the host's existing backup
  regimen.
- Nightly APScheduler job: `VACUUM INTO data/backups/coach-YYYYMMDD.db`,
  retain last 14.
- Markdown edits snapshot to `data/memory/history/` automatically; easy
  rollback of a bad `save_observation`.

### Secrets & config

- `.env` loaded by compose via `env_file`. `.env.example` committed with
  every key documented.
- `pydantic-settings` parses env at startup; app refuses to boot on missing
  required values with a clear error.
- `LITELLM_MASTER_KEY` shared between `coach-app` and `litellm`.

## Testing

- **`prompts.py`:** unit tests with fixture activities + fixture memory;
  snapshot the assembled string.
- **`storage/*`:** integration tests against in-memory SQLite.
- **`jobs.py`:** tests use a fake LLM client (canned responses, scripted
  tool calls) and a fake notifier; verify DB writes + notify calls.
- **`strava/webhook.py`:** FastAPI `TestClient` with canned payloads;
  fixtures reused from the source repo's `test-fixtures/`.
- No CI tests against real Ollama or real Strava. A `make smoke` target
  runs against a live local stack for pre-release sanity.

## Migration from the source repo

- **Keep:** `prompts/coach_voice.md`, the `save_observation` tool schema and
  append-to-markdown semantics, the single-tool-LLM loop shape, the Strava
  activity-shape mapping.
- **Rewrite:** everything else. Handler → FastAPI + APScheduler; context →
  pure `prompts.py` + `storage/activities.py`; Slack → ntfy; DynamoDB →
  SQLite; S3 → filesystem; Pulumi → compose.
- **Drop:** Pulumi infra, AWS-specific env wiring, the AWS Lambda handler
  shape.

## Open questions (non-blocking)

- Should the post-run review wait a few minutes after Strava activity creation
  in case the user edits the activity name/type immediately after? For now,
  fire immediately; revisit if it's noisy.
- Do we want per-model prompt overrides (e.g., a shorter system prompt for
  smaller local models)? Defer until we have data from the `messages` table
  showing it matters.
