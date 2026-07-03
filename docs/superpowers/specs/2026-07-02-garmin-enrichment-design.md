# Garmin Connect Enrichment — Design

**Date:** 2026-07-02
**Status:** Draft for review

Add Garmin Connect as a **data-enrichment source** layered on top of the
existing Strava integration. Strava webhooks remain the real-time trigger
("you just ran"); Garmin supplies richer physiology that Strava does not
expose — per-run detail (running dynamics, training effect, accurate HR
zones) and daily recovery context (HRV status, sleep, body battery, resting
HR, training readiness/status). The coach's prompts gain this data when it is
available and degrade cleanly to Strava-only when it is not.

## Goals

- Enrich each Strava-triggered run with the matching Garmin activity's detail.
- Give the morning check-in today's Garmin recovery/readiness context.
- Keep Garmin strictly best-effort: a Garmin outage, auth lapse, or API break
  must never fail a webhook, a coaching run, or the dashboard.
- Fit the existing codebase grain (mirror the `strava/` package seam; reuse
  the ingestion → upsert → coaching-job flow).

## Non-goals

- Replacing Strava. Strava remains the trigger and the canonical activity list.
- Supporting Garmin's official partner (B2B) APIs. We use the unofficial
  Connect web API via the `garminconnect` package, accepting its fragility.
- Multi-user / multi-account support.
- No bulk backfill job for historical activities; a manual re-review may still
  enrich an existing row on demand when its `garmin_json` is NULL.
- Storing full Garmin time-series streams; we store a compact normalized blob.

## Assumptions & constraints

- **Package:** the PyPI distribution is **`garminconnect`** (`>= 0.3.6`;
  imported as `from garminconnect import Garmin`). "python-garminconnect" is
  only the GitHub repo name. As of 0.3.6 it no longer depends on the
  now-deprecated `garth`; it authenticates via its own mobile SSO flow and
  manages/refreshes tokens itself. It pulls `curl_cffi` transitively.
- **The client is synchronous** (`requests`/`curl_cffi`). Our jobs are `async`,
  so **every** Garmin call must run off the loop, via
  `await asyncio.wait_for(asyncio.to_thread(fn, ...), timeout=...)` — note
  `asyncio.to_thread` itself takes no timeout, hence the `wait_for` wrapper.
  Caveat: `wait_for` cancels the *awaiter* on timeout but cannot cancel the
  underlying worker thread, which runs to completion in the default executor;
  this is acceptable for our low call volume. This is a hard requirement,
  unlike the async `httpx`-based `StravaClient`.
- **MFA is enabled** on the Garmin account. Headless `input()`-based MFA is not
  viable in the container, so login is bootstrapped once interactively; the
  saved token session is reused and auto-refreshed thereafter.
- The run **originates on a Garmin device**, so by the time a review runs,
  Garmin Connect already holds the activity. Matching is therefore
  high-confidence on start time.
- Garmin auth tokens are long-lived (~1 year). On expiry, a re-bootstrap is
  required; the system surfaces this rather than silently failing.

## Architecture

Enrichment attaches at the **review boundary** — `jobs.post_run_review` —
which is the single point every trigger funnels through (webhook, poll, and
manual re-review all call it). This also fixes a latent base-app fragility.

**Why not the poll seam:** the current defaults are `poll_cron = "30 22 * * *"`
(once nightly) and `webhook_delay_seconds = 900`. A webhook fires a review 15
minutes after a run, but `_poll_job` — currently the *only* code that fetches
and upserts a Strava activity — does not run until 22:30. So a webhook-driven
`post_run_review` reads the DB, finds no row, logs `unknown_activity`, and
silently no-ops. `StravaClient.get_activity` exists for exactly this fetch but
is **currently unwired** (no caller anywhere in `src/`).

**Fetch-on-demand review (chosen).** `post_run_review` is made responsible for
ensuring the activity exists, then enriching it:

1. `act = activities_repo.get(...)`; if missing, fetch via
   `StravaClient.get_activity(activity_id)` and `upsert` it. This Strava fetch
   is **not** best-effort in the Garmin sense: if it fails (network error, or
   the activity is missing/deleted on Strava), `post_run_review` logs
   `post_run.fetch_failed` and returns `None` — it never raises through the
   scheduled job or web route. (No activity ⇒ nothing to review.)
