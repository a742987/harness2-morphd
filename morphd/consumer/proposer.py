"""BYOK recursive proposer with a root-locked tool mode and a JSON patch fallback.

Both modes edit a temporary copy of the current harness. The caller validates the result
against the change-appetite policy before anything reaches the real harness.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from .. import store as engine_store
from .errors import ConsumerError
from .providers import Completion, ProviderClient, ProvidersManager
from .util import diff_trees, is_binary, is_secret_path, new_id, read_tree, safe_rel

MAX_TOOL_ROUNDS = 8
MAX_WRITES = 10
MAX_WRITE_BYTES = 1_000_000
MAX_READ_BYTES = 200_000
MAX_LIST_ENTRIES = 2_000
MAX_CONTEXT_CHARS = 180_000

TOOLS = [
    {
        "name": "list_dir",
        "description": "List files under a directory in the temporary harness.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file in the temporary harness.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "Write a complete UTF-8 file (creates parents).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_patch",
        "description": "Apply a unified diff to one file in the temporary harness.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "patch": {"type": "string"},
            },
            "required": ["path", "patch"],
            "additionalProperties": False,
        },
    },
]


class WorkspaceGuard:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path(self, rel: str, *, must_exist: bool = False) -> Path:
        value = safe_rel(rel)
        if is_secret_path(value):
            raise ConsumerError("CANDIDATE_UNSAFE", "不能读取或写入敏感文件。", "改用普通 harness 文件。")
        target = (self.root / value).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ConsumerError("CANDIDATE_UNSAFE", "路径越过了临时工作区。", "只使用相对路径。") from exc
        if target.is_symlink():
            raise ConsumerError("CANDIDATE_UNSAFE", "不允许符号链接。", "改用常规文件。")
        if must_exist and not target.is_file():
            raise ConsumerError("INVALID_REQUEST", f"文件不存在：{value}", "先用 list_dir 查看。")
        return target


def _apply_hunks(text: str, diff: str, rel: str) -> str:
    """Apply a small unified diff with exact context matching."""
    lines = text.splitlines(keepends=True)
    src = diff.splitlines()
    hunks = []
    index = 0
    while index < len(src):
        line = src[index]
        if not line.startswith("@@"):
            index += 1
            continue
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            raise ConsumerError("CANDIDATE_UNSAFE", f"{rel}: 无效的 diff 头", "让模型返回标准 unified diff。")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        index += 1
        body = []
        while index < len(src) and not src[index].startswith("@@"):
            body.append(src[index])
            index += 1
        hunks.append((old_start, old_count, body))
    if not hunks:
        raise ConsumerError("CANDIDATE_UNSAFE", f"{rel}: diff 中没有可应用的 hunk", "重试。")
    offset = 0
    for old_start, old_count, body in hunks:
        old_lines = []
        new_lines = []
        for raw in body:
            if raw.startswith("\\"):
                continue
            marker = raw[:1]
            content = raw[1:] if marker in (" ", "+", "-") else raw
            if marker == " ":
                old_lines.append(content + "\n")
                new_lines.append(content + "\n")
            elif marker == "-":
                old_lines.append(content + "\n")
            elif marker == "+":
                new_lines.append(content + "\n")
        if old_count and len(old_lines) != old_count:
            # trailing newline differences are common; keep the content-driven count
            pass
        position = max(0, old_start - 1 + offset)
        window = lines[position : position + len(old_lines)]
        if window != old_lines:
            # search nearby for an exact match before failing
            found = -1
            for candidate in range(max(0, position - 20), min(len(lines), position + 20)):
                if lines[candidate : candidate + len(old_lines)] == old_lines:
                    found = candidate
                    break
            if found < 0:
                raise ConsumerError(
                    "CANDIDATE_UNSAFE",
                    f"{rel}: diff 上下文不匹配，整批改动已拒绝。",
                    "让模型重新生成补丁。",
                )
            position = found
        lines[position : position + len(old_lines)] = new_lines
        offset += len(new_lines) - len(old_lines)
    return "".join(lines)


class BYOKProposer:
    def __init__(
        self,
        *,
        store,
        managers: ProvidersManager,
        clients: list[ProviderClient],
        work_root: Path,
        charge: Callable[[Completion, str], None] | None = None,
        precall: Callable[[str], None] | None = None,
        cache_root: Path | None = None,
        max_rounds: int = MAX_TOOL_ROUNDS,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.store = store
        self.managers = managers
        self.clients = clients
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.charge = charge
        self.precall = precall
        self.cache_root = Path(cache_root or (store.home / "runtime" / "cache" / "proposals"))
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.max_rounds = max_rounds
        self.should_stop = should_stop

    def _cache_key(self, mission: str, system: str, seed_dir: Path, label: str) -> str:
        tree = read_tree(seed_dir)
        digest = hashlib.sha256()
        digest.update(mission.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(system.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(label.encode("utf-8", errors="replace"))
        for rel, text in sorted(tree.items()):
            digest.update(rel.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(text.encode("utf-8", errors="replace")).digest())
        for client in self.clients:
            digest.update(client.provider["id"].encode())
            digest.update(client.provider["model"].encode())
        return digest.hexdigest()

    def _cache_get(self, key: str, output: Path) -> dict | None:
        meta_path = self.cache_root / key / "meta.json"
        tree_dir = self.cache_root / key / "tree"
        meta = engine_store.read_json(meta_path)
        if not isinstance(meta, dict) or not tree_dir.is_dir():
            return None
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(tree_dir, output)
        return {
            "text": meta.get("text", ""),
            "ok": bool(meta.get("ok")),
            "cost_usd": 0.0,
            "attempts": 0,
            "cached": True,
            "workspace": str(output),
            "tree": read_tree(output),
            "summary": meta.get("summary", ""),
            "notes": meta.get("notes", ""),
            "usage": {},
            "mode": meta.get("mode", "cache"),
            "error": None,
        }

    def _cache_put(self, key: str, result: dict, tree_dir: Path) -> None:
        target = self.cache_root / key
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copytree(tree_dir, target / "tree")
        engine_store.atomic_write_json(
            target / "meta.json",
            {
                "version": 1,
                "text": result.get("text", ""),
                "summary": result.get("summary", ""),
                "notes": result.get("notes", ""),
                "ok": result.get("ok", False),
                "mode": result.get("mode", ""),
                "at": engine_store.utc_now(),
            },
        )

    def call(
        self,
        mission: str,
        context_dir: Path,
        label: str,
        *,
        system: str = "",
        timeout: float = 1800,
        max_attempts: int = 2,
        harvest: Callable | None = None,
    ) -> dict:
        seed = Path(context_dir).resolve()
        if not seed.is_dir():
            raise ConsumerError("INTERNAL", "提案上下文不存在。", "重试分析。")
        output = self.work_root / f"{safe_rel(label).replace('/', '_')}-{new_id('p')}"
        key = self._cache_key(mission, system, seed, label)
        cached = self._cache_get(key, output)
        if cached is not None:
            self.store.append_log("proposal_cache_hit", label=label)
            return cached
        shutil.copytree(seed, output)
        guard = WorkspaceGuard(output)
        before_hash = engine_store.tree_hash(output)
        cost_total = 0.0
        usage_total: dict[str, int] = {}
        mode = "tool"

        def record(completion: Completion, phase: str) -> None:
            nonlocal cost_total
            if completion.usage:
                for k, v in completion.usage.items():
                    if isinstance(v, int):
                        usage_total[k] = usage_total.get(k, 0) + v
            if self.charge is not None:
                self.charge(completion, phase)
            cost_total += float(completion.cost_usd or 0.0)

        # Tool mode first; fall back to patch mode if the endpoint rejects tools.
        try:
            tool_result = self._tool_loop(
                mission=mission,
                system=system,
                guard=guard,
                label=label,
                output=output,
                before_hash=before_hash,
                record=record,
                timeout=timeout,
            )
            if tool_result is not None:
                result = tool_result
            else:
                mode = "patch"
                result = self._patch_mode(
                    mission=mission,
                    system=system,
                    guard=guard,
                    label=label,
                    output=output,
                    before_hash=before_hash,
                    record=record,
                )
        except ConsumerError as exc:
            if exc.code in (
                "CANDIDATE_UNSAFE",
                "PROVIDER_AUTH_FAILED",
                "PROVIDER_RATE_LIMITED",
                "PROVIDER_NOT_CONFIGURED",
                "BUDGET_EXCEEDED",
                "BUDGET_UNKNOWN_PRICE",
                "JOB_CANCELED",
                "VAULT_LOCKED",
            ):
                raise
            self.store.append_log("proposal_tool_mode_failed", label=label, error=exc.message)
            mode = "patch"
            result = self._patch_mode(
                mission=mission,
                system=system,
                guard=guard,
                label=label,
                output=output,
                before_hash=before_hash,
                record=record,
            )

        result.update(
            {
                "cost_usd": round(cost_total, 8),
                "usage": usage_total,
                "workspace": str(output),
                "tree": read_tree(output),
                "cached": False,
                "mode": mode,
            }
        )
        if harvest is not None:
            try:
                harvested = harvest(output)
                if isinstance(harvested, dict):
                    result["harvest"] = harvested
            except Exception as exc:  # harvest failure must not masquerade as success
                result["ok"] = False
                result["error"] = f"harvest failed: {exc}"
        # Cache only after harvest and validation succeeded; a failed result must never be
        # replayed as a successful candidate on the next run.
        if result.get("ok"):
            self._cache_put(key, result, output)
        return result

    def _tool_loop(
        self,
        *,
        mission: str,
        system: str,
        guard: WorkspaceGuard,
        label: str,
        output: Path,
        before_hash: str,
        record,
        timeout: float,
    ) -> dict | None:
        tree = read_tree(guard.root)
        names = sorted(tree)
        file_list = "\n".join(f"- {rel}" for rel in names[:400])
        if len(names) > 400:
            file_list += f"\n… {len(names) - 400} more files (not listed)"
        context_chunks = []
        remaining = MAX_CONTEXT_CHARS
        for rel in names:
            text = tree[rel]
            if remaining <= 0:
                context_chunks.append("\n…[remaining files omitted: context limit]")
                break
            body = text[:remaining]
            suffix = "\n…[truncated: context limit]" if len(text) > len(body) else ""
            piece = f"\n===== {rel} =====\n{body}{suffix}"
            context_chunks.append(piece)
            remaining -= len(piece)
        messages = [
            {
                "role": "system",
                "content": system
                or (
                    "You improve an agent harness. Edit only the temporary harness with the "
                    "provided tools. Never touch permissions, sandbox, hooks, settings, MCP, "
                    "credentials or paths outside the workspace. Make the smallest effective "
                    "change, then stop. Do not explain at length."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{mission}\n\nHarness files:\n{file_list}\n\nCurrent contents:"
                    f"{''.join(context_chunks)}\n\nUse tools to edit the harness. When finished, "
                    "reply with a short summary. Files you did not edit stay unchanged."
                ),
            },
        ]
        wrote = 0
        deadline = time.monotonic() + max(1.0, float(timeout))
        finished_cleanly = False
        for round_index in range(self.max_rounds):
            if self.should_stop and self.should_stop():
                raise ConsumerError("JOB_CANCELED", "任务已取消。", "下次宿主启动时可重新分析。")
            if time.monotonic() >= deadline:
                return {
                    "text": "",
                    "ok": False,
                    "summary": "",
                    "notes": "提案超时，已停止且没有生成候选。",
                    "attempts": round_index,
                    "error": "timeout",
                }
            if self.precall is not None:
                self.precall(f"propose-tool-{round_index}")
            try:
                completion = ProvidersManager.complete_with_fallback(
                    self.clients,
                    messages,
                    tools=TOOLS,
                    max_tokens=min(8192, int(self.clients[0].provider.get("max_output", 8192))),
                    temperature=0.0,
                )
            except ConsumerError as exc:
                if exc.code == "PROVIDER_UNAVAILABLE" and round_index == 0:
                    raise
                if round_index == 0:
                    raise ConsumerError(
                        "PROVIDER_UNAVAILABLE",
                        "模型没有接受工具模式。",
                        "将使用补丁模式重试。",
                    ) from exc
                raise
            record(completion, f"propose-tool-{round_index}")
            if not completion.tool_calls:
                text = completion.text or ""
                if wrote == 0 and text.strip().startswith("{"):
                    applied = self._apply_json_patch(text, guard)
                    if applied:
                        wrote += applied
                finished_cleanly = True
                break
            for call in completion.tool_calls:
                try:
                    payload = self._execute_tool(call, guard, label=label)
                except ConsumerError as exc:
                    payload = {"ok": False, "error": exc.message}
                messages.append(
                    {
                        "role": "assistant",
                        "content": completion.text or "",
                        "tool_calls": [call],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "tool_name": call.get("name", ""),
                        "content": json.dumps(payload, ensure_ascii=False)[:200_000],
                    }
                )
                if call.get("name") in ("write_file", "apply_patch") and payload.get("ok"):
                    wrote += 1
                if wrote > MAX_WRITES:
                    raise ConsumerError(
                        "CANDIDATE_UNSAFE",
                        "提案改动的文件太多，已拒绝。",
                        "选择更保守的变更幅度。",
                    )
        if not finished_cleanly:
            return {
                "text": "",
                "ok": False,
                "summary": "",
                "notes": f"超过 {self.max_rounds} 轮工具调用上限，已停止且没有生成候选。",
                "attempts": self.max_rounds,
                "error": "tool_round_limit",
            }
        after_hash = engine_store.tree_hash(guard.root)
        if after_hash == before_hash:
            return {
                "text": "",
                "ok": False,
                "summary": "",
                "notes": "模型没有修改任何文件。",
                "attempts": round_index + 1,
                "error": "no_changes",
            }
        if wrote == 0:
            # The model wrote directly through a tool that we did not count; trust the tree hash.
            wrote = len(diff_trees(read_tree(Path(guard.root)), {})[1])
        summary = completion.text.strip()[:800] if "completion" in locals() else ""
        return {
            "text": summary,
            "ok": True,
            "summary": summary or "Updated the harness.",
            "notes": "",
            "attempts": round_index + 1,
            "error": None,
        }

    def _apply_json_patch(self, text: str, guard: WorkspaceGuard) -> int:
        files = self._parse_patch_json(text)
        if files is None:
            return 0
        self._write_patch_files(files, guard)
        return len(files)

    @staticmethod
    def _parse_patch_json(text: str) -> list[dict] | None:
        raw = text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            payload = json.loads(raw)
        except ValueError:
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                return None
            try:
                payload = json.loads(match.group(0))
            except ValueError:
                return None
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            return None
        out = []
        for item in files:
            if not isinstance(item, dict):
                return None
            path = item.get("path")
            content = item.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                return None
            out.append({"path": path, "content": content})
        return out

    def _write_patch_files(self, files: list[dict], guard: WorkspaceGuard) -> None:
        if len(files) > MAX_WRITES:
            raise ConsumerError("CANDIDATE_UNSAFE", "补丁文件数超过上限。", "减少改动。")
        total = 0
        validated = []
        for item in files:
            rel = safe_rel(item["path"])
            content = item["content"]
            data = content.encode("utf-8", errors="replace")
            if is_binary(data):
                raise ConsumerError("CANDIDATE_UNSAFE", f"{rel}: 不允许二进制内容。", "改用文本。")
            total += len(data)
            if total > MAX_WRITE_BYTES:
                raise ConsumerError("CANDIDATE_UNSAFE", "补丁总大小超过 1MB。", "减少改动。")
            target = guard.path(rel)
            if target.is_symlink():
                raise ConsumerError("CANDIDATE_UNSAFE", f"{rel}: 不允许符号链接。", "改用常规文件。")
            validated.append((target, content))
        written: list[tuple[Path, bytes | None]] = []
        try:
            for target, content in validated:
                previous = target.read_bytes() if target.is_file() else None
                target.parent.mkdir(parents=True, exist_ok=True)
                engine_store.atomic_write_text(target, content)
                written.append((target, previous))
        except Exception:
            for target, previous in reversed(written):
                try:
                    if previous is None:
                        target.unlink()
                    else:
                        engine_store.atomic_write_bytes(target, previous)
                except OSError:
                    pass
            raise

    def _patch_mode(
        self,
        *,
        mission: str,
        system: str,
        guard: WorkspaceGuard,
        label: str,
        output: Path,
        before_hash: str,
        record,
    ) -> dict:
        tree = read_tree(guard.root)
        names = sorted(tree)
        file_list = "\n".join(f"- {rel}" for rel in names[:400])
        if len(names) > 400:
            file_list += f"\n… {len(names) - 400} more files (not listed)"
        contents = []
        remaining = MAX_CONTEXT_CHARS
        for rel in names:
            if remaining <= 0:
                contents.append("\n…[remaining files omitted: context limit]")
                break
            text = tree[rel]
            body = text[:remaining]
            suffix = "\n…[truncated: context limit]" if len(text) > len(body) else ""
            chunk = f"\n===== {rel} =====\n{body}{suffix}"
            contents.append(chunk)
            remaining -= len(chunk)
        messages = [
            {
                "role": "system",
                "content": system
                or (
                    "You improve an agent harness. Return ONLY JSON with complete file contents. "
                    "Never touch permissions, sandbox, hooks, settings, MCP or credentials."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{mission}\n\nHarness files:\n{file_list}\n\nCurrent contents:"
                    f"{''.join(contents)}\n\nReturn JSON exactly like "
                    '{"files":[{"path":"AGENTS.md","content":"..."}],"summary":"...","notes":"..."} '
                    "with the complete new content for each changed file. Change as few files as "
                    "possible. Do not include unchanged files."
                ),
            },
        ]
        if self.precall is not None:
            self.precall("propose-patch")
        completion = ProvidersManager.complete_with_fallback(
            self.clients,
            messages,
            response_format="json",
            max_tokens=min(8192, int(self.clients[0].provider.get("max_output", 8192))),
            temperature=0.0,
        )
        record(completion, "propose-patch")
        files = self._parse_patch_json(completion.text or "")
        summary = ""
        notes = ""
        if not files:
            try:
                payload = json.loads(completion.text or "{}")
                summary = str(payload.get("summary", ""))[:800]
                notes = str(payload.get("notes", ""))[:2000]
            except ValueError:
                pass
            return {
                "text": completion.text or "",
                "ok": False,
                "summary": summary,
                "notes": notes or "模型没有返回有效的文件补丁。",
                "attempts": 1,
                "error": "no_valid_patch",
            }
        self._write_patch_files(files, guard)
        after_hash = engine_store.tree_hash(guard.root)
        try:
            payload = json.loads(completion.text or "{}")
            summary = str(payload.get("summary", ""))[:800]
            notes = str(payload.get("notes", ""))[:2000]
        except ValueError:
            pass
        return {
            "text": completion.text or "",
            "ok": after_hash != before_hash,
            "summary": summary or "Updated the harness.",
            "notes": notes,
            "attempts": 1,
            "error": None if after_hash != before_hash else "no_changes",
        }

    # --- tools ---------------------------------------------------------------
    def _execute_tool(self, call: dict, guard: WorkspaceGuard, *, label: str) -> dict:
        name = call.get("name")
        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        if name == "list_dir":
            rel = str(args.get("path", "") or "")
            target = guard.path(rel) if rel else guard.root
            if not target.is_dir():
                return {"ok": False, "error": "not a directory"}
            entries = []
            for child in sorted(target.iterdir())[:MAX_LIST_ENTRIES]:
                if child.is_symlink():
                    continue
                entries.append(
                    {
                        "path": child.relative_to(guard.root).as_posix(),
                        "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None,
                    }
                )
            return {"ok": True, "entries": entries}
        if name == "read_file":
            rel = str(args.get("path", ""))
            target = guard.path(rel, must_exist=True)
            limit = min(MAX_READ_BYTES, int(args.get("max_bytes", MAX_READ_BYTES) or MAX_READ_BYTES))
            data = target.read_bytes()[:limit]
            if is_binary(data):
                return {"ok": False, "error": "binary file"}
            return {
                "ok": True,
                "path": rel,
                "content": data.decode("utf-8", errors="replace"),
                "truncated": target.stat().st_size > limit,
            }
        if name == "write_file":
            rel = safe_rel(str(args.get("path", "")))
            content = str(args.get("content", ""))
            self._write_patch_files([{"path": rel, "content": content}], guard)
            return {"ok": True, "path": rel}
        if name == "apply_patch":
            rel = safe_rel(str(args.get("path", "")))
            patch = str(args.get("patch", ""))
            target = guard.path(rel, must_exist=True)
            text = target.read_text(encoding="utf-8", errors="replace")
            updated = _apply_hunks(text, patch, rel)
            self._write_patch_files([{"path": rel, "content": updated}], guard)
            return {"ok": True, "path": rel}
        return {"ok": False, "error": f"unknown tool {name!r}"}
