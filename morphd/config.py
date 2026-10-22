"""Deployment settings, pinned versions and the fixed runtime baselines."""

from __future__ import annotations

import importlib.resources
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

HARNESS2_COMMIT = "1cfe254e8d25"
RUNTIMES = ("dsh", "codex", "claude-code")
RUNTIME_SUBSTRATE = {"dsh": "dsh", "codex": "cx", "claude-code": "cc"}
RUNTIME_PINS = {
    "dsh": os.environ.get("MORPHD_DSH_VERSION", "0.2.1-rc.2"),
    "codex": os.environ.get("MORPHD_CODEX_VERSION", "0.144.6"),
    "claude-code": os.environ.get("MORPHD_CLAUDE_VERSION", "2.1.302"),
}

MODEL_ENV = {
    "dsh": "MORPHD_DSH_MODEL",
    "codex": "MORPHD_CODEX_MODEL",
    "claude-code": "MORPHD_CLAUDE_MODEL",
}
DEFAULT_BIN = {
    "dsh": "dsh",
    "codex": "codex",
    "claude-code": "claude",
}
BIN_ENV = {
    "dsh": "MORPHD_DSH_BIN",
    "codex": "MORPHD_CODEX_BIN",
    "claude-code": "MORPHD_CLAUDE_BIN",
}

DSH_SANDBOX_MODE = "workspace-write"
DSH_APPROVAL_POLICY = "ask"
DEFAULT_APPROVER_TOOLS = (
    "bash,shell,read,write,edit,apply_patch,glob,grep,list_dir,view_image"
)
DEFAULT_APPROVER_DENY = (
    "rm -rf /,sudo,shutdown,reboot,mkfs,dd if=,danger-full-access,"
    "git push,--force,curl,wget,nc ,netcat,ssh ,scp ,/etc/,~/.ssh"
)
DEFAULT_CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash"
DEFAULT_CLAUDE_DISALLOWED_TOOLS = "WebFetch,WebSearch"

WORKSPACE_DIRNAME = "workspace"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path_env(name: str, default: Path) -> Path:
    raw = _env(name)
    return Path(raw).expanduser().resolve() if raw else default.expanduser().resolve()


def _package_path(*parts: str) -> Path:
    return Path(str(importlib.resources.files("morphd"))) / Path(*parts)


def packaged_pricing() -> Path:
    return _package_path("pricing.yaml")


def packaged_base_harness(runtime: str) -> Path:
    return _package_path("base_harnesses", runtime)


def default_approver_plugin() -> Path:
    return _package_path("templates", "morphd-approver.mjs")