2. If `garmin_enabled` and `act` has **no `garmin_json` yet** (SQL `NULL`), run
   best-effort `enrich_activity` and `set_garmin` with its result. Enrichment
   is **idempotent for definitive outcomes**: a successful match stores the
   metrics blob, and a clean no-match stores a sentinel
   `{"matched": false, "checked_at": <iso>}` — both make `garmin_json` non-NULL,
   so a later re-review skips Garmin entirely. A **transient failure**
   (exception/timeout) returns `None`, leaves `garmin_json` NULL, and therefore
   *does* allow a future re-review to retry — which is the desired behavior for
   an outage or a not-yet-synced activity.
3. Build the prompt from `act`. The Garmin block renders only when
   `garmin_json` holds a real match (`matched == true`); the sentinel is
   treated as "no Garmin data".

This removes all reliance on poll ordering, makes enrichment deterministic and
single-pointed, and reuses the already-present-but-unused `get_activity`. Daily
wellness is pulled fresh inside the coaching jobs (it is date-scoped, not
activity-scoped).

Because `post_run_review` now needs both a Strava fetch capability and a Garmin
client, these become **injected dependencies at the `jobs.py` boundary**,
supplied by both call sites (`scheduler._run_post_run` and
`web.routes.trigger_post_run`). See "Client injection" below.

```
 Strava webhook ─┐
 _poll_job       ├─► post_run_review(strava_client, garmin_client, activity_id)   ← extended
 manual re-review┘        │
                          ├─ act = activities.get(id)  ── if missing ─►
                          │        StravaClient.get_activity(id) → upsert   ← NEW (wires get_activity)
                          │
                          ├─ if garmin_enabled and garmin_json is NULL:        ← NEW, best-effort
                          │        enrich_activity(act) → set_garmin(id, blob|sentinel)
                          │        (transient failure → None → stays NULL, retried later)
                          │
                          └─ build_post_run_prompt(act incl. garmin) → notify

 morning_checkin(garmin_client) → get_wellness(today) → build_morning_prompt   ← NEW pull
```

### New package: `src/coach/garmin/`

Mirrors `src/coach/strava/` in style (thin client, plain-dict outputs via
`_map_*` normalizers).

- **`client.py` — `GarminClient`** (async-facing wrapper over the sync
  `garminconnect.Garmin`)
  - Constructed with `email`, `password` (SecretStr), and `tokenstore` dir.
  - **All public methods are `async`** and run the underlying sync call as
    `await asyncio.wait_for(asyncio.to_thread(...),
    timeout=garmin_call_timeout_sec)`, so the event loop is never blocked. Every
    call also updates the shared `garmin_status` (see observability).
  - `async login()` — calls `garminconnect.Garmin(...).login(tokenstore)`;
    reuses/refreshes the saved token. Never prompts for MFA (bootstrap owns
    that). Raises a typed `GarminAuthError` if no valid token exists, and sets
    `garmin_status = "needs_reauth"`.
  - `async find_activity_near(start_iso, duration_min, tolerance_sec)
    -> dict | None` — lists recent Garmin activities around `start_iso`,
    selects the nearest whose start is within `tolerance_sec`, tie-breaking on
    closest duration; returns a normalized detail dict or `None`.
  - `async get_wellness(date_iso: str) -> dict` — pulls training readiness, HRV
    status, sleep summary, body battery, and resting HR for the date;
    normalizes to a compact dict. Missing sub-metrics are omitted, not errored.
  - `_map_activity_detail` / `_map_wellness` — normalizers, analogous to
    Strava's `_map_activity`.

- **`enrich.py`**
  - `async enrich_activity(client, strava_act, *, tolerance_sec) -> dict | None`
    — orchestrates the match + pull with three distinct outcomes, so the caller
    can decide what to persist:
    - **match** → the normalized metrics blob (`{"matched": true, ...}`).
    - **clean no-match** (Garmin reachable, no activity within tolerance) → the
      sentinel `{"matched": false, "checked_at": <iso>}`.
    - **transient failure** (any Garmin/network/auth/timeout exception) → `None`,
      after logging a structured warning.
  - The caller persists the return value via `set_garmin` **only when it is not
    `None`**; `None` leaves `garmin_json` NULL so a future re-review retries. In
    all cases the review itself proceeds.

- **`bootstrap.py`**
  - `python -m coach.garmin.bootstrap` — one-time interactive login. Reads
    email/password from env, calls `Garmin(..., prompt_mfa=lambda: input("MFA
    code: "))`, and writes the token session (`garmin_tokens.json`, mode 0600)
    into the tokenstore dir on the data volume. Run via
    `docker compose exec coach-app python -m coach.garmin.bootstrap`.

