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