@dataclass(frozen=True)
class Settings:
    home: Path
    tasks_root: Path
    runs_root: Path
    libraries_root: Path
    snapshots_root: Path
    promotions_root: Path
    evaluations_root: Path
    pricing_path: Path
    split_file: Path | None = None
    timeout_s: int = 1800
    obs_trunc: int = 50_000
    container_required: bool = False
    dsh_provider: str = "deepseek-official"
    codex_reasoning_effort: str = "high"
    claude_effort: str = "high"
    claude_permission_mode: str = "acceptEdits"
    claude_allowed_tools: str = DEFAULT_CLAUDE_ALLOWED_TOOLS
    claude_disallowed_tools: str = DEFAULT_CLAUDE_DISALLOWED_TOOLS
    approver_tools: str = DEFAULT_APPROVER_TOOLS
    approver_deny: str = DEFAULT_APPROVER_DENY
    models: dict[str, str] = field(default_factory=dict)
    binaries: dict[str, str] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)
    services: dict[str, str] = field(
        default_factory=dict
    )

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        home = _path_env("MORPHD_HOME", Path.home() / ".morphd")
        models = {rt: _env(MODEL_ENV[rt]) for rt in RUNTIMES}
        binaries = {rt: _env(BIN_ENV[rt], DEFAULT_BIN[rt]) for rt in RUNTIMES}
        pricing = (
            Path(_env("MORPHD_PRICING")).expanduser().resolve()
            if _env("MORPHD_PRICING")
            else (home / "pricing.yaml" if (home / "pricing.yaml").is_file() else packaged_pricing())
        )
        base = cls(
            home=home,
            tasks_root=_path_env("MORPHD_TASKS", home / "tasks"),
            runs_root=_path_env("MORPHD_RUNS", home / "runs"),
            libraries_root=_path_env("MORPHD_LIBRARIES", home / "libraries"),
            snapshots_root=_path_env("MORPHD_SNAPSHOTS", home / "snapshots"),
            promotions_root=_path_env("MORPHD_PROMOTIONS", home / "promotions"),
            evaluations_root=_path_env("MORPHD_EVALUATIONS", home / "evaluations"),
            pricing_path=pricing,
            timeout_s=int(_env("MORPHD_TIMEOUT_S", "1800")),
            obs_trunc=int(_env("MORPHD_OBS_TRUNC", "50000")),
            container_required=_env("MORPHD_CONTAINER_REQUIRED", "0") not in {"0", "false", "no"},
            dsh_provider=_env("MORPHD_DSH_PROVIDER", "deepseek-official"),
            codex_reasoning_effort=_env("MORPHD_CODEX_EFFORT", "high"),
            claude_effort=_env("MORPHD_CLAUDE_EFFORT", "high"),
            claude_permission_mode=_env("MORPHD_CLAUDE_PERMISSION_MODE", "acceptEdits"),
            claude_allowed_tools=_env("MORPHD_CLAUDE_ALLOWED_TOOLS", DEFAULT_CLAUDE_ALLOWED_TOOLS),
            claude_disallowed_tools=_env(
                "MORPHD_CLAUDE_DISALLOWED_TOOLS", DEFAULT_CLAUDE_DISALLOWED_TOOLS
            ),
            approver_tools=_env("MORPHD_APPROVER_ALLOW_TOOLS", DEFAULT_APPROVER_TOOLS),
            approver_deny=_env("MORPHD_APPROVER_DENY", DEFAULT_APPROVER_DENY),
            models=models,
            binaries=binaries,
            pins=dict(RUNTIME_PINS),
            services={
                "dsh": _env("MORPHD_DSH_SERVICE", "dsh"),
                "codex": _env("MORPHD_CODEX_SERVICE", "codex"),
                "claude-code": _env("MORPHD_CLAUDE_SERVICE", "claude"),
            },
        )
        return replace(base, **overrides) if overrides else base

    def runtime_runs_root(self, runtime: str) -> Path:
        return self.runs_root / runtime

    def runtime_library_root(self, runtime: str) -> Path:
        return self.libraries_root / runtime

    def base_harness(self, runtime: str) -> Path:
        managed = self.home / "harnesses" / runtime / "base"
        if managed.is_dir():
            return managed
        return packaged_base_harness(runtime)

    def model_for(self, runtime: str) -> str:
        return self.models.get(runtime, "")

    def binary_for(self, runtime: str) -> str:
        return self.binaries.get(runtime) or DEFAULT_BIN[runtime]

    def ensure_dirs(self) -> None:
        for p in (
            self.home,
            self.tasks_root,
            self.runs_root,
            self.libraries_root,
            self.snapshots_root,
            self.promotions_root,
            self.evaluations_root,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def dsh_target_home(self) -> Path | None:
        raw = _env("MORPHD_DSH_TARGET_HOME")
        return Path(raw).expanduser().resolve() if raw else None

    def codex_target(self) -> Path | None:
        raw = _env("MORPHD_CODEX_TARGET")
        return Path(raw).expanduser().resolve() if raw else None

    def claude_target(self) -> Path | None:
        raw = _env("MORPHD_CLAUDE_TARGET")
        return Path(raw).expanduser().resolve() if raw else None

    def target_for(self, runtime: str) -> Path | None:
        return {
            "dsh": self.dsh_target_home(),
            "codex": self.codex_target(),
            "claude-code": self.claude_target(),
        }[runtime]


def parse_runtime_list(raw: str) -> list[str]:
    items = [s.strip() for s in str(raw).split(",") if s.strip()]
    if not items:
        raise ValueError("at least one --runtime is required")
    unknown = sorted(set(items) - set(RUNTIMES))
    if unknown:
        raise ValueError(f"unknown runtime(s) {unknown}; expected {RUNTIMES}")
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def parse_named_values(raw: list[str] | str | None, runtime: str | None = None) -> dict[str, str]:
    """Parse repeated ``runtime=value`` (or a bare value applied to one runtime)."""
    if raw is None:
        return {}
    values = [raw] if isinstance(raw, str) else list(raw)
    out: dict[str, str] = {}
    for value in values:
        for piece in str(value).split(","):
            piece = piece.strip()
            if not piece:
                continue
            if "=" in piece:
                key, _, val = piece.partition("=")
                out[key.strip()] = val.strip()
            elif runtime:
                out[runtime] = piece
            else:
                raise ValueError(f"expected runtime=value, got {piece!r}")
    return out
