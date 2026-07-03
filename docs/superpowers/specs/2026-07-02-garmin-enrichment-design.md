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
  Connect web API via `python-garminconnect`, accepting its fragility.
- Multi-user / multi-account support.
- Backfilling Garmin data onto historical activities (new/ingested runs only).
- Storing full Garmin time-series streams; we store a compact normalized blob.

## Assumptions & constraints

- **Package:** `python-garminconnect >= 0.3.6`. As of 0.3.6 it no longer
  depends on the now-deprecated `garth`; it authenticates via its own mobile
  SSO flow (`curl_cffi`) and manages/refreshes tokens itself.
- **MFA is enabled** on the Garmin account. Headless `input()`-based MFA is not
  viable in the container, so login is bootstrapped once interactively; the
  saved token session is reused and auto-refreshed thereafter.
- The run **originates on a Garmin device**, so by the time Strava's webhook
  fires (and after `webhook_delay_seconds`), Garmin Connect already holds the
  activity. Matching is therefore high-confidence on start time.
- Garmin auth tokens are long-lived (~1 year). On expiry, a re-bootstrap is
  required; the system surfaces this rather than silently failing.

## Architecture

Enrichment runs at the **ingestion seam** (design option A). In the current
codebase there is exactly **one** place a Strava activity is fetched and
written to the DB: `scheduler._poll_job`, which calls
`StravaClient.list_recent_since` and `activities_repo.upsert`. The webhook's
`on_create` handler and the manual re-review route (`web.routes`) do **not**
fetch or upsert — they only schedule/run `post_run_review`, which reads the
activity from the DB. The poll is therefore the single, authoritative
ingestion point (and the existing `webhook_delay_seconds` delay before a review
already relies on the poll having populated the row).

Enrichment attaches immediately after `activities_repo.upsert` **in
`_poll_job`**: a best-effort `enrich_activity` step matches and pulls the
corresponding Garmin detail and persists it on the activity row. Coaching jobs
remain source-agnostic and simply read what is stored. Daily wellness is pulled
fresh inside the coaching jobs (it is date-scoped, not activity-scoped).

Because there is a single upsert seam, enrichment is a single touch point.
This inherits the codebase's existing timing model: a webhook-triggered review
sees Garmin data once the poll has ingested (and enriched) that activity, the
same ordering that already governs whether the base activity row exists.

```
 Strava webhook ──► schedule post_run_review (after webhook_delay_seconds)   ← existing
                         (reads activity from DB; does not fetch/upsert)

 _poll_job (poll_cron):                                          ← existing, extended
        │  StravaClient.list_recent_since → for each new activity
        ▼
 activities_repo.upsert(act)                                     ← existing
        │
        ▼
 garmin.enrich.enrich_activity(act)                             ← NEW, best-effort
        │   match by start-time ±tolerance, pull detail, normalize
        ▼
 activities_repo.set_garmin(id, blob)  → stores garmin_json     ← NEW
        │
        ▼
 (later) post_run_review reads act (incl. garmin_json) → prompt ← existing job, extended

 morning_checkin → garmin.client.get_wellness(today) → prompt   ← NEW pull in existing job
```

### New package: `src/coach/garmin/`

Mirrors `src/coach/strava/` in style (thin client, plain-dict outputs via
`_map_*` normalizers).

- **`client.py` — `GarminClient`**
  - Constructed with `email`, `password` (SecretStr), and `tokenstore` dir.
  - `login()` — calls `python-garminconnect`'s `Garmin(...).login(tokenstore)`;
    reuses/refreshes the saved token. Never prompts for MFA (bootstrap owns
    that). Raises a typed `GarminAuthError` if no valid token exists.
  - `find_activity_near(start_iso: str, duration_min: int, tolerance_sec: int)
    -> dict | None` — lists recent Garmin activities around `start_iso`,
    selects the nearest whose start is within `tolerance_sec`, tie-breaking on
    closest duration; returns a normalized detail dict or `None`.
  - `get_wellness(date_iso: str) -> dict` — pulls training readiness, HRV
    status, sleep summary, body battery, and resting HR for the date;
    normalizes to a compact dict. Missing sub-metrics are omitted, not errored.
  - `_map_activity_detail` / `_map_wellness` — normalizers, analogous to
    Strava's `_map_activity`.

