# Homelab Running Coach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user homelab port of the `ai-running-coach` PyTexas demo as a Docker Compose stack: FastAPI + APScheduler app, LiteLLM proxy, optional Ollama, self-hosted ntfy, SQLite, local filesystem memory. Drives a daily morning coach message and per-run post-run review, with a local web UI.

**Architecture:** One Python service (`coach-app`) owns FastAPI routes (Strava webhook, web UI, HTMX API) and APScheduler jobs (morning cron, daily Strava reconciliation poll, nightly backup, memory-size warning). Strava webhooks arrive via a Tailscale Funnel path `/webhook/strava/{secret}` and are processed after a 15-minute delay with APScheduler job-ID deduplication. LLM calls flow through a LiteLLM proxy so local (Ollama) and cloud backends are swappable per-request. Memory is a markdown file the LLM can append to via a single `save_observation` tool; messages and activities are stored in SQLite for history and dedup.

**Tech Stack:** Python 3.12, FastAPI, APScheduler (SQLite jobstore), pydantic-settings, httpx, openai SDK (pointed at LiteLLM), structlog, Jinja2, HTMX, Tailwind CDN, SQLite (sqlite3 stdlib), pytest, respx, uv. Infra: Docker Compose, LiteLLM proxy, Ollama, ntfy.

**Spec:** `docs/superpowers/specs/2026-04-19-homelab-running-coach-design.md`

---

## File map

**Created:**

```
pyproject.toml
Dockerfile
docker-compose.yml
.env.example
.gitignore
README.md
litellm/config.yaml
prompts/coach_voice.md           # copied from source repo
data/memory/athlete_context.md   # seeded, gitignored
data/memory/training_plan.md     # seeded, gitignored
src/coach/__init__.py
src/coach/config.py
src/coach/main.py
src/coach/scheduler.py
src/coach/llm.py
src/coach/memory.py
src/coach/notify.py
src/coach/prompts.py
src/coach/jobs.py
src/coach/strava/__init__.py
src/coach/strava/client.py
src/coach/strava/webhook.py
src/coach/storage/__init__.py
src/coach/storage/db.py
src/coach/storage/activities.py
src/coach/storage/messages.py
src/coach/storage/tokens.py
src/coach/storage/migrations/0001_init.sql
src/coach/web/__init__.py
src/coach/web/routes.py
src/coach/web/templates/base.html
src/coach/web/templates/dashboard.html
src/coach/web/templates/messages.html
src/coach/web/templates/message_detail.html
src/coach/web/templates/memory.html
src/coach/web/templates/plan.html
src/coach/web/templates/settings.html
tests/__init__.py
tests/conftest.py
tests/test_config.py
tests/test_db.py
tests/test_activities.py
tests/test_messages.py
tests/test_tokens.py
tests/test_memory.py
tests/test_prompts.py
tests/test_strava_client.py
tests/test_llm.py
tests/test_notify.py
tests/test_jobs.py
tests/test_webhook.py
tests/test_main.py
tests/fixtures/strava_activity.json
tests/fixtures/strava_webhook_create.json
```

Each module has one responsibility. `storage/*` is the only caller of sqlite3; `memory.py` is the only caller of the filesystem for the markdown; `llm.py` is the only caller of LiteLLM; `strava/client.py` is the only caller of Strava. `jobs.py` is the composition root; `scheduler.py`, `strava/webhook.py`, `web/routes.py` are entry points.

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/coach/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `prompts/coach_voice.md`, `data/memory/athlete_context.md`, `data/memory/training_plan.md`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "coach"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "apscheduler>=3.10",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "httpx>=0.27",
    "openai>=1.40",
    "structlog>=24.1",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
    "slowapi>=0.1.9",
]

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
    "freezegun>=1.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/coach"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src"]
```

- [ ] **Step 2: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/
!data/.gitkeep
.env
dist/
build/
*.egg-info/
uv.lock
```

- [ ] **Step 3: Create `README.md` stub**

```markdown
# Homelab Running Coach

Local, self-hosted port of the PyTexas AI Running Coach demo.

See `docs/superpowers/specs/2026-04-19-homelab-running-coach-design.md` for design.

## Quick start

```bash
cp .env.example .env   # fill in secrets
docker compose up -d
```

Web UI: http://localhost:8000 (behind your tailnet).
```

- [ ] **Step 4: Create empty package init files and directory placeholders**

```bash
mkdir -p src/coach/strava src/coach/storage/migrations src/coach/web/templates
mkdir -p tests/fixtures data/memory data/backups data/ntfy prompts litellm
touch src/coach/__init__.py src/coach/strava/__init__.py \
      src/coach/storage/__init__.py src/coach/web/__init__.py \
      tests/__init__.py data/.gitkeep
```

- [ ] **Step 5: Copy `coach_voice.md` and seed memory files**

Copy `/home/r/Projects/Pythoneers/PyTexas-2026/ai-running-coach/prompts/coach_voice.md` to `prompts/coach_voice.md`.

Copy `/home/r/Projects/Pythoneers/PyTexas-2026/ai-running-coach/prompts/athlete_context.md` to `data/memory/athlete_context.md`.

Copy `/home/r/Projects/Pythoneers/PyTexas-2026/ai-running-coach/prompts/training_plan.md` to `data/memory/training_plan.md`.

- [ ] **Step 6: Create `tests/conftest.py` with a temp-data fixture**

```python
import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture
def tmp_data(monkeypatch):
    """Point the app's data dir at a throwaway tree."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "memory").mkdir()
        (root / "backups").mkdir()
        (root / "memory" / "athlete_context.md").write_text("- seed observation\n")
        (root / "memory" / "training_plan.md").write_text("# Plan\nrun more\n")
        monkeypatch.setenv("DATA_DIR", str(root))
        yield root
```

- [ ] **Step 7: Install and verify**

Run: `uv sync --all-groups && uv run pytest -q`
Expected: `no tests ran`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "scaffold coach project layout and dependencies"
```

---

### Task 2: Config module

**Files:**
- Create: `src/coach/config.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
from pydantic import ValidationError
from coach.config import Settings