### Client injection

The dependency lives at the **`jobs.py` boundary**, because both the scheduler
and the web routes call `jobs.post_run_review` / `jobs.morning_checkin`
directly. Both jobs gain two new keyword parameters:

- `strava_client` — used by `post_run_review` for the fetch-on-demand step.
- `garmin_client: GarminClient | None` — `None` disables enrichment/wellness.

Following the `_llm_chat_factory` / `_notify_factory` pattern in
`scheduler.py`, add `_strava_client_factory(settings)` (factoring out the
`StravaClient` construction already inline in `_poll_job`) and
`_garmin_client_factory(settings)` (returns a `GarminClient`, or `None` when
`garmin_enabled` is false). **Both call sites must be updated:**

- `scheduler._run_post_run` and `scheduler._morning_job` pass the factories'
  clients.
- `web.routes.trigger_post_run` and `web.routes.trigger_morning` do the same
  (they currently call the jobs with no such clients).

Passing plain client objects keeps the jobs testable with a fake.

## Data model

Migration **`0002_garmin.sql`** adds one nullable column to `activities`:

```sql
ALTER TABLE activities ADD COLUMN garmin_json TEXT;
```

- `activities_repo.upsert` is unchanged (does not clear `garmin_json`).
- New `activities_repo.set_garmin(db_path, activity_id, blob: dict)` writes the
  JSON verbatim, including the `{"matched": false, ...}` sentinel. The caller
  simply does not call it when enrichment returned `None`, so a NULL
  `garmin_json` unambiguously means "not yet checked / retry allowed".
- `activities_repo.get` and `.recent` parse `garmin_json` into a `garmin` key
  on the returned dict (absent/`None` when not enriched).

Wellness is **not** persisted; it is fetched per coaching run for the relevant
date. (Rationale: it is cheap to fetch, date-scoped, and always wanted "as of
now"; persisting it would add a table and staleness questions for no benefit.)

### Normalized Garmin activity blob (illustrative)

```json
{
  "garmin_activity_id": 1234567890,
  "avg_hr": 152, "max_hr": 171,
  "hr_zones_min": {"z1": 4, "z2": 18, "z3": 12, "z4": 5, "z5": 1},
  "aerobic_te": 3.4, "anaerobic_te": 1.1,
  "avg_cadence": 168, "avg_stride_len_m": 1.12,
  "avg_ground_contact_ms": 244, "avg_vertical_osc_cm": 8.9,
  "matched": true, "match_delta_sec": 42
}
```

### Normalized wellness blob (illustrative)

```json
{
  "date": "2026-07-02",
  "training_readiness": 78, "training_status": "productive",
  "hrv_status": "balanced", "hrv_last_night_ms": 62,
  "resting_hr": 48,
  "sleep_hours": 7.4, "sleep_score": 82,
  "body_battery_start": 84
}
```

## Prompt changes

`prompts.build_post_run_prompt` and `prompts.build_morning_prompt` gain an
optional Garmin section, rendered **only for a real match** (`matched == true`)
— the no-match sentinel and absent/NULL data render nothing:

- **Post-run:** matched-activity block (running dynamics, aerobic/anaerobic
  training effect, accurate HR zones) appended to the activity description.
- **Morning:** today's readiness/recovery block (readiness, training status,
  HRV status, sleep, body battery, resting HR).

When Garmin data is absent the prompts are byte-identical to today's output,
preserving current behavior and tests.

## Configuration

New `Settings` fields (in `config.py`, `.env.example`):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `garmin_enabled` | bool | `false` | Master switch; off = Strava-only. |
| `garmin_email` | `str \| None` | `None` | Garmin Connect account email. |
| `garmin_password` | `SecretStr \| None` | `None` | Never logged. |
| `garmin_match_tolerance_sec` | int | `300` | Start-time match window (±5 min). |
| `garmin_call_timeout_sec` | int | `30` | Per-call `asyncio.wait_for` timeout. |

**`garmin_tokenstore` is a derived `@property`, not a settable field.** Like the
existing `db_path`, `memory_dir`, and `backups_dir`, it returns
`self.data_dir / "garmin"`, so it always resolves under the configured data
volume (`DATA_DIR=/data` in compose) rather than the process cwd.

