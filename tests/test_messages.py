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