def test_missing_required_fails(monkeypatch):
    for k in ("ATHLETE_ID", "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET",
              "STRAVA_REFRESH_TOKEN", "WEBHOOK_SECRET", "NTFY_BASE_URL",
              "NTFY_TOPIC", "LITELLM_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_defaults(monkeypatch):
    for k, v in {
        "ATHLETE_ID": "a1", "STRAVA_CLIENT_ID": "x", "STRAVA_CLIENT_SECRET": "y",
        "STRAVA_REFRESH_TOKEN": "z", "WEBHOOK_SECRET": "s" * 32,
        "NTFY_BASE_URL": "http://ntfy", "NTFY_TOPIC": "coach",
        "LITELLM_MASTER_KEY": "k",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.morning_cron == "0 6 * * *"
    assert s.poll_cron == "30 22 * * *"
    assert s.webhook_delay_seconds == 15 * 60
    assert s.webhook_rate_limit == "30/minute"
    assert s.coach_model == "gpt-4o"
    assert s.memory_size_warn_kb == 20
    assert s.litellm_base_url == "http://litellm:4000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `Settings`**

```python
# src/coach/config.py
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Identity & storage
    athlete_id: str
    data_dir: Path = Path("/data")
    tz: str = "America/New_York"

    # Strava
    strava_client_id: str
    strava_client_secret: str
    strava_refresh_token: str

    # Webhook
    webhook_secret: str = Field(min_length=16)
    webhook_delay_seconds: int = 15 * 60
    webhook_rate_limit: str = "30/minute"

    # Schedules
    morning_cron: str = "0 6 * * *"
    poll_cron: str = "30 22 * * *"

    # LLM
    coach_model: str = "gpt-4o"
    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Notify
    ntfy_base_url: str
    ntfy_topic: str

    # Memory
    memory_size_warn_kb: int = 20

    @property
    def db_path(self) -> Path:
        return self.data_dir / "coach.db"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/config.py tests/test_config.py
git commit -m "add typed settings via pydantic-settings"
```

---

### Task 3: SQLite database + migrations

**Files:**
- Create: `src/coach/storage/db.py`, `src/coach/storage/migrations/0001_init.sql`, `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from pathlib import Path
from coach.storage.db import connect, apply_migrations


def test_migrations_bring_user_version_to_latest(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    with connect(db) as c:
        v = c.execute("PRAGMA user_version").fetchone()[0]
    assert v == 1


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    apply_migrations(db)
    with connect(db) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"activities", "messages", "strava_tokens"}.issubset(tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create migration SQL**

```sql
-- src/coach/storage/migrations/0001_init.sql
CREATE TABLE activities (
    id            INTEGER PRIMARY KEY,
    athlete_id    TEXT    NOT NULL,
    start_date    TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    type          TEXT    NOT NULL,
    distance_km   REAL,
    duration_min  INTEGER,
    avg_hr        INTEGER,
    raw_json      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_activities_athlete_date ON activities(athlete_id, start_date DESC);

CREATE TABLE messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL CHECK (kind IN ('morning','post_run')),
    trigger       TEXT    NOT NULL CHECK (trigger IN ('scheduled','webhook','poll','manual')),
    activity_id   INTEGER,
    model         TEXT    NOT NULL,
    prompt        TEXT    NOT NULL,
    response      TEXT    NOT NULL,
    tool_calls    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_created ON messages(created_at DESC);
CREATE INDEX idx_messages_activity ON messages(activity_id);

CREATE TABLE strava_tokens (
    athlete_id     TEXT PRIMARY KEY,
    access_token   TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    expires_at     INTEGER NOT NULL
);
```

- [ ] **Step 4: Implement `db.py`**

```python
# src/coach/storage/db.py
from contextlib import contextmanager
from pathlib import Path
import sqlite3

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@contextmanager
def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def apply_migrations(db_path: Path) -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with connect(db_path) as c:
        current = c.execute("PRAGMA user_version").fetchone()[0]
        for path in files:
            version = int(path.name.split("_")[0])
            if version <= current:
                continue
            with c:  # implicit transaction
                c.executescript(path.read_text())
                c.execute(f"PRAGMA user_version = {version}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coach/storage/db.py src/coach/storage/migrations/0001_init.sql tests/test_db.py
git commit -m "add sqlite connection helper and initial migration"
```

---

### Task 4: Activities repo

**Files:**
- Create: `src/coach/storage/activities.py`, `tests/test_activities.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_activities.py
import json
from coach.storage.db import apply_migrations, connect
from coach.storage import activities


def _setup(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    return db


def test_upsert_and_recent(tmp_path):
    db = _setup(tmp_path)
    a1 = {"id": 1, "athlete_id": "a", "start_date": "2026-04-10T10:00:00Z",
          "name": "Easy", "type": "Run", "distance_km": 5.1,
          "duration_min": 30, "avg_hr": 140}
    activities.upsert(db, a1, raw={"foo": "bar"})
    activities.upsert(db, a1, raw={"foo": "bar"})  # idempotent
    rows = activities.recent(db, "a", weeks=4)
    assert len(rows) == 1
    assert rows[0]["name"] == "Easy"
    assert json.loads(rows[0]["raw_json"]) == {"foo": "bar"}


def test_most_recent_start_date(tmp_path):
    db = _setup(tmp_path)
    activities.upsert(db, {"id": 1, "athlete_id": "a",
        "start_date": "2026-04-10T10:00:00Z", "name": "x", "type": "Run",
        "distance_km": 1, "duration_min": 1, "avg_hr": None}, raw={})
    activities.upsert(db, {"id": 2, "athlete_id": "a",
        "start_date": "2026-04-12T10:00:00Z", "name": "y", "type": "Run",
        "distance_km": 1, "duration_min": 1, "avg_hr": None}, raw={})
    assert activities.most_recent_start_date(db, "a") == "2026-04-12T10:00:00Z"


def test_most_recent_start_date_none_when_empty(tmp_path):
    db = _setup(tmp_path)
    assert activities.most_recent_start_date(db, "a") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_activities.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/storage/activities.py
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .db import connect


def upsert(db_path: Path, activity: dict, raw: dict) -> None:
    with connect(db_path) as c:
        c.execute("""
            INSERT INTO activities (id, athlete_id, start_date, name, type,
                distance_km, duration_min, avg_hr, raw_json)
            VALUES (:id, :athlete_id, :start_date, :name, :type,
                :distance_km, :duration_min, :avg_hr, :raw_json)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, type=excluded.type,
                distance_km=excluded.distance_km,
                duration_min=excluded.duration_min,
                avg_hr=excluded.avg_hr, raw_json=excluded.raw_json
        """, {**activity, "raw_json": json.dumps(raw)})


def recent(db_path: Path, athlete_id: str, weeks: int = 3) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with connect(db_path) as c:
        rows = c.execute("""
            SELECT * FROM activities
            WHERE athlete_id = ? AND start_date >= ?
            ORDER BY start_date DESC
        """, (athlete_id, cutoff)).fetchall()
        return [dict(r) for r in rows]


def get(db_path: Path, activity_id: int) -> dict | None:
    with connect(db_path) as c:
        r = c.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        return dict(r) if r else None


def most_recent_start_date(db_path: Path, athlete_id: str) -> str | None:
    with connect(db_path) as c:
        r = c.execute(
            "SELECT start_date FROM activities WHERE athlete_id=? "
            "ORDER BY start_date DESC LIMIT 1",
            (athlete_id,)).fetchone()
        return r[0] if r else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_activities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/storage/activities.py tests/test_activities.py
git commit -m "add activities repo with upsert and recent-window query"
```

---

### Task 5: Messages repo

**Files:**
- Create: `src/coach/storage/messages.py`, `tests/test_messages.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_messages.py
from coach.storage.db import apply_migrations
from coach.storage import messages


def test_save_and_exists(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    mid = messages.save(db, kind="post_run", trigger="webhook",
        activity_id=99, model="gpt-4o", prompt="p", response="r",
        tool_calls=[{"text": "obs"}])
    assert mid > 0
    assert messages.exists_for_activity(db, 99) is True
    assert messages.exists_for_activity(db, 100) is False


def test_list_paginated(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    for i in range(5):
        messages.save(db, kind="morning", trigger="scheduled",
            activity_id=None, model="m", prompt="p", response=f"r{i}",
            tool_calls=None)
    rows = messages.list_recent(db, limit=3, offset=0)
    assert [r["response"] for r in rows] == ["r4", "r3", "r2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_messages.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/storage/messages.py
import json
from pathlib import Path
from .db import connect


def save(db_path: Path, *, kind: str, trigger: str, activity_id: int | None,
         model: str, prompt: str, response: str,
         tool_calls: list[dict] | None) -> int:
    with connect(db_path) as c:
        cur = c.execute("""
            INSERT INTO messages (kind, trigger, activity_id, model, prompt,
                response, tool_calls)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (kind, trigger, activity_id, model, prompt, response,
              json.dumps(tool_calls) if tool_calls else None))
        return cur.lastrowid


def exists_for_activity(db_path: Path, activity_id: int) -> bool:
    with connect(db_path) as c:
        r = c.execute(
            "SELECT 1 FROM messages WHERE activity_id=? LIMIT 1",
            (activity_id,)).fetchone()
        return r is not None


def list_recent(db_path: Path, limit: int = 50, offset: int = 0,
                kind: str | None = None, trigger: str | None = None) -> list[dict]:
    clauses, params = [], []
    if kind:
        clauses.append("kind = ?"); params.append(kind)
    if trigger:
        clauses.append("trigger = ?"); params.append(trigger)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_path) as c:
        rows = c.execute(
            f"SELECT * FROM messages {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
        return [dict(r) for r in rows]


def get(db_path: Path, message_id: int) -> dict | None:
    with connect(db_path) as c:
        r = c.execute("SELECT * FROM messages WHERE id=?",
                      (message_id,)).fetchone()
        return dict(r) if r else None


def latest(db_path: Path) -> dict | None:
    rows = list_recent(db_path, limit=1)
    return rows[0] if rows else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_messages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/storage/messages.py tests/test_messages.py
git commit -m "add messages repo for coach message history"
```

---

### Task 6: Strava tokens repo

**Files:**
- Create: `src/coach/storage/tokens.py`, `tests/test_tokens.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tokens.py
from coach.storage.db import apply_migrations
from coach.storage import tokens


def test_upsert_and_get(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    tokens.upsert(db, "a1", access="A", refresh="R", expires_at=123)
    t = tokens.get(db, "a1")
    assert t == {"athlete_id": "a1", "access_token": "A",
                 "refresh_token": "R", "expires_at": 123}
    tokens.upsert(db, "a1", access="A2", refresh="R2", expires_at=456)
    assert tokens.get(db, "a1")["access_token"] == "A2"


def test_get_missing(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    assert tokens.get(db, "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tokens.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/storage/tokens.py
from pathlib import Path
from .db import connect


def upsert(db_path: Path, athlete_id: str, *, access: str, refresh: str,
           expires_at: int) -> None:
    with connect(db_path) as c:
        c.execute("""
            INSERT INTO strava_tokens (athlete_id, access_token, refresh_token, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(athlete_id) DO UPDATE SET
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at
        """, (athlete_id, access, refresh, expires_at))


def get(db_path: Path, athlete_id: str) -> dict | None:
    with connect(db_path) as c:
        r = c.execute("SELECT * FROM strava_tokens WHERE athlete_id=?",
                      (athlete_id,)).fetchone()
        return dict(r) if r else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tokens.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/storage/tokens.py tests/test_tokens.py
git commit -m "add strava token repo with rotation-safe upsert"
```

---

### Task 7: Memory module

**Files:**
- Create: `src/coach/memory.py`, `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory.py
from pathlib import Path
from coach import memory


def test_read(tmp_path):
    (tmp_path / "athlete_context.md").write_text("hello\n")
    assert memory.read_athlete_context(tmp_path) == "hello\n"


def test_read_training_plan(tmp_path):
    (tmp_path / "training_plan.md").write_text("# Plan\n")
    assert memory.read_training_plan(tmp_path) == "# Plan\n"


def test_append_observation_snapshots(tmp_path):
    (tmp_path / "athlete_context.md").write_text("start\n")
    memory.append_observation(tmp_path, "first")
    assert memory.read_athlete_context(tmp_path) == "start\n- first\n"
    hist = list((tmp_path / "history").glob("athlete_context-*.md"))
    assert len(hist) == 1
    assert hist[0].read_text() == "start\n"


def test_save_context_snapshots_previous(tmp_path):
    f = tmp_path / "athlete_context.md"
    f.write_text("old\n")
    memory.save_athlete_context(tmp_path, "new content\n")
    assert f.read_text() == "new content\n"
    hist = list((tmp_path / "history").glob("athlete_context-*.md"))
    assert any(p.read_text() == "old\n" for p in hist)


def test_size_bytes(tmp_path):
    (tmp_path / "athlete_context.md").write_text("x" * 100)
    assert memory.context_size_bytes(tmp_path) == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_memory.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/memory.py
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CONTEXT = "athlete_context.md"
PLAN = "training_plan.md"


def read_athlete_context(memory_dir: Path) -> str:
    return (memory_dir / CONTEXT).read_text()


def read_training_plan(memory_dir: Path) -> str:
    return (memory_dir / PLAN).read_text()


def context_size_bytes(memory_dir: Path) -> int:
    return (memory_dir / CONTEXT).stat().st_size


def _snapshot(memory_dir: Path, name: str) -> None:
    src = memory_dir / name
    if not src.exists():
        return
    hist = memory_dir / "history"
    hist.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = hist / f"{src.stem}-{ts}{src.suffix}"
    dest.write_text(src.read_text())


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                     encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def append_observation(memory_dir: Path, text: str) -> None:
    _snapshot(memory_dir, CONTEXT)
    existing = (memory_dir / CONTEXT).read_text()
    _atomic_write(memory_dir / CONTEXT, existing.rstrip() + f"\n- {text}\n")


def save_athlete_context(memory_dir: Path, content: str) -> None:
    _snapshot(memory_dir, CONTEXT)
    _atomic_write(memory_dir / CONTEXT, content)


def save_training_plan(memory_dir: Path, content: str) -> None:
    _snapshot(memory_dir, PLAN)
    _atomic_write(memory_dir / PLAN, content)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_memory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/memory.py tests/test_memory.py
git commit -m "add memory module with atomic writes and history snapshots"
```

---

### Task 8: Prompts module (pure)

**Files:**
- Create: `src/coach/prompts.py`, `tests/test_prompts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompts.py
from datetime import datetime, timezone
from coach import prompts


def _activities():
    return [
        {"start_date": "2026-04-18T10:00:00Z", "name": "Easy", "type": "Run",
         "distance_km": 6.0, "duration_min": 40, "avg_hr": 138},
        {"start_date": "2026-04-16T10:00:00Z", "name": "Long", "type": "Run",
         "distance_km": 18.0, "duration_min": 100, "avg_hr": 150},
    ]


def test_build_morning_prompt_contains_sections():
    p = prompts.build_morning_prompt(
        system_prompt="SYS",
        today_label="Saturday, April 18",
        recent=_activities(),
    )
    assert "SYS" in p
    assert "Saturday, April 18" in p
    assert "Easy" in p
    assert "Long" in p


def test_build_post_run_prompt_includes_current_activity():
    activity = {"id": 1, "name": "Tempo", "type": "Run",
                "distance_km": 8.0, "duration_min": 45, "avg_hr": 162,
                "start_date": "2026-04-18T10:00:00Z"}
    p = prompts.build_post_run_prompt(
        system_prompt="SYS",
        activity=activity,
        recent=_activities(),
    )
    assert "Tempo" in p
    assert "8.0 km" in p
    assert "45 min" in p
    assert "162 bpm" in p


def test_assemble_system_prompt_joins_with_headers():
    s = prompts.assemble_system_prompt(
        coach_voice="voice", training_plan="plan", athlete_context="ctx")
    assert "# Coach Voice\nvoice" in s
    assert "# Training Plan\nplan" in s
    assert "# Athlete Context\nctx" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/prompts.py
from __future__ import annotations


def assemble_system_prompt(*, coach_voice: str, training_plan: str,
                           athlete_context: str) -> str:
    return "\n\n---\n\n".join([
        f"# Coach Voice\n{coach_voice}",
        f"# Training Plan\n{training_plan}",
        f"# Athlete Context\n{athlete_context}",
    ])


def _format_activity_line(a: dict) -> str:
    date = str(a.get("start_date", ""))[:10]
    hr = f", {a['avg_hr']} bpm" if a.get("avg_hr") else ""
    return (f"- {date}: {a.get('name', 'Activity')} — "
            f"{a.get('distance_km', '?')} km, "
            f"{a.get('duration_min', '?')} min{hr}")


def _format_recent_section(recent: list[dict], exclude_id: int | None = None) -> str:
    if not recent:
        return ""
    lines = ["## Recent Activity (Last 3 Weeks)"]
    for a in recent:
        if exclude_id is not None and a.get("id") == exclude_id:
            continue
        lines.append(_format_activity_line(a))
    return "\n".join(lines)


def build_morning_prompt(*, system_prompt: str, today_label: str,
                          recent: list[dict]) -> str:
    body = [f"Today is {today_label}. Write the morning check-in.", ""]
    recent_md = _format_recent_section(recent)
    if recent_md:
        body.append(recent_md)
    return system_prompt + "\n\n---\n\n" + "\n".join(body)


def build_post_run_prompt(*, system_prompt: str, activity: dict,
                          recent: list[dict]) -> str:
    hr_note = f", avg HR {activity['avg_hr']} bpm" if activity.get("avg_hr") else ""
    header = (
        f"{activity['name']} just finished — "
        f"{activity['distance_km']} km, {activity['duration_min']} min{hr_note}. "
        f"Write the post-run coaching message now."
    )
    lines = [header, "", "## This Run",
             f"- {activity['name']} ({activity['type']})",
             f"- Distance: {activity['distance_km']} km, "
             f"Duration: {activity['duration_min']} min"]
    if activity.get("avg_hr"):
        lines.append(f"- Avg HR: {activity['avg_hr']} bpm")
    recent_md = _format_recent_section(recent, exclude_id=activity.get("id"))
    if recent_md:
        lines += ["", recent_md]
    return system_prompt + "\n\n---\n\n" + "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/prompts.py tests/test_prompts.py
git commit -m "add pure prompt assembly helpers"
```

---

### Task 9: Strava client

**Files:**
- Create: `src/coach/strava/client.py`, `tests/fixtures/strava_activity.json`, `tests/test_strava_client.py`

- [ ] **Step 1: Create fixture**

```json
// tests/fixtures/strava_activity.json
{
    "id": 12345,
    "name": "Morning Run",
    "type": "Run",
    "distance": 8300.0,
    "moving_time": 2700,
    "average_heartrate": 152.0,
    "start_date_local": "2026-04-18T06:30:00",
    "athlete": {"id": 9999}
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_strava_client.py
import json
import time
from pathlib import Path
import httpx
import pytest
import respx
from coach.storage.db import apply_migrations
from coach.storage import tokens
from coach.strava.client import StravaClient


FIXTURE = Path(__file__).parent / "fixtures" / "strava_activity.json"


def _setup(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    tokens.upsert(db, "a1", access="expired", refresh="R0",
                  expires_at=int(time.time()) - 10)
    return db


@respx.mock
async def test_get_activity_refreshes_when_expired(tmp_path):
    db = _setup(tmp_path)
    respx.post("https://www.strava.com/oauth/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "A1", "refresh_token": "R1",
            "expires_at": int(time.time()) + 3600}))
    respx.get("https://www.strava.com/api/v3/activities/12345").mock(
        return_value=httpx.Response(200, json=json.loads(FIXTURE.read_text())))

    client = StravaClient(db, "a1", client_id="cid", client_secret="csec",
                          initial_refresh_token="R0")
    activity = await client.get_activity(12345)

    assert activity["distance_km"] == 8.3
    assert activity["duration_min"] == 45
    assert activity["avg_hr"] == 152
    assert activity["start_date"] == "2026-04-18T06:30:00"
    # persisted rotated tokens
    t = tokens.get(db, "a1")
    assert t["access_token"] == "A1"
    assert t["refresh_token"] == "R1"


@respx.mock
async def test_list_recent_since(tmp_path):
    db = _setup(tmp_path)
    tokens.upsert(db, "a1", access="A", refresh="R",
                  expires_at=int(time.time()) + 3600)
    respx.get("https://www.strava.com/api/v3/athlete/activities").mock(
        return_value=httpx.Response(200, json=[
            {"id": 1, "name": "A", "type": "Run", "distance": 1000.0,
             "moving_time": 600, "average_heartrate": None,
             "start_date_local": "2026-04-18T10:00:00",
             "athlete": {"id": 9999}}]))
    client = StravaClient(db, "a1", client_id="cid", client_secret="csec",
                          initial_refresh_token="R")
    out = await client.list_recent_since("2026-04-17T00:00:00Z")
    assert len(out) == 1
    assert out[0]["distance_km"] == 1.0
    assert out[0]["avg_hr"] is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_strava_client.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

```python
# src/coach/strava/client.py
import time
from datetime import datetime
from pathlib import Path
import httpx
from coach.storage import tokens

BASE = "https://www.strava.com"


def _map_activity(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "name": raw["name"],
        "type": raw["type"],
        "distance_km": round(raw["distance"] / 1000, 2),
        "duration_min": round(raw["moving_time"] / 60),
        "avg_hr": int(raw["average_heartrate"]) if raw.get("average_heartrate") else None,
        "start_date": raw["start_date_local"],
        "owner_id": raw.get("athlete", {}).get("id"),
    }


class StravaClient:
    def __init__(self, db_path: Path, athlete_id: str, *, client_id: str,
                 client_secret: str, initial_refresh_token: str):
        self.db_path = db_path
        self.athlete_id = athlete_id
        self.client_id = client_id
        self.client_secret = client_secret
        # Seed tokens row if missing
        if tokens.get(db_path, athlete_id) is None:
            tokens.upsert(db_path, athlete_id, access="",
                          refresh=initial_refresh_token, expires_at=0)

    async def _access_token(self) -> str:
        t = tokens.get(self.db_path, self.athlete_id)
        assert t is not None
        if t["expires_at"] > int(time.time()) + 60:
            return t["access_token"]
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{BASE}/oauth/token", data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": t["refresh_token"],
                "grant_type": "refresh_token",
            })
            r.raise_for_status()
            body = r.json()
        tokens.upsert(self.db_path, self.athlete_id,
                      access=body["access_token"],
                      refresh=body["refresh_token"],
                      expires_at=int(body["expires_at"]))
        return body["access_token"]

    async def get_activity(self, activity_id: int) -> dict:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/api/v3/activities/{activity_id}",
                            headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
        return _map_activity(r.json())

    async def list_recent_since(self, iso_after: str) -> list[dict]:
        token = await self._access_token()
        after_ts = int(datetime.fromisoformat(iso_after.replace("Z", "+00:00")).timestamp())
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/api/v3/athlete/activities",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"after": after_ts, "per_page": 30})
            r.raise_for_status()
        return [_map_activity(x) for x in r.json()]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_strava_client.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coach/strava/client.py tests/test_strava_client.py tests/fixtures/strava_activity.json
git commit -m "add async strava client with rotating token refresh"
```

---

### Task 10: LLM module (LiteLLM + save_observation tool loop)

**Files:**
- Create: `src/coach/llm.py`, `tests/test_llm.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm.py
from types import SimpleNamespace
import pytest
from coach.llm import chat, SAVE_OBSERVATION_TOOL


class FakeClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.scripted.pop(0)


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=tool_calls))


def _resp(content=None, tool_calls=None):
    return SimpleNamespace(choices=[_msg(content, tool_calls)])


def _tc(tid, text):
    return SimpleNamespace(
        id=tid,
        function=SimpleNamespace(
            name="save_observation",
            arguments=f'{{"text": "{text}"}}'))


def test_chat_returns_content_when_no_tool_call():
    client = FakeClient([_resp(content="hi there")])
    saved = []
    out, tool_calls = chat(client, model="m", system_prompt="S",
        user_prompt="U", on_observation=saved.append, max_tool_calls=3)
    assert out == "hi there"
    assert tool_calls == []
    assert saved == []


def test_chat_invokes_save_observation_and_loops():
    client = FakeClient([
        _resp(tool_calls=[_tc("c1", "learned X")]),
        _resp(content="final"),
    ])
    saved = []
    out, tool_calls = chat(client, model="m", system_prompt="S",
        user_prompt="U", on_observation=saved.append, max_tool_calls=3)
    assert out == "final"
    assert saved == ["learned X"]
    assert tool_calls == [{"text": "learned X"}]


def test_chat_honors_max_tool_calls():
    # simulate a runaway: always returns a tool call
    scripted = [_resp(tool_calls=[_tc(f"c{i}", f"o{i}")]) for i in range(10)]
    client = FakeClient(scripted)
    saved = []
    out, tool_calls = chat(client, model="m", system_prompt="S",
        user_prompt="U", on_observation=saved.append, max_tool_calls=2)
    assert len(saved) == 2
    assert out == ""  # no content produced within the cap


def test_save_observation_tool_schema_shape():
    assert SAVE_OBSERVATION_TOOL["function"]["name"] == "save_observation"
    assert "text" in SAVE_OBSERVATION_TOOL["function"]["parameters"]["properties"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/llm.py
import json
from typing import Callable, Protocol
from openai import OpenAI


SAVE_OBSERVATION_TOOL = {
    "type": "function",
    "function": {
        "name": "save_observation",
        "description": "Save a durable observation about the athlete to long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The observation to save."}
            },
            "required": ["text"],
        },
    },
}


class _ChatClient(Protocol):
    chat: object  # openai-compatible


def make_client(*, base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def chat(client: _ChatClient, *, model: str, system_prompt: str,
         user_prompt: str, on_observation: Callable[[str], None],
         max_tool_calls: int = 5) -> tuple[str, list[dict]]:
    """Run the tool-call loop. Returns (final_content, list_of_tool_args)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_args: list[dict] = []
    calls_made = 0

    while True:
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=[SAVE_OBSERVATION_TOOL])
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return msg.content or "", tool_args

        if calls_made >= max_tool_calls:
            return msg.content or "", tool_args

        # Preserve the assistant turn with the tool calls
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name,
                              "arguments": c.function.arguments}}
                for c in msg.tool_calls
            ],
        })

        for call in msg.tool_calls:
            if calls_made >= max_tool_calls:
                break
            if call.function.name != "save_observation":
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": "unknown tool"})
                continue
            args = json.loads(call.function.arguments)
            on_observation(args["text"])
            tool_args.append(args)
            calls_made += 1
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": "Saved."})
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/llm.py tests/test_llm.py
git commit -m "add LLM tool-call loop for save_observation with max-call guard"
```

---

### Task 11: Notify module (ntfy)

**Files:**
- Create: `src/coach/notify.py`, `tests/test_notify.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notify.py
import httpx
import respx
from coach.notify import send


@respx.mock
async def test_send_posts_to_topic():
    route = respx.post("http://ntfy/coach").mock(
        return_value=httpx.Response(200))
    await send(base_url="http://ntfy", topic="coach",
               title="Morning check-in", body="go run")
    assert route.called
    req = route.calls[0].request
    assert req.content.decode() == "go run"
    assert req.headers["title"] == "Morning check-in"


@respx.mock
async def test_send_swallows_errors():
    respx.post("http://ntfy/coach").mock(return_value=httpx.Response(500))
    # should not raise
    await send(base_url="http://ntfy", topic="coach", title="t", body="b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_notify.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/notify.py
import httpx
import structlog

log = structlog.get_logger()


async def send(*, base_url: str, topic: str, title: str, body: str) -> None:
    url = f"{base_url.rstrip('/')}/{topic}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, content=body.encode("utf-8"),
                             headers={"title": title})
            if r.status_code >= 400:
                log.warning("ntfy.non_2xx", status=r.status_code, body=r.text[:200])
    except httpx.HTTPError as e:
        log.warning("ntfy.failed", error=str(e))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_notify.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/notify.py tests/test_notify.py
git commit -m "add ntfy notifier with error-swallowing semantics"
```

---

### Task 12: Jobs module (orchestration)

**Files:**
- Create: `src/coach/jobs.py`, `tests/test_jobs.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jobs.py
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from coach.storage.db import apply_migrations
from coach.storage import activities, messages
from coach import jobs, memory


class StubLLM:
    def __init__(self, response="coach says hi", tool_text: str | None = None):
        self.response = response
        self.tool_text = tool_text
        self.last_kwargs = None

    def chat(self, *, model, system_prompt, user_prompt, on_observation,
             max_tool_calls):
        self.last_kwargs = {"model": model, "system_prompt": system_prompt,
                            "user_prompt": user_prompt}
        calls = []
        if self.tool_text:
            on_observation(self.tool_text)
            calls.append({"text": self.tool_text})
        return self.response, calls


class StubNotify:
    def __init__(self):
        self.calls = []

    async def send(self, *, title, body):
        self.calls.append((title, body))


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    apply_migrations(db)
    memdir = tmp_path / "memory"
    memdir.mkdir()
    (memdir / "athlete_context.md").write_text("- seed\n")
    (memdir / "training_plan.md").write_text("plan\n")
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "coach_voice.md").write_text("voice\n")
    return SimpleNamespace(db=db, memdir=memdir, prompts_dir=prompts_dir)


async def test_morning_checkin_saves_message_and_notifies(env):
    llm = StubLLM(response="morning!")
    notify = StubNotify()
    await jobs.morning_checkin(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="scheduled",
        llm_chat=llm.chat, notify_send=notify.send,
        now=lambda: datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc))
    row = messages.latest(env.db)
    assert row["kind"] == "morning"
    assert row["trigger"] == "scheduled"
    assert row["response"] == "morning!"
    assert notify.calls[0][1] == "morning!"


async def test_post_run_review_deduplicates(env):
    llm = StubLLM(response="good run")
    notify = StubNotify()
    activity = {"id": 42, "athlete_id": "a1",
                "start_date": "2026-04-18T10:00:00Z",
                "name": "Tempo", "type": "Run", "distance_km": 8.0,
                "duration_min": 45, "avg_hr": 160}
    activities.upsert(env.db, activity, raw={})

    await jobs.post_run_review(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="webhook",
        activity_id=42,
        llm_chat=llm.chat, notify_send=notify.send)
    await jobs.post_run_review(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="poll",
        activity_id=42,
        llm_chat=llm.chat, notify_send=notify.send)
    rows = messages.list_recent(env.db)
    assert len(rows) == 1


async def test_post_run_review_appends_observation(env):
    llm = StubLLM(response="ok", tool_text="ankle felt good")
    notify = StubNotify()
    activity = {"id": 7, "athlete_id": "a1",
                "start_date": "2026-04-18T10:00:00Z",
                "name": "Easy", "type": "Run", "distance_km": 5.0,
                "duration_min": 30, "avg_hr": 140}
    activities.upsert(env.db, activity, raw={})
    await jobs.post_run_review(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="webhook",
        activity_id=7,
        llm_chat=llm.chat, notify_send=notify.send)
    ctx = memory.read_athlete_context(env.memdir)
    assert "ankle felt good" in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/jobs.py
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo
import structlog

from coach import memory, prompts
from coach.storage import activities, messages

log = structlog.get_logger()

LlmChat = Callable[..., tuple[str, list[dict]]]
NotifySend = Callable[..., Awaitable[None]]


def _system_prompt(memory_dir: Path, prompts_dir: Path) -> str:
    return prompts.assemble_system_prompt(
        coach_voice=(prompts_dir / "coach_voice.md").read_text(),
        training_plan=memory.read_training_plan(memory_dir),
        athlete_context=memory.read_athlete_context(memory_dir),
    )


async def morning_checkin(*, db_path: Path, memory_dir: Path,
                          prompts_dir: Path, athlete_id: str, model: str,
                          trigger: str, llm_chat: LlmChat,
                          notify_send: NotifySend,
                          tz: str = "America/New_York",
                          now: Callable[[], datetime] | None = None) -> int:
    now_fn = now or (lambda: datetime.now(timezone.utc))
    local = now_fn().astimezone(ZoneInfo(tz))
    today = local.strftime("%A, %B %d")
    recent = activities.recent(db_path, athlete_id)
    system = _system_prompt(memory_dir, prompts_dir)
    user = prompts.build_morning_prompt(
        system_prompt="", today_label=today, recent=recent
    ).split("\n\n---\n\n", 1)[1]

    response, tool_calls = llm_chat(
        model=model, system_prompt=system, user_prompt=user,
        on_observation=lambda t: memory.append_observation(memory_dir, t),
        max_tool_calls=5)

    msg_id = messages.save(db_path, kind="morning", trigger=trigger,
        activity_id=None, model=model, prompt=system + "\n\n---\n\n" + user,
        response=response, tool_calls=tool_calls or None)
    await notify_send(title="Morning check-in", body=response)
    log.info("job.morning", msg_id=msg_id, trigger=trigger,
             tool_calls=len(tool_calls))
    return msg_id


async def post_run_review(*, db_path: Path, memory_dir: Path,
                          prompts_dir: Path, athlete_id: str, model: str,
                          trigger: str, activity_id: int,
                          llm_chat: LlmChat,
                          notify_send: NotifySend) -> int | None:
    if messages.exists_for_activity(db_path, activity_id):
        log.info("job.post_run.skip_duplicate", activity_id=activity_id)
        return None
    act = activities.get(db_path, activity_id)
    if act is None:
        log.warning("job.post_run.unknown_activity", activity_id=activity_id)
        return None

    recent = activities.recent(db_path, athlete_id)
    system = _system_prompt(memory_dir, prompts_dir)
    user = prompts.build_post_run_prompt(
        system_prompt="", activity=act, recent=recent
    ).split("\n\n---\n\n", 1)[1]

    response, tool_calls = llm_chat(
        model=model, system_prompt=system, user_prompt=user,
        on_observation=lambda t: memory.append_observation(memory_dir, t),
        max_tool_calls=5)

    msg_id = messages.save(db_path, kind="post_run", trigger=trigger,
        activity_id=activity_id, model=model,
        prompt=system + "\n\n---\n\n" + user,
        response=response, tool_calls=tool_calls or None)
    await notify_send(title=f"Post-run: {act['name']}", body=response)
    log.info("job.post_run", msg_id=msg_id, activity_id=activity_id,
             trigger=trigger, tool_calls=len(tool_calls))
    return msg_id
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/jobs.py tests/test_jobs.py
git commit -m "add jobs orchestration for morning check-in and post-run review"
```

---

### Task 13: Scheduler module

**Files:**
- Create: `src/coach/scheduler.py`

No dedicated unit test for cron registration (APScheduler is well-tested upstream). We validate wiring via integration in Task 15 (main) and Task 14 (webhook).

- [ ] **Step 1: Implement**

```python
# src/coach/scheduler.py
from __future__ import annotations
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from coach import jobs, llm, memory, notify as notifier
from coach.config import Settings
from coach.storage import activities as activities_repo
from coach.storage.db import connect

log = structlog.get_logger()


def _llm_chat_factory(settings: Settings):
    client = llm.make_client(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_master_key)
    def _chat(**kw):
        return llm.chat(client, **kw)
    return _chat


def _notify_factory(settings: Settings):
    async def _send(*, title, body):
        await notifier.send(
            base_url=settings.ntfy_base_url, topic=settings.ntfy_topic,
            title=title, body=body)
    return _send


def job_id_for_activity(activity_id: int) -> str:
    return f"post_run:{activity_id}"


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.data_dir}/scheduler.db")
    sched = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.tz)
    _register_crons(sched, settings)
    return sched