The email/password fields are **optional** (`| None`) so that with
`garmin_enabled=false` Pydantic does not require them and no Garmin config is
needed. A `model_validator(mode="after")` raises if `garmin_enabled` is true
while `garmin_email` or `garmin_password` is unset. When disabled, no Garmin
code paths run.

## Failure isolation & observability

- Every Garmin network call is wrapped; failures log a structured warning and
  degrade to Strava-only. All three `enrich_activity` outcomes (metrics blob,
  no-match sentinel, `None` on transient failure) are normal.
- **Health state.** FastAPI and APScheduler run in the **same process**
  (`main.build_app` constructs both), so a module-level singleton is sufficient
  — no cross-process channel needed. Add `coach/garmin/status.py` holding a
  `garmin_status` value in `{"disabled", "unknown", "ok", "needs_reauth"}`:
  - Initialized to `"disabled"` when `garmin_enabled` is false, else `"unknown"`.
  - The `GarminClient` wrapper sets `"ok"` after any successful call and
    `"needs_reauth"` on `GarminAuthError`. Transient network errors do **not**
    flip it to `needs_reauth` (they log and leave the last state).
  - `/healthz` adds a `garmin` key with this value but is only overall-`ok`
    based on the DB check as today — a Garmin problem never fails `/healthz`.
  - `/settings` renders the value (e.g. a "Garmin: needs re-auth — run
    bootstrap" banner) so the state is actionable.
  - State is in-memory and resets to `"unknown"` on restart until the first
    Garmin call; this is acceptable (it self-heals on next poll/coaching run).
- Structured log events: `garmin.enrich.matched`, `garmin.enrich.no_match`,
  `garmin.enrich.error`, `garmin.wellness.error`, `garmin.auth.needs_reauth`.

## Testing strategy

`respx` cannot intercept `garminconnect` (it uses `curl_cffi`, not `httpx`), so
tests inject a **fake Garmin client** (an object with the same `async` methods)
through the injection seam rather than mocking HTTP.

- `find_activity_near`: matches within tolerance; picks nearest on start then
  duration; returns `None` outside tolerance / empty list.
- `enrich_activity` three outcomes: match → metrics blob; clean no-match →
  `{"matched": false, ...}` sentinel; raised exception → `None` (logged).
- `post_run_review` fetch-on-demand: when the activity row is missing, it
  fetches via the injected Strava client and upserts (fake client); a failing
  Strava fetch logs `post_run.fetch_failed` and returns `None` without raising.
- `post_run_review` enrichment persistence: a match **and** a no-match sentinel
  both set `garmin_json`, so a re-review skips Garmin; a transient failure
  (`None`) leaves it NULL so a re-review retries; with `garmin_client=None`,
  behavior is unchanged; the sentinel does **not** render a Garmin prompt block.
- `activities_repo`: `set_garmin` round-trips (incl. the sentinel); `get`/
  `recent` expose `garmin`; `upsert` does not clobber an existing `garmin_json`.
- Prompts: Garmin section appears when data present; output unchanged when
  absent (guards existing behavior).
- Config: `garmin_enabled=false` requires no Garmin secrets; the
  `model_validator` raises when `garmin_enabled=true` without email/password.
- `garmin_status` transitions: `ok` on success, `needs_reauth` on
  `GarminAuthError`, unchanged on transient error.
- Migration `0002` applies cleanly on an existing `0001` database.

## Operational notes (README additions)

1. `uv add "garminconnect>=0.3.6"` (pulls `curl_cffi` transitively).
2. Set `garmin_enabled=true` and Garmin creds in `.env`.
3. One-time bootstrap (interactive, for MFA):
   `docker compose exec coach-app python -m coach.garmin.bootstrap`
   — enter the emailed/authenticator MFA code once; a token is written to
   `data/garmin/`.
4. Restart; enrichment now runs automatically. Re-run the bootstrap if
   `/settings` reports `garmin: needs_reauth` (token expired, ~yearly).

## Security

- `garmin_password` is a `SecretStr`, never logged.
- The token session lives on the data volume at `0600`. `_nightly_backup_job`
  is **extended** to add the `garmin_tokenstore` dir to the tar bundle (it
  currently only adds the DB and `memory/`), so a restore does not force a
  re-bootstrap. If the dir is absent, the backup step skips it silently.
- No new inbound network surface — Garmin is outbound-only, polled/pulled.
