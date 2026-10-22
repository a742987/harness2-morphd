"""The runtime-independent task contract (``task_contract: 1``) and its store."""

from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import store

SCHEMA_PATH = Path(__file__).with_name("schemas") / "task_contract.schema.json"
TASK_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RUNTIMES = ("dsh", "codex", "claude-code", "manual")
SPLIT_NAMES = ("train", "test", "holdout")


class ContractError(ValueError):
    pass


def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_schema(payload: dict) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise ContractError("jsonschema is required to validate task contracts") from exc
    validator = jsonschema.Draft202012Validator(_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for err in errors:
            where = "/".join(str(p) for p in err.absolute_path) or "<root>"
            lines.append(f"{where}: {err.message}")
        raise ContractError("; ".join(lines))


def _safe_path(value: str, field_name: str) -> str:
    value = str(value).replace("\\", "/")
    if value.startswith("/") or ".." in value.split("/") or "\x00" in value:
        raise ContractError(f"{field_name} must be a relative path without '..': {value!r}")
    return value


@dataclass(frozen=True)
class UserTurn:
    text: str
    corrective: bool = False

    def to_dict(self) -> dict:
        return {"text": self.text, "corrective": bool(self.corrective)}


@dataclass(frozen=True)
class TaskContract:
    task_ref: str
    instruction: str
    workspace_sha256: str
    checks: tuple[dict, ...]
    user_turns: tuple[UserTurn, ...]
    expected_deliverables: tuple[str, ...]
    domain: str
    source_runtime: str
    created_at: str = ""
    provenance: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict) -> "TaskContract":
        _validate_schema(payload)
        instruction = str(payload["instruction"])
        if not instruction.strip():
            raise ContractError("instruction must not be blank")
        turns = tuple(
            UserTurn(text=str(t["text"]), corrective=bool(t["corrective"]))
            for t in payload["user_turns"]
        )
        if not turns:
            raise ContractError("user_turns must contain U1")
        if turns[0].text != instruction:
            raise ContractError("user_turns[0].text must equal instruction (U1 is the task)")
        for i, turn in enumerate(turns):
            if not turn.text.strip():
                raise ContractError(f"user_turns[{i}].text must not be blank")
        checks = tuple(copy.deepcopy(payload["criteria"]["checks"]))
        for i, check in enumerate(checks):
            if "path" in check:
                _safe_path(check["path"], f"criteria.checks[{i}].path")
            if "glob" in check:
                _safe_path(check["glob"], f"criteria.checks[{i}].glob")
        expected = tuple(str(x) for x in payload["expected_deliverables"])
        for i, name in enumerate(expected):
            _safe_path(name, f"expected_deliverables[{i}]")
        if payload["source_runtime"] not in RUNTIMES:
            raise ContractError(f"unknown source_runtime {payload['source_runtime']!r}")
        if not TASK_REF_RE.fullmatch(str(payload["task_ref"])):
            raise ContractError(f"invalid task_ref {payload['task_ref']!r}")
        return cls(
            task_ref=str(payload["task_ref"]),
            instruction=instruction,
            workspace_sha256=str(payload["workspace"]["sha256"]),
            checks=checks,
            user_turns=turns,
            expected_deliverables=expected,
            domain=str(payload["domain"]),
            source_runtime=str(payload["source_runtime"]),
            created_at=str(payload.get("created_at", "")),
            provenance=copy.deepcopy(payload.get("provenance") or {}),
        )

    @classmethod
    def from_path(cls, path: Path) -> "TaskContract":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError(f"cannot read contract {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ContractError(f"contract {path} must be a JSON object")
        return cls.from_dict(payload)

    @classmethod
    def new(
        cls,
        *,
        task_ref: str,
        instruction: str,
        workspace_sha256: str,
        domain: str,
        checks: Iterable[dict] = (),
        user_turns: Iterable[UserTurn | dict] | None = None,
        expected_deliverables: Iterable[str] = (),
        source_runtime: str = "manual",
        created_at: str | None = None,
        provenance: dict | None = None,
    ) -> "TaskContract":
        turns: list[UserTurn] = []
        for turn in user_turns or [UserTurn(instruction)]:
            turns.append(turn if isinstance(turn, UserTurn) else UserTurn(**turn))
        payload = {
            "task_contract": 1,
            "task_ref": task_ref,
            "instruction": instruction,
            "workspace": {"dir": "workspace", "sha256": workspace_sha256},
            "criteria": {"checks": list(checks)},
            "user_turns": [t.to_dict() for t in turns],
            "expected_deliverables": list(expected_deliverables),
            "domain": domain,
            "source_runtime": source_runtime,
            "created_at": created_at or store.utc_now(),
            "provenance": provenance or {},
        }
        return cls.from_dict(payload)

    def to_dict(self) -> dict:
        payload = {
            "task_contract": 1,
            "task_ref": self.task_ref,
            "instruction": self.instruction,
            "workspace": {"dir": "workspace", "sha256": self.workspace_sha256},
            "criteria": {"checks": [copy.deepcopy(c) for c in self.checks]},
            "user_turns": [t.to_dict() for t in self.user_turns],
            "expected_deliverables": list(self.expected_deliverables),
            "domain": self.domain,
            "source_runtime": self.source_runtime,
            "created_at": self.created_at,
            "provenance": copy.deepcopy(self.provenance),
        }
        return payload

    def canonical(self) -> str:
        return store.canonical_json(self.to_dict())

    def content_hash(self) -> str:
        return store.payload_hash(self.to_dict())

    def hidden_terms(self) -> list[str]:
        """High-signal answer material that must not appear in a prompt or trajectory.

        A check's expected text is usually a restatement of what the user asked for, so it is
        not hidden unless the task author marks it `hidden: true`. Hashes, regexes and
        JSON answers are answers by construction.
        """
        terms: list[str] = []
        for check in self.checks:
            hidden = bool(check.get("hidden"))
            kind = check.get("type")
            if hidden:
                for key in ("text", "pattern"):
                    val = check.get(key)
                    if isinstance(val, str) and len(val.strip()) >= 4:
                        terms.append(val.strip())
            if kind == "file_sha256" and isinstance(check.get("sha256"), str):
                terms.append(check["sha256"])
            if kind == "file_regex" and isinstance(check.get("pattern"), str):
                if len(check["pattern"].strip()) >= 8:
                    terms.append(check["pattern"].strip())
            if kind == "json_equals":
                val = check.get("value")
                rendered = store.canonical_json(val) if not isinstance(val, str) else val.strip()
                if len(rendered) >= 4:
                    terms.append(rendered)
        return sorted(set(terms))

    def max_turns(self) -> int:
        return max(1, len(self.user_turns))


@dataclass
class TaskStore:
    root: Path
    split_file: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.split_file = Path(self.split_file) if self.split_file else None
        self._contracts: dict[str, TaskContract] = {}
        self._task_dirs: dict[str, Path] = {}

    def task_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(
            p for p in self.root.iterdir() if p.is_dir() and (p / "contract.json").is_file()
        )

    def load(self) -> dict[str, TaskContract]:
        if self._contracts:
            return dict(self._contracts)
        errors: list[str] = []
        for directory in self.task_dirs():
            try:
                contract = TaskContract.from_path(directory / "contract.json")
            except ContractError as exc:
                errors.append(f"{directory}: {exc}")
                continue
            if contract.task_ref in self._contracts:
                errors.append(f"duplicate task_ref {contract.task_ref!r}")
                continue
            self._contracts[contract.task_ref] = contract
            self._task_dirs[contract.task_ref] = directory
        if errors:
            raise ContractError("invalid task store: " + " | ".join(errors))
        return dict(self._contracts)

    def all(self) -> list[str]:
        return sorted(self.load())

    def directory_of(self, task_ref: str) -> Path:
        self.load()
        try:
            return self._task_dirs[task_ref]
        except KeyError as exc:
            raise ContractError(f"unknown task {task_ref!r}") from exc

    def get(self, task_ref: str) -> TaskContract:
        self.load()
        try:
            return self._contracts[task_ref]
        except KeyError as exc:
            raise ContractError(f"unknown task {task_ref!r}") from exc

    def workspace_dir(self, task_ref: str, *, verify: bool = False) -> Path:
        directory = self.directory_of(task_ref) / "workspace"
        if not directory.is_dir():
            raise ContractError(f"task {task_ref!r} has no workspace directory")
        if verify:
            self.verify_workspace(task_ref)
        return directory

    def verify_workspace(self, task_ref: str) -> str:
        contract = self.get(task_ref)
        directory = self.directory_of(task_ref) / "workspace"
        actual = store.tree_hash(directory)
        if actual != contract.workspace_sha256:
            raise ContractError(
                f"workspace hash mismatch for {task_ref}: contract={contract.workspace_sha256} "
                f"actual={actual}"
            )
        return actual

    def split_path(self) -> Path:
        return self.split_file or (self.root / "splits.json")

    def assignments(self) -> dict[str, str]:
        path = self.split_path()
        payload = store.read_json(path)
        if not isinstance(payload, dict):
            raise ContractError(
                f"no readable split file at {path}; run `morphd tasks split` first"
            )
        if isinstance(payload.get("assignments"), dict):
            return {str(k): str(v) for k, v in payload["assignments"].items()}
        splits = payload.get("splits")
        if isinstance(splits, dict):
            out: dict[str, str] = {}
            for name, tasks in splits.items():
                for task in tasks or []:
                    out[str(task)] = str(name)
            return out
        raise ContractError(f"split file {path} has no assignments or splits map")

    def tasks(self, split: str = "all") -> list[str]:
        all_tasks = self.all()
        if split in ("all", "", None):
            return all_tasks
        if split not in SPLIT_NAMES:
            raise ContractError(f"unknown split {split!r}; expected all or {SPLIT_NAMES}")
        assignments = self.assignments()
        unknown = sorted(set(assignments) - set(all_tasks))
        if unknown:
            raise ContractError(f"split file names unknown tasks: {unknown}")
        missing = sorted(set(all_tasks) - set(assignments))
        if missing:
            raise ContractError(
                "split file does not cover every task; regenerate with `morphd tasks split "
                f"--force` (missing: {missing[:5]})"
            )
        selected = [t for t in all_tasks if assignments.get(t) == split]
        return selected

    def domains(self, split: str = "all") -> dict[str, int]:
        counts: dict[str, int] = {}
        for task_ref in self.tasks(split):
            domain = self.get(task_ref).domain
            counts[domain] = counts.get(domain, 0) + 1
        return dict(sorted(counts.items()))

    def domain_of(self, task_ref: str) -> str:
        return self.get(task_ref).domain

    def write_splits(
        self,
        assignments: dict[str, str],
        *,
        force: bool = False,
        metadata: dict | None = None,
    ) -> Path:
        tasks = set(self.all())
        if set(assignments) != tasks:
            missing = sorted(tasks - set(assignments))
            extra = sorted(set(assignments) - tasks)
            raise ContractError(f"split assignments must cover every task; missing={missing} extra={extra}")
        bad = sorted({v for v in assignments.values()} - set(SPLIT_NAMES))
        if bad:
            raise ContractError(f"unknown split names {bad}")
        path = self.split_path()
        if path.exists() and not force:
            raise ContractError(f"split file already exists: {path} (pass --force to replace)")
        payload = {
            "contract": 1,
            "created_at": store.utc_now(),
            "assignments": {k: assignments[k] for k in sorted(assignments)},
            **(metadata or {}),
        }
        store.atomic_write_json(path, payload)
        return path

    def add_task(self, contract: TaskContract, workspace_src: Path, *, force: bool = False) -> Path:
        from . import sanitize

        dest = self.root / store.safe_slug(contract.task_ref)
        if dest.exists():
            existing = TaskContract.from_path(dest / "contract.json") if (
                dest / "contract.json"
            ).is_file() else None
            if existing and existing.content_hash() == contract.content_hash():
                return dest
            if not force:
                raise ContractError(f"task {contract.task_ref!r} already exists at {dest}")
            shutil.rmtree(dest)
        staging = dest.with_name(dest.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            sanitize.sanitize_tree(Path(workspace_src), staging / "workspace")
            actual = store.tree_hash(staging / "workspace")
            if actual != contract.workspace_sha256:
                raise ContractError(
                    f"workspace hash mismatch for {contract.task_ref}: "
                    f"contract={contract.workspace_sha256} sanitized={actual}"
                )
            store.atomic_write_json(staging / "contract.json", contract.to_dict())
            if dest.exists():
                shutil.rmtree(dest)
            staging.rename(dest)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self._contracts.clear()
        self._task_dirs.clear()
        return dest

    def build_split_assignments(
        self, *, holdout: float = 0.2, test: float = 0.5, seed: int = 0
    ) -> dict[str, str]:
        tasks = sorted(self.all())
        if not tasks:
            return {}
        ordered = sorted(tasks, key=lambda t: store.sha256_text(f"{seed}:{t}"))
        if not 0 <= holdout < 1:
            raise ContractError("holdout ratio must be in [0, 1)")
        n_holdout = int(round(len(ordered) * holdout)) if holdout else 0
        if holdout > 0 and len(ordered) >= 5:
            n_holdout = max(1, n_holdout)
        held = ordered[:n_holdout]
        rest = ordered[n_holdout:]
        n_test = int(round(len(rest) * test)) if test else 0
        if test > 0 and len(rest) >= 2:
            n_test = max(1, n_test)
        test_set = set(rest[:n_test])
        out: dict[str, str] = {}
        for task in held:
            out[task] = "holdout"
        for task in rest:
            out[task] = "test" if task in test_set else "train"
        return out


def load_contract(path: Path) -> TaskContract:
    return TaskContract.from_path(path)


def task_workspace_hash(path: Path) -> str:
    return store.tree_hash(Path(path))