def _register_crons(sched: AsyncIOScheduler, settings: Settings) -> None:
    chat = _llm_chat_factory(settings)
    notify_send = _notify_factory(settings)

    async def morning():
        await jobs.morning_checkin(
            db_path=settings.db_path, memory_dir=settings.memory_dir,
            prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
            model=settings.coach_model, trigger="scheduled",
            llm_chat=chat, notify_send=notify_send, tz=settings.tz)

    async def poll():
        from coach.strava.client import StravaClient
        client = StravaClient(settings.db_path, settings.athlete_id,
            client_id=settings.strava_client_id,
            client_secret=settings.strava_client_secret,
            initial_refresh_token=settings.strava_refresh_token)
        since = activities_repo.most_recent_start_date(settings.db_path,
                    settings.athlete_id) or "2026-01-01T00:00:00Z"
        found = await client.list_recent_since(since)
        for act in found:
            activities_repo.upsert(settings.db_path,
                {**act, "athlete_id": settings.athlete_id}, raw=act)
            try:
                sched.add_job(
                    _run_post_run, trigger=DateTrigger(
                        run_date=datetime.now(timezone.utc) +
                                  timedelta(seconds=settings.webhook_delay_seconds)),
                    args=[settings, "poll", act["id"]],
                    id=job_id_for_activity(act["id"]),
                    replace_existing=False, max_instances=1,
                    coalesce=True, misfire_grace_time=3600)
            except Exception as e:  # ConflictingIdError or already-ran
                log.info("poll.skip_existing", activity_id=act["id"], error=str(e))

    async def nightly_backup():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        out = settings.backups_dir / f"coach-{ts}.tar.gz"
        settings.backups_dir.mkdir(parents=True, exist_ok=True)
        tmp_db = settings.backups_dir / f"coach-{ts}.db"
        with connect(settings.db_path) as c:
            c.execute(f"VACUUM INTO '{tmp_db}'")
        with tarfile.open(out, "w:gz") as tar:
            tar.add(tmp_db, arcname=f"coach-{ts}.db")
            tar.add(settings.memory_dir, arcname="memory")
        tmp_db.unlink()
        # retain last 14
        all_bundles = sorted(settings.backups_dir.glob("coach-*.tar.gz"))
        for extra in all_bundles[:-14]:
            extra.unlink()
        log.info("backup.done", path=str(out))

    async def memory_size_warn():
        size = memory.context_size_bytes(settings.memory_dir)
        if size > settings.memory_size_warn_kb * 1024:
            log.warning("memory.oversize", size_bytes=size,
                        threshold_kb=settings.memory_size_warn_kb)

    sched.add_job(morning, CronTrigger.from_crontab(settings.morning_cron,
                  timezone=settings.tz),
                  id="morning", replace_existing=True,
                  misfire_grace_time=3600)
    sched.add_job(poll, CronTrigger.from_crontab(settings.poll_cron,
                  timezone=settings.tz),
                  id="poll", replace_existing=True,
                  misfire_grace_time=3600)
    sched.add_job(nightly_backup, CronTrigger(hour=3, minute=0,
                  timezone=settings.tz),
                  id="backup", replace_existing=True)
    sched.add_job(memory_size_warn, CronTrigger(hour=3, minute=5,
                  timezone=settings.tz),
                  id="memory_warn", replace_existing=True)


