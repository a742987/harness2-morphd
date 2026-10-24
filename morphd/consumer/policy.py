"""Trust levels, change appetite and candidate scanning.

The consumer product is stricter than the internal engine: a candidate can never touch
permission/sandbox/hooks/settings/MCP surfaces, regardless of trust level or appetite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .. import sanitize, security
from .errors import ConsumerError
from .util import diff_trees, is_secret_path, safe_rel


@dataclass(frozen=True)
class Appetite:
    name: str
    max_files: int
    max_lines: int
    allow_skills: bool
    allow_existing_scripts: bool
    allow_new_scripts: bool
    allow_agents: bool = False


APPETITES = {
    "conservative": Appetite("conservative", 1, 30, False, False, False),
    "balanced": Appetite("balanced", 3, 100, True, True, False),
    "bold": Appetite("bold", 10, 300, True, True, True, allow_agents=True),
}

INSTRUCTION_FILE = {
    "dsh": "AGENTS.md",
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
}
SKILL_PATTERN = {
    "dsh": re.compile(r"^skills/([a-z0-9][a-z0-9-]*)/SKILL\.md$"),
    "claude-code": re.compile(r"^\.claude/skills/([a-z0-9][a-z0-9-]*)/SKILL\.md$"),
    "codex": re.compile(r"^\.agents/skills/([a-z0-9][a-z0-9-]*)/SKILL\.md$"),
}
SCRIPT_PATTERN = re.compile(r"^scripts/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.py$")
CLAUDE_AGENT_PATTERN = re.compile(r"^\.claude/agents/[a-z0-9][a-z0-9-]*\.md$")
FORBIDDEN_NAMES = {
    "settings.json",
    "settings.local.json",
    "hooks.json",
    "mcp.json",
    ".mcp.json",
    "cordis.patch.yml",
    "credentials.json",
    "auth.json",
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
}
FORBIDDEN_PREFIXES = (
    ".claude/settings",
    ".codex/",
    ".dsh/",
    ".git/",
    ".agents/mcp",
    ".claude/mcp",
)
RISK_TERMS = re.compile(
    r"(?i)(danger-full-access|bypass[-_ ]?approvals|bypass[-_ ]?hook|approval/policy|"
    r"sandbox/mode|sandbox[_-]?policy|hooks?\.json|settings\.json|\.mcp\.json|"
    r"mcp\.json|cordis\.patch\.yml|permission[-_ ]?mode|dangerously[-_]bypass)"
)
CREDENTIAL_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".password-store"}
_CREDENTIAL_TOKEN = re.compile(r"[^\s\"'`<>()\[\]{},;]+")
ALWAYS_ON_LIMIT = {"dsh": 65_536, "codex": 32_768, "claude-code": 32_768}


@dataclass
class CandidateCheck:
    ok: bool
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    kind: str = "instruction"
    lines: int = 0
    diff: str = ""
    violations: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    appetite: str = "conservative"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "added": self.added,
            "kind": self.kind,
            "lines": self.lines,
            "violations": self.violations,
            "findings": self.findings,
            "appetite": self.appetite,
            "checks": ["surface", "appetite", "secrets", "no-permission-change"],
        }


def _surface_allowed(rel: str, *, runtime: str, appetite: Appetite, existing: bool) -> bool:
    if rel == INSTRUCTION_FILE.get(runtime, "AGENTS.md"):
        return True
    if appetite.allow_skills and SKILL_PATTERN.get(runtime, SKILL_PATTERN["dsh"]).match(rel):
        return True
    if SCRIPT_PATTERN.match(rel):
        return existing and appetite.allow_existing_scripts or appetite.allow_new_scripts
    if appetite.allow_agents and CLAUDE_AGENT_PATTERN.match(rel) and runtime == "claude-code":
        return True
    return False


def _forbidden(rel: str) -> bool:
    path = PurePosixPath(rel)
    if any(part in (".git", ".ssh", ".aws", ".gnupg", ".kube", ".docker") for part in path.parts):
        return True
    name = path.name
    if name in FORBIDDEN_NAMES or is_secret_path(rel):
        return True
    return any(rel == prefix or rel.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def _credential_reference(text: str) -> str:
    """Return a credential path mentioned in candidate content, if any."""
    for match in _CREDENTIAL_TOKEN.finditer(text):
        token = match.group(0).strip("*`-").rstrip(".:")
        if not token:
            continue
        normalized = token.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        if sanitize.is_secret_filename(name):
            return token
        if any(part in CREDENTIAL_DIRS for part in normalized.split("/")):
            return token
    return ""


def check_candidate(
    *,
    runtime: str,
    base_tree: dict[str, str],
    candidate_tree: dict[str, str],
    appetite_name: str,
) -> CandidateCheck:
    appetite = APPETITES.get(appetite_name, APPETITES["conservative"])
    diff, changed, lines = diff_trees(base_tree, candidate_tree)
    result = CandidateCheck(
        ok=True,
        changed=changed,
        added=sorted(set(candidate_tree) - set(base_tree)),
        lines=lines,
        diff=diff,
        appetite=appetite.name,
    )
    if not changed:
        result.ok = False
        result.violations.append("no changes")
        return result
    if len(changed) > appetite.max_files:
        result.ok = False
        result.violations.append(
            f"{len(changed)} files changed; {appetite.name} allows at most {appetite.max_files}"
        )
    if lines > appetite.max_lines:
        result.ok = False
        result.violations.append(
            f"{lines} changed lines; {appetite.name} allows at most {appetite.max_lines}"
        )
    for rel in changed:
        try:
            clean = safe_rel(rel)
        except ConsumerError:
            result.violations.append(f"{rel}: unsafe path")
            continue
        if _forbidden(clean):
            result.violations.append(f"{rel}: forbidden path or secret file")
            continue
        before = base_tree.get(clean)
        after = candidate_tree.get(clean)
        existing = before is not None
        if not _surface_allowed(
            clean, runtime=runtime, appetite=appetite, existing=existing
        ):
            result.violations.append(
                f"{rel}: not allowed by the {appetite.name} change appetite"
            )
            continue
        if after is None:
            result.violations.append(f"{rel}: candidate may not delete harness files")
            continue
        if clean in (INSTRUCTION_FILE.get(runtime), "AGENTS.md", "CLAUDE.md"):
            limit = ALWAYS_ON_LIMIT.get(runtime, 32_768)
            if len(after.encode("utf-8")) > limit:
                result.violations.append(f"{rel}: exceeds the {limit}-byte always-on limit")
        if RISK_TERMS.search(after) and not RISK_TERMS.search(before or ""):
            result.violations.append(
                f"{rel}: references a controlled permission/config surface; rejected"
            )
        credential = _credential_reference(after)
        if credential and not _credential_reference(before or ""):
            result.violations.append(
                f"{rel}: references credential material ({credential}); candidates may not read "
                "credentials, .env files or private keys"
            )
    scan = security.scan_tree_contents(
        {rel: text for rel, text in candidate_tree.items() if rel in changed}
    )
    # scan_tree_contents treats a missing base as "everything is new"; that is correct for
    # consumer changed files because the caller has already compared them above.
    result.violations.extend(scan.violations)
    result.findings = [finding.to_dict() for finding in scan.findings]
    kinds = []
    for rel in changed:
        if rel in (INSTRUCTION_FILE.get(runtime), "AGENTS.md", "CLAUDE.md"):
            kinds.append("instruction")
        elif "/skills/" in rel or rel.startswith("skills/"):
            kinds.append("skill")
        elif rel.endswith(".py"):
            kinds.append("script")
        elif CLAUDE_AGENT_PATTERN.match(rel):
            kinds.append("agent")
        else:
            kinds.append("other")
    if "script" in kinds:
        result.kind = "script"
    elif "skill" in kinds:
        result.kind = "skill"
    elif "agent" in kinds:
        result.kind = "agent"
    result.ok = not result.violations and not result.findings
    return result


def candidate_confidence(kind: str, lines: int, signals: dict) -> str:
    """High-confidence suggestions may be surfaced immediately (PRD §4.7)."""
    corrections = int(signals.get("corrections", 0) or 0)
    loops = int(signals.get("tool_loops", 0) or 0)
    achieved = signals.get("achieved")
    if kind == "instruction" and (corrections >= 1 or loops >= 1 or achieved is False):
        return "high"
    if kind in ("skill", "script") and corrections >= 2:
        return "high"
    return "normal"


def risk_statement(check: CandidateCheck) -> str:
    if check.kind == "instruction":
        return "只修改现有说明文件；不会修改权限、沙箱或 hooks。"
    if check.kind == "skill":
        return "只新增或修改技能说明；不会修改权限、沙箱或 hooks。"
    if check.kind == "script":
        return "只修改声明范围内的 Python 脚本；已通过静态扫描，无网络/子进程/动态执行。"
    return "只修改声明的 harness 文件；不会修改权限、沙箱或 hooks。"
