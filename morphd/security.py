"""Candidate-tree security checks and execution-boundary assertions."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import sanitize

DANGEROUS_IMPORTS = {
    "aiohttp",
    "ctypes",
    "ftplib",
    "http",
    "http.client",
    "httpx",
    "paramiko",
    "pty",
    "requests",
    "socket",
    "subprocess",
    "telnetlib",
    "urllib",
    "urllib.request",
    "websockets",
}
DANGEROUS_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "os.system",
    "os.popen",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "shutil.rmtree",
    "importlib.import_module",
}
PROCESS_CALL_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")
NETWORK_CALL_PREFIXES = ("socket.", "requests.", "httpx.", "urllib.", "http.", "aiohttp.", "websockets.")
CREDENTIAL_PATH_MARKERS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
    ".password-store",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
)
RISK_TERMS = re.compile(
    r"(?i)(danger-full-access|bypass[-_ ]?approvals|approval/policy|sandbox/mode|"
    r"settings\.json|hooks\.json|cordis\.patch\.yml)"
)


class SecurityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanResult:
    violations: list[str]
    findings: list[sanitize.SecretFinding]

    @property
    def ok(self) -> bool:
        return not self.violations and not self.findings


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def scan_python(rel: str, source: str) -> list[str]:
    """Reject the execution surfaces a candidate script must not need."""
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [f"{rel} does not parse: {exc.msg} (line {exc.lineno})"]
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]

    def module_scope(node: ast.AST) -> bool:
        current = getattr(node, "parent", None)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                return False
            current = getattr(current, "parent", None)
        return True

    def dangerous_module(name: str) -> bool:
        if not name:
            return False
        if name in DANGEROUS_IMPORTS:
            return True
        root = name.split(".")[0]
        return any(
            name == item or name.startswith(item + ".")
            for item in DANGEROUS_IMPORTS
        ) or root in {"aiohttp", "ctypes", "ftplib", "http", "httpx", "paramiko", "pty", "requests", "socket", "subprocess", "telnetlib", "urllib", "websockets"}

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if dangerous_module(alias.name):
                    violations.append(f"{rel}:{node.lineno} imports dangerous module {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if dangerous_module(name):
                violations.append(f"{rel}:{node.lineno} imports dangerous module {name!r}")
            for alias in node.names:
                if name in ("os", "shutil") and alias.name in {
                    "system",
                    "popen",
                    "remove",
                    "unlink",
                    "rmdir",
                    "rmtree",
                } | {f"exec{tail}" for tail in ("v", "ve", "l", "le", "lp", "lpe")} | {
                    f"spawn{tail}" for tail in ("l", "le", "v", "ve")
                }:
                    violations.append(
                        f"{rel}:{node.lineno} imports dangerous function {name}.{alias.name}"
                    )
                if name == "importlib" and alias.name == "import_module":
                    violations.append(f"{rel}:{node.lineno} imports dynamic import_module")
                if alias.name in {"eval", "exec", "compile"}:
                    violations.append(f"{rel}:{node.lineno} imports dynamic execution {alias.name}")
        elif isinstance(node, ast.Call):
            path_line = f"{rel}:{getattr(node, 'lineno', 0)}"
            call = _call_name(node)
            if call.rsplit(".", 1)[-1] in {"eval", "exec"}:
                violations.append(f"{path_line} calls dynamic execution {call}()")
            if (
                call in DANGEROUS_CALLS
                or call.startswith(NETWORK_CALL_PREFIXES)
                or call.startswith(PROCESS_CALL_PREFIXES)
            ):
                violations.append(f"{path_line} calls {call}() — network/process/dynamic execution is not allowed")
            if call.rsplit(".", 1)[-1] in {"read_text", "read_bytes", "open"}:
                for literal in node.args[:2]:
                    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                        value = literal.value.replace("\\", "/")
                        if any(marker in value for marker in CREDENTIAL_PATH_MARKERS) or value.startswith(("/", "~")):
                            violations.append(
                                f"{path_line} reads a credential or absolute path ({literal.value!r}); "
                                "candidates may not read outside their temporary workspace"
                            )
            if call == "open" and len(node.args) >= 2 and module_scope(node):
                mode = node.args[1]
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(
                    c in mode.value for c in "wax+"
                ):
                    violations.append(
                        f"{path_line} opens a file for writing at module scope; move side effects "
                        "under `if __name__ == '__main__'`"
                    )
            if module_scope(node) and call.split(".")[0] in {"os", "shutil"} and call not in {
                "os.path.join",
                "os.path.exists",
                "os.getenv",
                "os.environ.get",
                "os.makedirs",
            }:
                violations.append(
                    f"{path_line} calls {call}() at module scope; module-level execution is not allowed"
                )
            if module_scope(node) and call.rsplit(".", 1)[-1] in {
                "write_text",
                "write_bytes",
                "touch",
            }:
                # C.3.6: a Path write performed at import time is a module-scope side effect even
                # when it is the value of an assignment (e.g. `p = Path("f").write_text(...)`).
                violations.append(
                    f"{path_line} writes to the filesystem ({call}()) at module scope; move "
                    "side effects under `if __name__ == '__main__'`"
                )
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            name = _call_name(stmt.value)
            if name not in {
                "Path",
                "PurePath",
                "re.compile",
                "str",
                "int",
                "float",
                "bool",
                "list",
                "dict",
                "tuple",
                "set",
                "frozenset",
                "len",
                "range",
                "print",
                "sorted",
                "min",
                "max",
                "enumerate",
                "zip",
                "json.dumps",
                "json.loads",
            }:
                violations.append(
                    f"{rel}:{getattr(stmt, 'lineno', 0)} runs {name}() at import time; "
                    "module-level execution is not allowed"
                )
    return sorted(set(violations))


def scan_tree_contents(tree: dict[str, str], base_tree: dict[str, str] | None = None) -> ScanResult:
    violations: list[str] = []
    findings: list[sanitize.SecretFinding] = []
    base = base_tree or {}
    for rel, text in sorted(tree.items()):
        changed = rel not in base or base.get(rel) != text
        before = base.get(rel, "")
        if rel.endswith(".py") and changed:
            violations.extend(scan_python(rel, text))
        findings.extend(sanitize.scan_text(text, path=rel))
        match = RISK_TERMS.search(text)
        if match and changed and not RISK_TERMS.search(before):
            violations.append(
                f"{rel}: references a controlled permission/config surface "
                f"({match.group(0)!r}); candidates may not change execution policy"
            )
    return ScanResult(violations=violations, findings=findings)


def scan_workspace(path: Path) -> list[sanitize.SecretFinding]:
    return sanitize.scan_paths([Path(path)])


def require_isolation(*, enabled: bool) -> dict:
    """Fail closed unless the caller is inside the container/egress boundary."""
    if not enabled:
        return {"required": False, "verified": False, "reason": "disabled-for-test"}
    marker = (
        Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
        or os.environ.get("MORPHD_CONTAINER_ISOLATED", "").lower() in {"1", "true", "yes"}
    )
    egress = os.environ.get("MORPHD_EGRESS_ENFORCED", "").lower() in {"1", "true", "yes"}
    allowlist = [
        item.strip()
        for item in os.environ.get("MORPHD_EGRESS_ALLOWLIST", "").split(",")
        if item.strip()
    ]
    if not marker:
        raise SecurityError(
            "harness2-morphd refuses to start real rollouts outside the isolated container. "
            "Run through deploy/container (which sets MORPHD_CONTAINER_ISOLATED=1), or set "
            "MORPHD_CONTAINER_REQUIRED=0 only in a disposable test environment."
        )
    if not egress or not allowlist:
        raise SecurityError(
            "egress enforcement is not declared. Set MORPHD_EGRESS_ENFORCED=1 and "
            "MORPHD_EGRESS_ALLOWLIST to the model endpoints permitted by the container firewall."
        )
    return {
        "required": True,
        "verified": True,
        "egress_enforced": True,
        "egress_allowlist": sorted(allowlist),
    }


def assert_no_leaks(
    terms: list[str],
    instruction: str,
    workspace: Path,
    trajectory_text: str = "",
    *,
    leak_globs: tuple[str, ...] = (),
) -> list[str]:
    problems: list[str] = []
    found = sanitize.scan_forbidden_terms(instruction, terms)
    if found:
        problems.append(f"instruction exposes hidden criteria terms: {found}")
    for glob in leak_globs:
        for path in Path(workspace).rglob(glob):
            if path.is_file():
                problems.append(f"workspace contains leak path {path.relative_to(workspace)}")
    # Hidden answers must not leak into any workspace file, not just names matched by
    # leak_globs. Scan text files with a bounded size to keep the check deterministic.
    workspace_root = Path(workspace)
    if workspace_root.is_dir():
        for path in sorted(workspace_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            text = data.decode("utf-8", errors="replace")
            leaked = sanitize.scan_forbidden_terms(text, terms)
            if leaked:
                problems.append(
                    f"workspace file {path.relative_to(workspace_root)} exposes hidden criteria terms: {leaked}"
                )
    if trajectory_text:
        found = sanitize.scan_forbidden_terms(trajectory_text, terms)
        if found:
            problems.append(f"trajectory exposes hidden criteria terms: {found}")
    return problems
