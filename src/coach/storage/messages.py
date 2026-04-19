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
