from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from morphd import pricing, sanitize, store
from morphd.config import Settings
from morphd.contract import TaskContract, TaskStore, UserTurn

REPO = Path(__file__).resolve().parent.parent
FAKES = Path(__file__).resolve().parent / "fakes"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir(parents=True)
    price = yaml.safe_load((REPO / "morphd" / "pricing.yaml").read_text(encoding="utf-8"))
    price["confirmed"] = True
    price_path = tmp_path / "pricing.yaml"
    price_path.write_text(yaml.safe_dump(price, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("MORPHD_HOME", str(home))
    monkeypatch.setenv("MORPHD_TASKS", str(tasks_root))
    monkeypatch.setenv("MORPHD_PRICING", str(price_path))
    monkeypatch.setenv("MORPHD_CONTAINER_REQUIRED", "0")
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-" + "test-secret-value")
    fake_bins = tmp_path / "fake-bin"
    fake_bins.mkdir()
    for name in ("dsh", "codex", "claude"):
        wrapper = fake_bins / f"{name}.cmd"
        contents = f'@echo off\r\n"{sys.executable}" "{FAKES / name}" %*\r\n'
        wrapper.write_bytes(contents.encode("utf-8"))
        monkeypatch.setenv(f"MORPHD_{name.upper() if name != 'claude' else 'CLAUDE'}_BIN", str(wrapper))
    monkeypatch.setenv("MORPHD_DSH_DUMP_CHECK", "1")
    monkeypatch.setenv("MORPHD_CLAUDE_MODEL", "fake-model")
    monkeypatch.setenv("MORPHD_CODEX_MODEL", "fake-model")
    monkeypatch.delenv("FAKE_DSH_REQUIRE_MARKER", raising=False)
    monkeypatch.delenv("FAKE_APPROVALS", raising=False)
    monkeypatch.delenv("FAKE_TOOL_LOOP", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_UNKNOWN", raising=False)
    settings = Settings.from_env()
    return {
        "settings": settings,
        "home": home,
        "tasks_root": tasks_root,
        "price": pricing.Pricing.load(settings.pricing_path),
        "monkeypatch": monkeypatch,
        "tmp": tmp_path,
    }


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "source-workspace"
    ws.mkdir()
    (ws / "README.md").write_text("# Demo workspace\n\nProduce hello.txt.\n", encoding="utf-8")
    return ws


@pytest.fixture
def add_task(env, workspace):
    def _add(
        name: str = "demo-1",
        *,
        instruction: str = "Write hello.txt with the word hello.",
        turns: list[UserTurn] | None = None,
        checks: list[dict] | None = None,
        expected: list[str] | None = None,
        domain: str = "demo",
        ws: Path | None = None,
        split: str | None = None,
    ) -> str:
        store_ = TaskStore(env["settings"].tasks_root)
        source = ws or workspace
        staged = env["tmp"] / f"staged-{name}"
        if staged.exists():
            shutil.rmtree(staged)
        sanitize.sanitize_tree(source, staged)
        digest = store.tree_hash(staged)
        task = TaskContract.new(
            task_ref=name,
            instruction=instruction,
            workspace_sha256=digest,
            domain=domain,
            checks=checks
            if checks is not None
            else [
                {"type": "file_exists", "path": "hello.txt"},
                {"type": "file_contains", "path": "hello.txt", "text": "hello"},
            ],
            user_turns=turns,
            expected_deliverables=expected if expected is not None else ["hello.txt"],
        )
        store_.add_task(task, staged)
        if split:
            existing = {}
            if store_.split_path().is_file():
                existing = store_.assignments()
            existing[task.task_ref] = split
            if len(existing) == len(store_.all()):
                store_.write_splits(existing, force=True)
        return task.task_ref

    return _add


@pytest.fixture
def task_store(env) -> TaskStore:
    return TaskStore(env["settings"].tasks_root)


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_session(path: Path, *, sid: str, cwd: Path, events: list[dict], compressed: bool = True) -> None:
    import zstandard

    header = {
        "type": "session",
        "version": 3,
        "id": sid,
        "createdAt": 1,
        "cwd": str(cwd),
        "isSeeded": False,
        "delegationDepth": 0,
    }
    rows = [header, *events]
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if compressed:
        path.write_bytes(zstandard.ZstdCompressor().compress(raw))
    else:
        path.write_bytes(raw)
