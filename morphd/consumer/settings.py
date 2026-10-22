"""Consumer settings, project overrides and their validation."""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any

from .. import store as engine_store
from .errors import SettingsError
from .store import ConsumerStore

TRUST_LEVELS = ("observer", "advisor", "assistant", "autopilot")
CHANGE_APPETITES = ("conservative", "balanced", "bold")
ANALYSIS_DEPTHS = ("adaptive", "light", "full")
DIGEST_MODES = ("off", "daily", "weekly", "only_inbox")
AUTO_APPLY_WINDOWS = ("any", "quiet", "custom")
THEMES = ("system", "light", "dark")
LANGUAGES = ("system", "zh", "en")
FONT_SIZES = ("small", "medium", "large", "xlarge")
CONTRASTS = ("normal", "high")
MOTIONS = ("system", "on", "off")
STYLES = ("concise", "detailed")
PRESETS = ("frugal", "balanced", "quality", "local", "custom")

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "trust_level": "advisor",
    "change_appetite": "conservative",
    "auto_apply": False,
    "auto_apply_window": "any",
    "auto_rollback": True,
    "trial_sessions": 3,
    "undo_days": 7,
    "autopilot_ack": False,
    "budget": {
        "monthly_usd": 5.0,
        "per_session_usd": 0.5,
        "per_job_usd": 1.0,
        "alert_ratio": 0.8,
        "confirm_before_analysis": False,
    },
    "cadence": {
        "focus_mode": True,
        "quiet_hours": ["22:00", "08:00"],
        "weekly_cap": 2,
        "digest": "weekly",
        "system_notifications": False,
        "notification_sound": False,
        "start_minimized": False,
        "suggestion_badge": False,
        "close_window": "tray",
        "idle_delay_minutes": 0,
        "tray_enabled": True,
    },
    "privacy": {
        "telemetry": False,
        "retention_days": 30,
        "excluded_globs": [],
        "local_only": False,
        "auto_discover_hosts": True,
    },
    "ui": {
        "theme": "system",
        "language": "system",
        "font_size": "medium",
        "contrast": "normal",
        "reduced_motion": "system",
        "style": "concise",
        "beginner": True,
        "terminology_tooltips": True,
    },
    "models": {
        "preset": "balanced",
        "proposer": "",
        "composer": "",
        "selector": "",
        "judge": None,
        "fallbacks": [],
    },
    "analysis": {
        "depth": "adaptive",
        "k": 1,
        "modes": "parallel",
    },
    "enterprise": {
        "strict": False,
        "dual_approval": False,
        "container": False,
        "egress_allowlist": [],
    },
}
DEFAULT_PROJECT: dict[str, Any] = {
    "version": 1,
    "id": "",
    "root": "",
    "enabled": True,
    "sensitive": False,
    "local_only": False,
    "domain": "general",
    "excluded_globs": [],
    "retention_days": 30,
    "budget": {},
    "cadence": {},
    "trust_level": None,
    "change_appetite": None,
    "created_at": "",
    "updated_at": "",
}

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def deep_merge(base: dict, override: dict | None) -> dict:
    """Merge stored values over defaults; lists are replaced, unknown keys preserved."""
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _bool(value) -> bool:
    return value is True


