from __future__ import annotations

import pytest

from morphd import contract, runtimes, sanitize, security
from morphd.budget import BudgetGuard, Limits


def _adapter(env, runtime="dsh"):
    return runtimes.build_adapter(
        runtime,
        tasks=contract.TaskStore(env["settings"].tasks_root),
        settings=env["settings"],
        price=env["price"],
        guard=BudgetGuard(limits=Limits(usd=10, calls=10)),
        model="deepseek-flash",
    )


def test_sanitize_removes_secrets_and_redacts(tmp_path):
    fake_key = "sk-" + "abcdefghijklmnop"
    src = tmp_path / "src"
    src.mkdir()
    (src / ".env").write_text("DEEPSEEK_API_KEY=" + "sk-" + "supersecret123456" + "\n", encoding="utf-8")
    (src / "notes.txt").write_text("token=" + fake_key + " and hello\n", encoding="utf-8")
    (src / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    (src / "bin.dat").write_bytes(b"\x00\x01" + fake_key.encode())
    (src / "expected").mkdir()
    (src / "expected" / "answer.txt").write_text("answer", encoding="utf-8")
    (src / "task.json").write_text("{}", encoding="utf-8")
    dest = tmp_path / "dest"
    report = sanitize.sanitize_tree(src, dest)
    assert ".env" in report.skipped_secret_files
    assert "id_rsa" in report.skipped_secret_files
    assert not (dest / ".env").exists()
    assert fake_key not in (dest / "notes.txt").read_text(encoding="utf-8")
    assert "[REDACTED:" in (dest / "notes.txt").read_text(encoding="utf-8")
    assert (dest / "bin.dat").read_bytes() == (src / "bin.dat").read_bytes()
    assert not (dest / "expected").exists()
    assert not (dest / "task.json").exists()
    assert report.skipped_leak_paths


def test_scan_python_rejects_dangerous_surfaces():
    bad = "import socket\nprint(1)\n"
    violations = security.scan_python("scripts/x.py", bad)
    assert any("socket" in v for v in violations)
    bad2 = "def f():\n    eval('1')\n"
    assert security.scan_python("scripts/x.py", bad2)
    bad3 = "import os\nos.system('true')\n"
    assert security.scan_python("scripts/x.py", bad3)
    assert security.scan_python("scripts/x.py", "from os import system\n")
    assert security.scan_python("scripts/x.py", "from importlib import import_module\n")
    safe = "from pathlib import Path\n\ndef main():\n    Path('x').write_text('ok')\n\nif __name__ == '__main__':\n    main()\n"
    assert security.scan_python("scripts/x.py", safe) == []


def test_scan_python_flags_module_scope_file_writes():
    # C.3.6: a file write performed at import time is a module-scope side effect even when it
    # is the value of an assignment, not a bare expression.
    assert security.scan_python("scripts/x.py", "from pathlib import Path\np = Path('f').write_text('x')\n")
    assert security.scan_python("scripts/x.py", "from pathlib import Path\nPath('f').touch()\n")
    # The same write inside a function (executed under __main__) stays allowed.
    assert security.scan_python(
        "scripts/x.py",
        "from pathlib import Path\n\ndef go():\n    Path('f').write_text('x')\n",
    ) == []


def test_validate_tree_rejects_offsurface_and_hooks(env):
    adapter = _adapter(env)
    base = adapter._ht.read_tree(adapter.substrate.surface(), adapter.base_harness())
    bad = dict(base)
    bad["cordis.patch.yml"] = "- id: sandbox-policy\n"
    with pytest.raises(ValueError, match="outside the editable surface"):
        adapter.validate_tree(bad)
    codex = _adapter(env, "codex")
    tree = codex._ht.read_tree(codex.substrate.surface(), codex.base_harness())
    tree[".codex/hooks.json"] = '{"hooks": {"PreToolUse": []}}'
    with pytest.raises(ValueError, match="c8 hooks"):
        codex.validate_tree(tree)


def test_validate_tree_rejects_dangerous_script(env):
    adapter = _adapter(env)
    base = adapter._ht.read_tree(adapter.substrate.surface(), adapter.base_harness())
    tree = dict(base)
    tree["scripts/steal.py"] = "import requests\nrequests.get('https://example.com')\n"
    with pytest.raises(ValueError, match="dangerous|network"):
        adapter.validate_tree(tree)


def test_validate_tree_rejects_oversize_agents(env):
    adapter = _adapter(env)
    tree = {"AGENTS.md": "x" * 70_000}
    with pytest.raises(ValueError, match="65|budget"):
        adapter.validate_tree(tree)
    codex = _adapter(env, "codex")
    with pytest.raises(ValueError):
        codex.validate_tree({"AGENTS.md": "x" * 40_000})


def test_validate_tree_accepts_triggered_skill(env):
    adapter = _adapter(env)
    tree = {
        "AGENTS.md": "# Guide\n\n- When you need the demo workflow, use the demo-skill skill.\n",
        "skills/demo-skill/SKILL.md": (
            "---\nname: demo-skill\ndescription: demo workflow\n---\n\nDo the demo work.\n"
        ),
    }
    adapter.validate_tree(tree)


def test_validate_tree_rejects_untriggered_skill(env):
    adapter = _adapter(env)
    with pytest.raises(ValueError, match="never named"):
        adapter.validate_tree(
            {
                "AGENTS.md": "# Guide\n",
                "skills/demo-skill/SKILL.md": (
                    "---\nname: demo-skill\ndescription: demo workflow\n---\n"
                ),
            }
        )


def test_tree_secret_scan(env):
    adapter = _adapter(env)
    with pytest.raises(ValueError, match="secret-shaped"):
        adapter.validate_tree(
            {
                "AGENTS.md": "# Guide\n\nUse " + "sk-" + "abcdefghijklmnop" + " in the example.\n",
            }
        )


def test_egress_and_container_are_fail_closed(monkeypatch):
    monkeypatch.setenv("MORPHD_CONTAINER_ISOLATED", "1")
    monkeypatch.delenv("MORPHD_EGRESS_ENFORCED", raising=False)
    with pytest.raises(security.SecurityError):
        security.require_isolation(enabled=True)
    monkeypatch.setenv("MORPHD_EGRESS_ENFORCED", "1")
    monkeypatch.setenv("MORPHD_EGRESS_ALLOWLIST", "api.example.com")
    detail = security.require_isolation(enabled=True)
    assert detail["egress_allowlist"] == ["api.example.com"]


def test_scan_python_rejects_nested_network_imports_and_credential_reads():
    for source in (
        "import urllib.error\n",
        "import requests.adapters\n",
        "import aiohttp.client\n",
        "import os\nos.execvp('x', [])\n",
        "from pathlib import Path\nPath('~/.ssh/id_rsa').read_text()\n",
    ):
        assert security.scan_python("scripts/x.py", source), source


def test_hidden_terms_are_rejected_inside_workspace_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("the hidden answer is answer-42\n", encoding="utf-8")
    problems = security.assert_no_leaks(
        ["answer-42"], "solve it", workspace, "", leak_globs=()
    )
    assert any("answer-42" in item for item in problems)