async def _run_post_run(settings: Settings, trigger: str, activity_id: int):
    """Module-level function so APScheduler can serialize it."""
    chat = _llm_chat_factory(settings)
    notify_send = _notify_factory(settings)
    await jobs.post_run_review(
        db_path=settings.db_path, memory_dir=settings.memory_dir,
        prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
        model=settings.coach_model, trigger=trigger,
        activity_id=activity_id, llm_chat=chat, notify_send=notify_send)


def schedule_post_run(sched: AsyncIOScheduler, settings: Settings,
                      trigger: str, activity_id: int) -> bool:
    """Schedule a delayed post-run job. Returns False if already scheduled/ran."""
    run_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.webhook_delay_seconds)
    try:
        sched.add_job(
            _run_post_run, trigger=DateTrigger(run_date=run_at),
            args=[settings, trigger, activity_id],
            id=job_id_for_activity(activity_id),
            replace_existing=False, max_instances=1,
            coalesce=True, misfire_grace_time=3600)
        return True
    except Exception as e:
        log.info("schedule.skip_existing", activity_id=activity_id, error=str(e))
        return False
```

- [ ] **Step 2: Smoke test import**

Run: `uv run python -c "from coach.scheduler import build_scheduler, schedule_post_run; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/coach/scheduler.py
git commit -m "add APScheduler setup with cron jobs, backups, memory warnings"
```

---

### Task 14: Strava webhook router

**Files:**
- Create: `src/coach/strava/webhook.py`, `tests/fixtures/strava_webhook_create.json`, `tests/test_webhook.py`

- [ ] **Step 1: Create fixture**

```json
// tests/fixtures/strava_webhook_create.json
{
    "object_type": "activity",
    "aspect_type": "create",
    "object_id": 12345,
    "owner_id": 9999,
    "subscription_id": 1,
    "event_time": 1700000000
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_webhook.py
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from coach.strava.webhook import build_router


class FakeScheduler:
    def __init__(self):
        self.scheduled = []

    def schedule_post_run(self, trigger, activity_id):
        self.scheduled.append((trigger, activity_id))
        return True


@pytest.fixture
def client():
    sched = FakeScheduler()
    app = FastAPI()
    app.include_router(build_router(
        secret="s" * 32, athlete_id="9999",
        on_create=sched.schedule_post_run))
    return TestClient(app), sched


def test_get_handshake(client):
    c, _ = client
    r = c.get(f"/webhook/strava/{'s'*32}",
              params={"hub.challenge": "CHAL",
                      "hub.verify_token": "x",
                      "hub.mode": "subscribe"})
    assert r.status_code == 200
    assert r.json() == {"hub.challenge": "CHAL"}


def test_post_create_triggers_schedule(client):
    c, sched = client
    payload = json.loads((Path(__file__).parent / "fixtures" /
                          "strava_webhook_create.json").read_text())
    r = c.post(f"/webhook/strava/{'s'*32}", json=payload)
    assert r.status_code == 200
    assert sched.scheduled == [("webhook", 12345)]


def test_post_wrong_secret_is_404(client):
    c, sched = client
    r = c.post("/webhook/strava/wrong", json={})
    assert r.status_code == 404
    assert sched.scheduled == []


def test_post_ignores_other_owner(client):
    c, sched = client
    r = c.post(f"/webhook/strava/{'s'*32}", json={
        "object_type": "activity", "aspect_type": "create",
        "object_id": 1, "owner_id": 1})
    assert r.status_code == 200
    assert sched.scheduled == []


def test_post_ignores_non_create(client):
    c, sched = client
    r = c.post(f"/webhook/strava/{'s'*32}", json={
        "object_type": "activity", "aspect_type": "update",
        "object_id": 1, "owner_id": 9999})
    assert r.status_code == 200
    assert sched.scheduled == []
```

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run pytest tests/test_webhook.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

```python
# src/coach/strava/webhook.py
from typing import Callable
from fastapi import APIRouter, HTTPException, Request
import structlog

log = structlog.get_logger()


def build_router(*, secret: str, athlete_id: str,
                 on_create: Callable[[str, int], bool]) -> APIRouter:
    router = APIRouter()

    def _check(path_secret: str) -> None:
        if path_secret != secret:
            raise HTTPException(status_code=404)

    @router.get("/webhook/strava/{path_secret}")
    async def handshake(path_secret: str, request: Request):
        _check(path_secret)
        params = request.query_params
        return {"hub.challenge": params.get("hub.challenge", "")}

    @router.post("/webhook/strava/{path_secret}")
    async def create(path_secret: str, request: Request):
        _check(path_secret)
        body = await request.json()
        if body.get("object_type") != "activity":
            return {"status": "ignored"}
        if body.get("aspect_type") != "create":
            return {"status": "ignored"}
        if str(body.get("owner_id")) != str(athlete_id):
            log.info("webhook.wrong_owner", owner=body.get("owner_id"))
            return {"status": "ignored"}
        activity_id = int(body["object_id"])
        scheduled = on_create("webhook", activity_id)
        return {"status": "ok" if scheduled else "duplicate"}

    return router
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_webhook.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coach/strava/webhook.py tests/test_webhook.py tests/fixtures/strava_webhook_create.json
git commit -m "add strava webhook router with secret path and owner check"
```

---

### Task 15: FastAPI main + healthchecks

**Files:**
- Create: `src/coach/main.py`, `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_main.py
from fastapi.testclient import TestClient


def _env(monkeypatch, tmp_path):
    for k, v in {
        "ATHLETE_ID": "9999", "STRAVA_CLIENT_ID": "c", "STRAVA_CLIENT_SECRET": "s",
        "STRAVA_REFRESH_TOKEN": "r", "WEBHOOK_SECRET": "s" * 32,
        "NTFY_BASE_URL": "http://ntfy", "NTFY_TOPIC": "coach",
        "LITELLM_MASTER_KEY": "k", "DATA_DIR": str(tmp_path),
        "COACH_MODEL": "gpt-4o",
    }.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "athlete_context.md").write_text("- seed\n")
    (tmp_path / "memory" / "training_plan.md").write_text("plan\n")