def _number(value, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError("预算必须是数字")
    if float(value) < minimum:
        raise SettingsError("预算不能为负数")
    return float(value)


def validate_settings(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise SettingsError("设置必须是 JSON 对象")
    merged = deep_merge(DEFAULT_SETTINGS, payload)
    if merged["trust_level"] not in TRUST_LEVELS:
        raise SettingsError("未知的信任等级")
    if merged["change_appetite"] not in CHANGE_APPETITES:
        raise SettingsError("未知的变更幅度")
    if not isinstance(merged["enabled"], bool):
        raise SettingsError("enabled 必须是布尔值")
    if merged["analysis"]["depth"] not in ANALYSIS_DEPTHS:
        raise SettingsError("未知的分析深度")
    if merged["analysis"]["k"] not in (1, 2, 3):
        raise SettingsError("k 只能是 1–3")
    if merged["analysis"]["modes"] not in ("parallel", "sequential"):
        raise SettingsError("modes 只能是 parallel 或 sequential")
    if merged["auto_apply_window"] not in AUTO_APPLY_WINDOWS:
        raise SettingsError("未知的自动应用时间窗")
    if merged["cadence"]["digest"] not in DIGEST_MODES:
        raise SettingsError("未知的摘要频率")
    if merged["cadence"]["close_window"] not in ("tray", "exit"):
        raise SettingsError("关闭窗口行为只能是 tray 或 exit")
    if not isinstance(merged["cadence"]["tray_enabled"], bool):
        raise SettingsError("cadence.tray_enabled 必须是布尔值")

    trial = merged["trial_sessions"]
    if not isinstance(trial, int) or not 1 <= trial <= 10:
        raise SettingsError("试用会话数必须在 1–10 之间")
    undo = merged["undo_days"]
    if not isinstance(undo, int) or not 1 <= undo <= 30:
        raise SettingsError("撤销窗口必须在 1–30 天之间")
    cap = merged["cadence"]["weekly_cap"]
    if not isinstance(cap, int) or not 0 <= cap <= 10:
        raise SettingsError("每周主动建议上限必须在 0–10 之间")
    idle = merged["cadence"]["idle_delay_minutes"]
    if not isinstance(idle, int) or not 0 <= idle <= 30:
        raise SettingsError("空闲延迟必须在 0–30 分钟之间")

    quiet = merged["cadence"]["quiet_hours"]
    if quiet is not None:
        if not isinstance(quiet, list) or len(quiet) != 2:
            raise SettingsError("静默时段必须包含开始和结束时间")
        for item in quiet:
            if not isinstance(item, str) or not _TIME_RE.match(item):
                raise SettingsError("静默时段格式必须是 HH:MM")

    budget = merged["budget"]
    for key in ("monthly_usd", "per_session_usd", "per_job_usd"):
        budget[key] = _number(budget[key])
    ratio = budget["alert_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= float(ratio) <= 1:
        raise SettingsError("预算提醒比例必须在 0–1 之间")
    budget["alert_ratio"] = float(ratio)
    if not isinstance(budget["confirm_before_analysis"], bool):
        raise SettingsError("confirm_before_analysis 必须是布尔值")
    if budget["per_session_usd"] > budget["monthly_usd"]:
        raise SettingsError("单会话预算不能高于月度预算")

    privacy = merged["privacy"]
    for key in ("telemetry", "local_only", "auto_discover_hosts"):
        if not isinstance(privacy[key], bool):
            raise SettingsError(f"privacy.{key} 必须是布尔值")
    retention = privacy["retention_days"]
    if retention not in (0, 7, 30, 90):
        raise SettingsError("数据保留必须是 7/30/90 天或永久")
    if not isinstance(privacy["excluded_globs"], list) or not all(
        isinstance(g, str) and g.strip() for g in privacy["excluded_globs"]
    ):
        raise SettingsError("排除规则必须是字符串列表")

    ui = merged["ui"]
    if ui["theme"] not in THEMES:
        raise SettingsError("未知主题")
    if ui["language"] not in LANGUAGES:
        raise SettingsError("未知语言")
    if ui["font_size"] not in FONT_SIZES:
        raise SettingsError("未知字号")
    if ui["contrast"] not in CONTRASTS:
        raise SettingsError("未知对比度设置")
    if ui["reduced_motion"] not in MOTIONS:
        raise SettingsError("未知动效设置")
    if ui["style"] not in STYLES:
        raise SettingsError("未知建议风格")
    for key in ("beginner", "terminology_tooltips"):
        if not isinstance(ui[key], bool):
            raise SettingsError(f"ui.{key} 必须是布尔值")

    models = merged["models"]
    if models["preset"] not in PRESETS:
        raise SettingsError("未知模型预设")
    for role in ("proposer", "composer", "selector", "judge"):
        value = models.get(role)
        if value is not None and not isinstance(value, str):
            raise SettingsError(f"models.{role} 必须是字符串或 null")
    if not isinstance(models["fallbacks"], list) or len(models["fallbacks"]) > 3:
        raise SettingsError("fallback 最多 3 个")
    if not all(isinstance(item, str) and item for item in models["fallbacks"]):
        raise SettingsError("fallback 必须是模型引用")

    enterprise = merged["enterprise"]
    for key in ("strict", "dual_approval", "container"):
        if not isinstance(enterprise[key], bool):
            raise SettingsError(f"enterprise.{key} 必须是布尔值")
    if enterprise["strict"] and not enterprise["dual_approval"]:
        merged["enterprise"]["dual_approval"] = True

    if merged["auto_apply"]:
        if merged["trust_level"] not in ("assistant", "autopilot"):
            raise SettingsError("自动应用只能在“助手”或“自动驾驶”等级开启")
        if budget["monthly_usd"] <= 0 or budget["per_job_usd"] <= 0:
            raise SettingsError("开启自动应用前必须设置大于 0 的预算")
        if not merged["auto_rollback"]:
            raise SettingsError("开启自动应用前必须保留自动回滚")
        if not isinstance(merged["autopilot_ack"], bool):
            raise SettingsError("autopilot_ack 必须是布尔值")
    if merged["trust_level"] == "assistant" and merged["change_appetite"] != "conservative":
        if not merged["autopilot_ack"]:
            raise SettingsError("助手等级切换到均衡/大胆幅度需要再次确认")
    if merged["trust_level"] == "autopilot":
        if not merged["auto_apply"] or not merged["autopilot_ack"]:
            raise SettingsError("自动驾驶需要显式确认、自动应用和预算锁")
        if merged["undo_days"] < 30:
            raise SettingsError("自动驾驶要求至少 30 天撤销窗口")
        merged["change_appetite"] = "conservative"
    if merged["trust_level"] == "observer":
        merged["auto_apply"] = False
    return merged


def validate_project(payload: dict, *, root: Path | None = None) -> dict:
    merged = deep_merge(DEFAULT_PROJECT, payload)
    if root is not None:
        merged["root"] = str(Path(root).resolve())
    if not merged["root"]:
        raise SettingsError("项目必须包含 root")
    for key in ("enabled", "sensitive", "local_only"):
        if not isinstance(merged[key], bool):
            raise SettingsError(f"project.{key} 必须是布尔值")
    if merged["trust_level"] not in (None, *TRUST_LEVELS):
        raise SettingsError("未知的项目信任等级")
    if merged["change_appetite"] not in (None, *CHANGE_APPETITES):
        raise SettingsError("未知的项目变更幅度")
    retention = merged["retention_days"]
    if not isinstance(retention, int) or retention not in (0, 7, 30, 90):
        raise SettingsError("项目数据保留必须是 7/30/90 天或永久")
    if not isinstance(merged["excluded_globs"], list):
        raise SettingsError("排除规则必须是列表")
    if not isinstance(merged["domain"], str) or not merged["domain"].strip():
        raise SettingsError("domain 不能为空")
    return merged


def parse_role(ref: str | None) -> tuple[str, str] | None:
    if not ref or not isinstance(ref, str):
        return None
    if "/" not in ref:
        return None
    provider_id, model = ref.split("/", 1)
    if not provider_id or not model:
        return None
    return provider_id, model


class SettingsManager:
    def __init__(self, store: ConsumerStore):
        self.store = store

    def load(self) -> dict:
        raw = self.store.read_json(self.store.config_path, default={})
        merged = deep_merge(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})
        try:
            return validate_settings(merged)
        except SettingsError:
            # Never make the UI unusable because one stored field is bad; the diagnostics page
            # shows the raw value and the user can reset it.
            return deep_merge(DEFAULT_SETTINGS, {})

    def save(self, payload: dict) -> dict:
        current = self.load()
        merged = deep_merge(current, payload)
        validated = validate_settings(merged)
        with self.store.lock("settings"):
            self.store.write_json(self.store.config_path, validated)
        self.store.append_log("settings_updated", keys=sorted(payload))
        return validated

    def reset(self) -> dict:
        with self.store.lock("settings"):
            self.store.write_json(self.store.config_path, DEFAULT_SETTINGS)
        return deep_merge(DEFAULT_SETTINGS, {})


class ProjectManager:
    def __init__(self, store: ConsumerStore):
        self.store = store

    @staticmethod
    def project_id(root: Path) -> str:
        digest = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()
        return f"proj_{digest[:12]}"

    @staticmethod
    def discover_root(path: Path) -> Path:
        path = Path(path).expanduser().resolve()
        if path.is_file():
            path = path.parent
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                return candidate
        return path

    def for_root(self, root: Path, *, create: bool = True) -> dict:
        root = self.discover_root(root)
        pid = self.project_id(root)
        payload = self.store.read_record("projects", pid)
        if isinstance(payload, dict):
            return validate_project(payload, root=root)
        if not create:
            return validate_project({**DEFAULT_PROJECT, "id": pid, "root": str(root)}, root=root)
        payload = validate_project(
            {
                **DEFAULT_PROJECT,
                "id": pid,
                "root": str(root),
                "created_at": engine_store.utc_now(),
                "updated_at": engine_store.utc_now(),
            },
            root=root,
        )
        self.store.write_record("projects", pid, payload)
        return payload

    def save(self, project_id: str, payload: dict) -> dict:
        existing = self.store.read_record("projects", project_id)
        if not isinstance(existing, dict):
            raise SettingsError("项目不存在")
        merged = deep_merge(existing, payload)
        merged["id"] = project_id
        merged["updated_at"] = engine_store.utc_now()
        validated = validate_project(merged)
        self.store.write_record("projects", project_id, validated)
        return validated

    def list(self) -> list[dict]:
        return [p for p in self.store.list_records("projects") if p.get("id")]

    def delete(self, project_id: str) -> None:
        self.store.delete_record("projects", project_id)


def effective_settings(settings: dict, project: dict | None) -> dict:
    """Project-level overrides for the fields the PRD allows to differ."""
    if not isinstance(project, dict):
        return settings
    merged = copy.deepcopy(settings)
    merged["trust_level"] = project.get("trust_level") or settings["trust_level"]
    merged["change_appetite"] = project.get("change_appetite") or settings["change_appetite"]
    if project.get("sensitive"):
        merged["privacy"]["local_only"] = bool(project.get("local_only"))
        merged["auto_apply"] = False
    # Project overrides must never grant more automatic power than the global setting.
    if merged.get("trust_level") == "autopilot":
        merged["change_appetite"] = "conservative"
    if merged.get("trust_level") == "assistant" and merged.get("auto_apply"):
        if merged.get("change_appetite") != "conservative":
            merged["auto_apply"] = False
    if project.get("local_only"):
        merged["privacy"]["local_only"] = True
    excluded = list(settings["privacy"].get("excluded_globs", []))
    for glob in project.get("excluded_globs", []) or []:
        if glob not in excluded:
            excluded.append(glob)
    merged["privacy"]["excluded_globs"] = excluded
    if project.get("retention_days") is not None:
        merged["privacy"]["retention_days"] = project["retention_days"]
    for key in ("monthly_usd", "per_session_usd", "per_job_usd", "alert_ratio"):
        value = (project.get("budget") or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            merged["budget"][key] = float(value)
    for key in ("weekly_cap", "quiet_hours", "digest", "focus_mode"):
        value = (project.get("cadence") or {}).get(key)
        if value is not None:
            merged["cadence"][key] = value
    return merged


def ensure_default_files(store: ConsumerStore) -> None:
    manager = SettingsManager(store)
    if not store.config_path.exists():
        manager.save(DEFAULT_SETTINGS)
