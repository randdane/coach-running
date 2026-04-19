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
        # Requires config to be read lazily (get_settings() called inside functions, not at import time).
        monkeypatch.setenv("DATA_DIR", str(root))
        yield root