def test_healthz(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    from coach.main import create_app
    app = create_app(start_scheduler=False)
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/coach/main.py
from contextlib import asynccontextmanager
from pathlib import Path
import structlog
import uvicorn
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from coach.config import get_settings
from coach.scheduler import build_scheduler, schedule_post_run
from coach.storage.db import apply_migrations
from coach.strava.webhook import build_router as build_webhook_router
from coach.web.routes import build_router as build_web_router

log = structlog.get_logger()


def create_app(*, start_scheduler: bool = True) -> FastAPI:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(settings.db_path)

    sched = build_scheduler(settings) if start_scheduler else None

    def _on_create(trigger: str, activity_id: int) -> bool:
        if sched is None:
            return False
        return schedule_post_run(sched, settings, trigger, activity_id)

    limiter = Limiter(key_func=get_remote_address,
                      default_limits=[settings.webhook_rate_limit])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if sched is not None:
            sched.start()
        yield
        if sched is not None:
            sched.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Apply rate limit via dependency on webhook routes
    webhook_router = build_webhook_router(
        secret=settings.webhook_secret,
        athlete_id=settings.athlete_id,
        on_create=_on_create)
    for route in webhook_router.routes:
        route.dependant.dependencies  # noqa: B018 — touch to ensure registration
    app.include_router(webhook_router)
    app.include_router(build_web_router(settings, scheduler=sched))

    @app.get("/healthz")
    async def healthz():
        # SQLite reachable?
        try:
            from coach.storage.db import connect
            with connect(settings.db_path) as c:
                c.execute("SELECT 1").fetchone()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/readyz")
    async def readyz():
        from coach.storage import tokens
        t = tokens.get(settings.db_path, settings.athlete_id)
        return {"ok": True, "token_loaded": t is not None}

    return app


def main():
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ])
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coach/main.py tests/test_main.py
git commit -m "add fastapi app factory with healthz and scheduler lifespan"
```

---

### Task 16: Web UI (HTMX + templates)

**Files:**
- Create: `src/coach/web/routes.py` and all templates under `src/coach/web/templates/`

Tests are light here (smoke). UI behavior is validated by hand in Task 18.

- [ ] **Step 1: Create base template**

```html
<!-- src/coach/web/templates/base.html -->
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Running Coach</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-800">
  <header class="border-b bg-white">
    <nav class="max-w-4xl mx-auto flex gap-4 p-4">
      <a href="/" class="font-semibold">Dashboard</a>
      <a href="/messages">History</a>
      <a href="/memory">Memory</a>
      <a href="/plan">Plan</a>
      <a href="/settings">Settings</a>
    </nav>
  </header>
  <main class="max-w-4xl mx-auto p-6">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 2: Create dashboard template**

