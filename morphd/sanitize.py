"""Workspace sanitisation and credential scanning.

The task snapshot is the only copy an executor sees. Deleting secret-bearing files and
redacting credential shapes happens before the workspace hash is recorded, so all three
runtimes receive byte-identical, already-sanitised input.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import store

SECRET_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    ".credentials*",
    "credentials.json",
    "auth.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    ".git-credentials",
)
SECRET_DIR_NAMES = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".password-store"}
LEAK_TOP_LEVEL_DIRS = {"rubrics", "expected", "reference", "solution", "criteria"}
LEAK_TOP_LEVEL_FILES = {"RUBRICS.json", "task.json", "criteria.json"}
TEXT_LIMIT = 2_000_000

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9]+ )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*")),
    ("assignment", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*[\"']?([A-Za-z0-9._~+/=-]{12,})")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
)


@dataclass
class SanitizeReport:
    source: str
    destination: str
    files_copied: int = 0
    bytes_copied: int = 0
    skipped_secret_files: list[str] = field(default_factory=list)
    skipped_symlinks: list[str] = field(default_factory=list)
    skipped_leak_paths: list[str] = field(default_factory=list)
    redacted_files: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "destination": self.destination,
            "files_copied": self.files_copied,
            "bytes_copied": self.bytes_copied,
            "skipped_secret_files": sorted(self.skipped_secret_files),
            "skipped_symlinks": sorted(self.skipped_symlinks),
            "skipped_leak_paths": sorted(self.skipped_leak_paths),
            "redacted_files": self.redacted_files,
        }


def is_secret_filename(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pattern) for pattern in SECRET_FILE_PATTERNS)


def redact_text(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    for name, pattern in _PATTERNS:
        def repl(match: re.Match) -> str:
            kinds.append(name)
            return f"[REDACTED:{name}]"

        text = pattern.sub(repl, text)
    return text, sorted(set(kinds))


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def sanitize_tree(
    src: Path,
    dest: Path,
    *,
    max_files: int = 20_000,
    max_total_bytes: int = 250 * 1024 * 1024,
    report_path: Path | None = None,
) -> SanitizeReport:
    src = Path(src).resolve()
    dest = Path(dest)
    if not src.is_dir():
        raise ValueError(f"workspace source is not a directory: {src}")
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"sanitize destination must be empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    report = SanitizeReport(source=str(src), destination=str(dest))
    total = 0
    ignore = store.WORKSPACE_IGNORES | SECRET_DIR_NAMES

    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        for name in list(dirnames):
            if not Path(dirpath).relative_to(src).parts and name in LEAK_TOP_LEVEL_DIRS:
                report.skipped_leak_paths.append(name)
                dirnames.remove(name)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in ignore and not d.startswith(".git") and d not in SECRET_DIR_NAMES
        )
        base = Path(dirpath)
        for name in sorted(filenames):
            source = base / name
            rel = source.relative_to(src).as_posix()
            if source.is_symlink():
                report.skipped_symlinks.append(rel)
                continue
            if name.endswith(tuple(store._IGNORED_SUFFIXES)):
                continue
            if is_secret_filename(name):
                report.skipped_secret_files.append(rel)
                continue
            if not base.relative_to(src).parts and name in LEAK_TOP_LEVEL_FILES:
                report.skipped_leak_paths.append(rel)
                continue
            try:
                st = source.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if report.files_copied >= max_files:
                raise ValueError(f"workspace has more than {max_files} files")
            if total + st.st_size > max_total_bytes:
                raise ValueError(f"workspace exceeds {max_total_bytes} bytes")
            data = source.read_bytes()
            kinds: list[str] = []
            if not _looks_binary(data):
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                redacted, kinds = redact_text(text)
                if kinds:
                    data = redacted.encode("utf-8")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            store.atomic_write_bytes(target, data)
            mode = stat.S_IMODE(st.st_mode)
            if mode & stat.S_IXUSR:
                os.chmod(target, mode & 0o755)
            report.files_copied += 1
            report.bytes_copied += len(data)
            total += len(data)
            if kinds:
                report.redacted_files.append({"path": rel, "kinds": kinds})
    if report_path is not None:
        store.atomic_write_json(report_path, report.to_dict())
    return report


def _secret_values(extra: dict[str, str] | None = None) -> list[tuple[str, str]]:
    names = (
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "HARNESS2_CODEX_API_KEY",
        "HARNESS2_JUDGE_API_KEY",
        "GH_TOKEN",
    )
    out: list[tuple[str, str]] = []
    for name in names:
        value = os.environ.get(name, "").strip()
        if len(value) >= 8:
            out.append((name, value))
    for name, value in (extra or {}).items():
        value = str(value).strip()
        if len(value) >= 8:
            out.append((name, value))
    return out


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    kind: str

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "kind": self.kind}


def scan_text(text: str, *, path: str = "<text>") -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    seen: set[tuple[str, int, str]] = set()
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            key = (path, line, name)
            if key not in seen:
                seen.add(key)
                findings.append(SecretFinding(path=path, line=line, kind=name))
    for env_name, value in _secret_values():
        start = text.find(value)
        while start != -1:
            line = text.count("\n", 0, start) + 1
            key = (path, line, f"env:{env_name}")
            if key not in seen:
                seen.add(key)
                findings.append(SecretFinding(path=path, line=line, kind=f"env:{env_name}"))
            start = text.find(value, start + len(value))
    return findings


def scan_paths(paths: list[Path], *, max_file_size: int = TEXT_LIMIT) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for item in paths:
        item = Path(item)
        candidates = store.iter_files(item) if item.is_dir() else [item]
        for path in candidates:
            try:
                if path.stat().st_size > max_file_size:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if _looks_binary(data):
                continue
            findings.extend(
                scan_text(data.decode("utf-8", errors="replace"), path=str(path))
            )
    return findings


def scan_forbidden_terms(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    return sorted({term for term in terms if term and term.lower() in low})


__all__ = [
    "SanitizeReport",
    "SecretFinding",
    "is_secret_filename",
    "redact_text",
    "sanitize_tree",
    "scan_forbidden_terms",
    "scan_paths",
    "scan_text",
]
