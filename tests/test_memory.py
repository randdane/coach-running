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