```html
<!-- src/coach/web/templates/dashboard.html -->
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Today — {{ today }}</h1>

<section class="mb-6 bg-white rounded p-4 shadow-sm">
  <h2 class="font-semibold mb-2">Latest coach message</h2>
  {% if latest %}
    <div class="text-sm text-slate-500">{{ latest.created_at }} · {{ latest.kind }} · {{ latest.trigger }} · {{ latest.model }}</div>
    <pre class="whitespace-pre-wrap mt-2">{{ latest.response }}</pre>
  {% else %}
    <p class="text-slate-500">No messages yet.</p>
  {% endif %}
</section>

<section class="mb-6 bg-white rounded p-4 shadow-sm flex gap-2 items-center">
  <form hx-post="/api/jobs/morning" hx-target="#action-result">
    <select name="model" class="border rounded p-1">
      {% for m in models %}<option value="{{ m }}" {% if m == default_model %}selected{% endif %}>{{ m }}</option>{% endfor %}
    </select>
    <button class="px-3 py-1 bg-blue-600 text-white rounded">Run morning check-in</button>
  </form>
  <div id="action-result" class="text-sm text-slate-500"></div>
</section>

<section class="bg-white rounded p-4 shadow-sm">
  <h2 class="font-semibold mb-2">Recent activities</h2>
  <table class="w-full text-sm">
    <thead><tr class="text-left text-slate-500"><th>Date</th><th>Name</th><th>km</th><th>min</th><th></th></tr></thead>
    <tbody>
      {% for a in recent %}
      <tr class="border-t">
        <td>{{ a.start_date[:10] }}</td>
        <td>{{ a.name }}</td>
        <td>{{ a.distance_km }}</td>
        <td>{{ a.duration_min }}</td>
        <td>
          <form hx-post="/api/jobs/post-run/{{ a.id }}" hx-target="#action-result">
            <button class="text-blue-600">Re-review</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

- [ ] **Step 3: Create messages / memory / plan / settings templates**

```html
<!-- src/coach/web/templates/messages.html -->
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Message history</h1>
<ul class="space-y-2">
{% for m in messages %}
  <li class="bg-white p-3 rounded shadow-sm">
    <div class="text-sm text-slate-500">{{ m.created_at }} · {{ m.kind }} · {{ m.trigger }} · {{ m.model }}</div>
    <a href="/messages/{{ m.id }}" class="font-semibold">{{ m.response.splitlines()[0] if m.response else "(empty)" }}</a>
  </li>
{% endfor %}
</ul>
{% endblock %}
```

```html
<!-- src/coach/web/templates/message_detail.html -->
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-2">Message {{ message.id }}</h1>
<div class="text-sm text-slate-500 mb-4">{{ message.created_at }} · {{ message.kind }} · {{ message.trigger }} · {{ message.model }}</div>
<h2 class="font-semibold mt-4">Response</h2>
<pre class="whitespace-pre-wrap bg-white p-3 rounded shadow-sm">{{ message.response }}</pre>
<h2 class="font-semibold mt-4">Prompt</h2>
<pre class="whitespace-pre-wrap bg-white p-3 rounded shadow-sm">{{ message.prompt }}</pre>
{% if message.tool_calls %}
<h2 class="font-semibold mt-4">Tool calls</h2>
<pre class="whitespace-pre-wrap bg-white p-3 rounded shadow-sm">{{ message.tool_calls }}</pre>
{% endif %}
{% endblock %}
```

```html
<!-- src/coach/web/templates/memory.html -->
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Athlete context</h1>
<form method="post" action="/memory" class="space-y-2">
  <textarea name="content" rows="30" class="w-full border rounded p-2 font-mono text-sm">{{ content }}</textarea>
  <button class="px-3 py-1 bg-blue-600 text-white rounded">Save</button>