- **`enrich.py`**
  - `enrich_activity(client: GarminClient, strava_act: dict, *, tolerance_sec:
    int) -> dict` — orchestrates the match + pull, returns the normalized
    Garmin blob or `{}`. **Catches all Garmin/network exceptions**, logs a
    structured warning, and returns `{}` so ingestion always proceeds.

- **`bootstrap.py`**
  - `python -m coach.garmin.bootstrap` — one-time interactive login. Reads
    email/password from env, calls `Garmin(..., prompt_mfa=lambda: input("MFA
    code: "))`, and writes the token session (`garmin_tokens.json`, mode 0600)
    into the tokenstore dir on the data volume. Run via
    `docker compose exec coach-app python -m coach.garmin.bootstrap`.

### Client injection / factory

Following the `_llm_chat_factory` / `_notify_factory` pattern in
`scheduler.py`, add a `_garmin_client_factory(settings)` that returns a
logged-in `GarminClient`, or `None` when `garmin_enabled` is false or auth is
unavailable. Ingestion and coaching call sites accept an injected client (or a
factory), which keeps them testable with a fake.

## Data model

Migration **`0002_garmin.sql`** adds one nullable column to `activities`:

```sql
ALTER TABLE activities ADD COLUMN garmin_json TEXT;
```

- `activities_repo.upsert` is unchanged (does not clear `garmin_json`).
- New `activities_repo.set_garmin(db_path, activity_id, blob: dict | None)`
  writes the JSON (or leaves NULL for `{}`/`None`).
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
optional Garmin section, rendered **only when data is present**:

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
| `garmin_email` | str | — | Garmin Connect account email. |
| `garmin_password` | SecretStr | — | Never logged. |
| `garmin_tokenstore` | Path | `data/garmin` | Token session dir on data volume. |
| `garmin_match_tolerance_sec` | int | `300` | Start-time match window (±5 min). |

When `garmin_enabled` is false, no Garmin code paths run and no Garmin config
is required.

## Failure isolation & observability

- Every Garmin network call is wrapped; failures log a structured warning and
  degrade to Strava-only. `enrich_activity` returning `{}` is normal.
- A `GarminAuthError` (missing/expired token) sets a health flag surfaced on
  `/settings` and reflected in `/healthz` as
  `{"garmin": "ok" | "needs_reauth" | "disabled"}`. It does **not** make
  `/healthz` fail overall.
- Structured log events: `garmin.enrich.matched`, `garmin.enrich.no_match`,
  `garmin.enrich.error`, `garmin.wellness.error`, `garmin.auth.needs_reauth`.

## Testing strategy

`respx` cannot intercept `python-garminconnect` (it uses `curl_cffi`, not
`httpx`), so tests inject a **fake Garmin client** through the factory seam
rather than mocking HTTP.

- `find_activity_near`: matches within tolerance; picks nearest on start then
  duration; returns `None` outside tolerance / empty list.
- `enrich_activity`: merges a match into the blob; returns `{}` and logs on a
  raised exception (graceful degradation); returns `{}` on no match.
- `activities_repo`: `set_garmin` round-trips; `get`/`recent` expose `garmin`;
  `upsert` does not clobber an existing `garmin_json`.
- Prompts: Garmin section appears when data present; output unchanged when
  absent (guards existing behavior).
- Config: `garmin_enabled=false` requires no Garmin secrets and runs no paths.
- Migration `0002` applies cleanly on an existing `0001` database.

## Operational notes (README additions)

1. `pip`/`uv add python-garminconnect`.
2. Set `garmin_enabled=true` and Garmin creds in `.env`.
3. One-time bootstrap (interactive, for MFA):
   `docker compose exec coach-app python -m coach.garmin.bootstrap`
   — enter the emailed/authenticator MFA code once; a token is written to
   `data/garmin/`.
4. Restart; enrichment now runs automatically. Re-run the bootstrap if
   `/settings` reports `garmin: needs_reauth` (token expired, ~yearly).

## Security

- `garmin_password` is a `SecretStr`, never logged.
- The token session lives on the data volume at `0600`; it is included in the
  nightly backup bundle alongside `memory/` and the DB.
- No new inbound network surface — Garmin is outbound-only, polled/pulled.
```