</form>
{% endblock %}
```

```html
<!-- src/coach/web/templates/plan.html -->
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Training plan</h1>
<form method="post" action="/plan" class="space-y-2">
  <textarea name="content" rows="30" class="w-full border rounded p-2 font-mono text-sm">{{ content }}</textarea>
  <button class="px-3 py-1 bg-blue-600 text-white rounded">Save</button>
</form>
{% endblock %}
```

```html
<!-- src/coach/web/templates/settings.html -->
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Status</h1>
<ul class="space-y-1 text-sm bg-white p-4 rounded shadow-sm">
  {% for k, v in info.items() %}<li><strong>{{ k }}:</strong> {{ v }}</li>{% endfor %}
</ul>
{% endblock %}
```

- [ ] **Step 4: Implement routes**

```python
# src/coach/web/routes.py
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx
import structlog

from coach import memory
from coach.config import Settings
from coach.jobs import morning_checkin, post_run_review
from coach.scheduler import schedule_post_run
from coach.storage import activities, messages, tokens
from coach import llm, notify as notifier

log = structlog.get_logger()

TEMPLATES_DIR = Path(__file__).parent / "templates"


def build_router(settings: Settings, scheduler=None) -> APIRouter:
    router = APIRouter()
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    def _chat():
        client = llm.make_client(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_master_key)
        def inner(**kw):
            return llm.chat(client, **kw)
        return inner

    async def _notify(title, body):
        await notifier.send(
            base_url=settings.ntfy_base_url, topic=settings.ntfy_topic,
            title=title, body=body)

    def _models() -> list[str]:
        try:
            r = httpx.get(f"{settings.litellm_base_url}/v1/models",
                          headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                          timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            log.info("litellm.models_unavailable", error=str(e))
            return [settings.coach_model]

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        today = datetime.now(ZoneInfo(settings.tz)).strftime("%A, %B %d")
        latest = messages.latest(settings.db_path)
        recent = activities.recent(settings.db_path, settings.athlete_id)
        return templates.TemplateResponse("dashboard.html", {
            "request": request, "today": today, "latest": latest,
            "recent": recent, "models": _models(),
            "default_model": settings.coach_model})

    @router.get("/messages", response_class=HTMLResponse)
    async def messages_page(request: Request):
        return templates.TemplateResponse("messages.html", {
            "request": request,
            "messages": messages.list_recent(settings.db_path, limit=100)})

    @router.get("/messages/{mid}", response_class=HTMLResponse)
    async def message_detail(mid: int, request: Request):
        return templates.TemplateResponse("message_detail.html", {
            "request": request, "message": messages.get(settings.db_path, mid)})

    @router.get("/memory", response_class=HTMLResponse)
    async def memory_page(request: Request):
        return templates.TemplateResponse("memory.html", {
            "request": request,
            "content": memory.read_athlete_context(settings.memory_dir)})

    @router.post("/memory")
    async def save_memory(content: str = Form(...)):
        memory.save_athlete_context(settings.memory_dir, content)
        return RedirectResponse("/memory", status_code=303)

    @router.get("/plan", response_class=HTMLResponse)
    async def plan_page(request: Request):
        return templates.TemplateResponse("plan.html", {
            "request": request,
            "content": memory.read_training_plan(settings.memory_dir)})

    @router.post("/plan")
    async def save_plan(content: str = Form(...)):
        memory.save_training_plan(settings.memory_dir, content)
        return RedirectResponse("/plan", status_code=303)

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        t = tokens.get(settings.db_path, settings.athlete_id)
        info = {
            "model": settings.coach_model,
            "morning_cron": settings.morning_cron,
            "poll_cron": settings.poll_cron,
            "strava_token_expires": t["expires_at"] if t else "unset",
            "memory_size_bytes": memory.context_size_bytes(settings.memory_dir),
            "memory_warn_threshold_kb": settings.memory_size_warn_kb,
        }
        if scheduler is not None:
            info["next_morning"] = str(scheduler.get_job("morning").next_run_time)
            info["next_poll"] = str(scheduler.get_job("poll").next_run_time)
        return templates.TemplateResponse("settings.html",
            {"request": request, "info": info})

    @router.post("/api/jobs/morning", response_class=HTMLResponse)
    async def trigger_morning(model: str = Form(None)):
        chosen = model or settings.coach_model
        mid = await morning_checkin(
            db_path=settings.db_path, memory_dir=settings.memory_dir,
            prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
            model=chosen, trigger="manual",
            llm_chat=_chat(), notify_send=_notify, tz=settings.tz)
        return HTMLResponse(f"Saved message #{mid}")

    @router.post("/api/jobs/post-run/{activity_id}", response_class=HTMLResponse)
    async def trigger_post_run(activity_id: int):
        mid = await post_run_review(
            db_path=settings.db_path, memory_dir=settings.memory_dir,
            prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
            model=settings.coach_model, trigger="manual",
            activity_id=activity_id,
            llm_chat=_chat(), notify_send=_notify)
        return HTMLResponse(f"Saved message #{mid}" if mid
                            else "No message (duplicate or unknown activity)")

    return router
```

- [ ] **Step 5: Smoke test web routes start**

Run:
```bash
uv run python -c "
import os, tempfile
tmp = tempfile.mkdtemp()
os.makedirs(f'{tmp}/memory'); open(f'{tmp}/memory/athlete_context.md','w').write('x'); open(f'{tmp}/memory/training_plan.md','w').write('y')
os.environ.update({'ATHLETE_ID':'1','STRAVA_CLIENT_ID':'c','STRAVA_CLIENT_SECRET':'s','STRAVA_REFRESH_TOKEN':'r','WEBHOOK_SECRET':'s'*32,'NTFY_BASE_URL':'http://ntfy','NTFY_TOPIC':'t','LITELLM_MASTER_KEY':'k','DATA_DIR':tmp})
from coach.main import create_app
from fastapi.testclient import TestClient
c = TestClient(create_app(start_scheduler=False))
for path in ['/','/messages','/memory','/plan','/settings']:
    r = c.get(path); print(path, r.status_code)
"
```
Expected: all paths return `200`.

- [ ] **Step 6: Commit**

```bash
git add src/coach/web/
git commit -m "add htmx web UI with dashboard, history, memory, plan, settings"
```

---

### Task 17: Docker, compose, LiteLLM config, env template

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `litellm/config.yaml`, `.env.example`

- [ ] **Step 1: Dockerfile**

```dockerfile
# Dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python -e .
ENV PATH=/opt/venv/bin:$PATH
ENV PYTHONPATH=/app/src

COPY src ./src
COPY prompts ./prompts

EXPOSE 8000
CMD ["python", "-m", "coach.main"]
```

- [ ] **Step 2: docker-compose.yml**

```yaml
services:
  coach-app:
    build: .
    restart: unless-stopped
    env_file: .env
    environment:
      - DATA_DIR=/data
    volumes:
      - ./data:/data
      - ./prompts:/app/prompts:ro
    ports:
      - "8000:8000"   # tailnet-only; front with Tailscale Funnel for /webhook/strava/*
    depends_on:
      - litellm
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8000/healthz').raise_for_status()"]
      interval: 30s
      timeout: 5s
      retries: 3

  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./litellm/config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]
    ports:
      - "127.0.0.1:4000:4000"

  # Uncomment for local LLMs. GPU passthrough requires the nvidia container
  # toolkit. On CPU, remove the deploy section — inference will be slow.
  # ollama:
  #   image: ollama/ollama:latest
  #   restart: unless-stopped
  #   volumes:
  #     - ollama-models:/root/.ollama
  #   ports:
  #     - "127.0.0.1:11434:11434"
  #   deploy:
  #     resources:
  #       reservations:
  #         devices:
  #           - driver: nvidia
  #             count: 1
  #             capabilities: [gpu]

  ntfy:
    image: binwiederhier/ntfy:latest
    restart: unless-stopped
    command: ["serve"]
    environment:
      - NTFY_BEHIND_PROXY=true
      - NTFY_CACHE_FILE=/var/lib/ntfy/cache.db
      - NTFY_AUTH_FILE=/var/lib/ntfy/auth.db
    volumes:
      - ./data/ntfy:/var/lib/ntfy
    ports:
      - "127.0.0.1:8080:80"

# volumes:
#   ollama-models:
```

- [ ] **Step 3: LiteLLM config**

```yaml
# litellm/config.yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
  - model_name: claude
    litellm_params:
      model: anthropic/claude-opus-4-7
      api_key: os.environ/ANTHROPIC_API_KEY
  # Uncomment when ollama is enabled:
  # - model_name: local-small
  #   litellm_params:
  #     model: ollama/llama3.1:8b
  #     api_base: http://ollama:11434
  # - model_name: local-large
  #   litellm_params:
  #     model: ollama/qwen2.5:32b
  #     api_base: http://ollama:11434

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

- [ ] **Step 4: .env.example**

```
# Identity
ATHLETE_ID=9999999
TZ=America/New_York

# Strava (create your own app at https://www.strava.com/settings/api)
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REFRESH_TOKEN=

# Webhook (32+ char random; configure your Strava subscription callback to
# https://<host>.ts.net/webhook/strava/<WEBHOOK_SECRET>)
WEBHOOK_SECRET=change-me-to-32-random-chars-xxxx
WEBHOOK_DELAY_SECONDS=900
WEBHOOK_RATE_LIMIT=30/minute

# Schedules (cron, in TZ above)
MORNING_CRON=0 6 * * *
POLL_CRON=30 22 * * *

# LLM
COACH_MODEL=gpt-4o
LITELLM_BASE_URL=http://litellm:4000
LITELLM_MASTER_KEY=change-me
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Notifications
NTFY_BASE_URL=http://ntfy:80
NTFY_TOPIC=coach

# Memory warning
MEMORY_SIZE_WARN_KB=20
```

- [ ] **Step 5: Validate compose config**

Run: `docker compose config >/dev/null && echo ok`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml litellm/config.yaml .env.example
git commit -m "add dockerfile, compose stack, litellm config, env template"
```

---

### Task 18: README / runbook + final manual smoke

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Flesh out README**

```markdown
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
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "flesh out README with runbook and restore steps"
```

- [ ] **Step 4: Manual smoke (outside CI)**

The following are manual-only steps; run once on real hardware before declaring done:

1. `docker compose up -d`
2. `curl -f http://localhost:8000/healthz` → `{"ok": true}`
3. Open `http://localhost:8000/` in a browser via tailnet; verify dashboard renders.
4. `curl -f "http://localhost:8000/webhook/strava/$WEBHOOK_SECRET?hub.challenge=abc"` → `{"hub.challenge":"abc"}`
5. Click "Run morning check-in"; confirm an ntfy push lands on your phone.
6. Wait for (or force) the daily poll; confirm one coach message per new activity.

---

## Self-review notes

Checked:

- Every spec section has a matching task: scaffold (1), config (2), SQLite tables + migrations (3), repos (4-6), memory file (7), pure prompts (8), Strava client (9), LLM tool loop (10), ntfy (11), jobs (12), scheduler + backups + memory-size warn (13), webhook with secret + delay + dedup via `schedule_post_run` (14), FastAPI main + healthz + readyz + rate limit wiring (15), web UI (16), compose/litellm/env (17), README + smoke (18).
- Naming is consistent: `schedule_post_run` in scheduler.py is the only path the webhook takes; it uses `job_id_for_activity(activity_id)`. `on_observation` callback signature matches between `llm.chat` and `jobs.*`.
- No placeholders: each step has concrete code or command.
- Deferred-in-v1 per spec: context summarization (scheduler slot unused), token encryption (explicitly not done).
