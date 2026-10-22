"""BYOK provider profiles, HTTP clients and connection tests."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .. import sanitize
from .. import store as engine_store
from .errors import ConsumerError
from .keychain import KeyVault
from .settings import parse_role
from .store import ConsumerStore
from .util import new_id

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_OUTPUT = 4096


@dataclass
class Completion:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    cost_estimated: bool = False
    latency_ms: int = 0
    finish_reason: str = ""
    provider_id: str = ""
    model: str = ""
    fallback_used: bool = False

    @property
    def cost_known(self) -> bool:
        return self.cost_usd is not None


def _is_local_url(base_url: str) -> bool:
    try:
        host = urllib.parse.urlparse(base_url).hostname or ""
    except ValueError:
        return False
    if host in ("localhost", "::1", "0.0.0.0"):
        return True
    try:
        return socket.gethostbyname(host).startswith("127.")
    except OSError:
        return False


def validate_provider(payload: dict, *, provider_id: str = "") -> dict:
    if not isinstance(payload, dict):
        raise ConsumerError("INVALID_REQUEST", "服务商配置必须是对象。", "检查输入。")
    protocol = str(payload.get("protocol", ""))
    if protocol not in ("openai-compatible", "anthropic", "gemini"):
        raise ConsumerError("INVALID_REQUEST", "不支持的服务商协议。", "选择 OpenAI、Anthropic、Gemini 或兼容端点。")
    base_url = str(payload.get("base_url", "")).strip().rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConsumerError("INVALID_REQUEST", "Base URL 无效。", "填写完整的 http(s) 地址。")
    if parsed.username or parsed.password:
        raise ConsumerError("INVALID_REQUEST", "Base URL 不能包含凭据。", "把 Key 填到单独的字段。")
    model = str(payload.get("model", "")).strip()
    if not model:
        raise ConsumerError("PROVIDER_MODEL_MISSING", "还没有填写模型 ID。", "填写服务商文档中的模型名。")
    price = payload.get("price") or {}
    if not isinstance(price, dict):
        raise ConsumerError("INVALID_REQUEST", "价格配置必须是对象。", "填写输入/输出价格。")
    clean_price = {}
    for key in (
        "input_per_million",
        "cached_input_per_million",
        "cache_write_per_million",
        "output_per_million",
        "reasoning_per_million",
        "per_call_usd",
    ):
        try:
            value = float(price.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            raise ConsumerError("INVALID_REQUEST", f"价格字段 {key} 无效。", "填写非负数字。")
        if value < 0:
            raise ConsumerError("INVALID_REQUEST", "价格不能为负数。", "填写非负数字。")
        clean_price[key] = value
    clean_price["confirmed"] = bool(price.get("confirmed", False))
    local = _is_local_url(base_url) or bool(payload.get("local"))
    if local and not clean_price.get("confirmed"):
        clean_price["confirmed"] = True
    context_window = payload.get("context_window", 128_000)
    max_output = payload.get("max_output", DEFAULT_MAX_OUTPUT)
    try:
        context_window = max(1024, int(context_window))
        max_output = max(64, int(max_output))
    except (TypeError, ValueError):
        raise ConsumerError("INVALID_REQUEST", "上下文/输出上限必须是数字。", "检查模型参数。")
    supports_tools = bool(payload.get("supports_tools", protocol != "openai-compatible"))
    supports_json = bool(payload.get("supports_json", True))
    data_policy_ack = bool(payload.get("data_policy_ack", local))
    if not data_policy_ack:
        raise ConsumerError(
            "INVALID_REQUEST",
            "首次启用服务商前需要确认数据政策。",
            "阅读数据会发送给谁，然后勾选确认。",
        )
    help_url = str(payload.get("help_url") or "").strip()
    if help_url:
        parsed_help = urllib.parse.urlparse(help_url)
        if parsed_help.scheme not in ("http", "https") or not parsed_help.hostname:
            help_url = ""
    result = {
        "id": provider_id or payload.get("id") or new_id("p"),
        "label": str(payload.get("label") or "Custom").strip()[:80],
        "protocol": protocol,
        "base_url": base_url,
        "model": model,
        "context_window": context_window,
        "max_output": max_output,
        "supports_tools": supports_tools,
        "supports_json": supports_json,
        "price": clean_price,
        "data_policy": str(payload.get("data_policy") or "")[:2000],
        "data_policy_ack": True,
        "help_url": help_url,
        "local": local,
        "key_ref": str(payload.get("key_ref") or ""),
        "key_hint": str(payload.get("key_hint") or ""),
        "created_at": payload.get("created_at") or engine_store.utc_now(),
        "updated_at": engine_store.utc_now(),
    }
    return result


def price_known(provider: dict) -> bool:
    price = provider.get("price") or {}
    if price.get("confirmed"):
        return True
    return any(float(price.get(k, 0) or 0) > 0 for k in price if k != "confirmed")


def compute_cost(provider: dict, usage: dict) -> tuple[float | None, bool]:
    price = provider.get("price") or {}
    if not price_known(provider):
        return None, True
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cached = int(usage.get("cached_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_write_tokens", 0) or 0)
    output = int(usage.get("output_tokens", 0) or 0)
    reasoning = int(usage.get("reasoning_tokens", 0) or 0)
    billable_input = max(0, input_tokens - cached - cache_write)
    cost = (
        billable_input * float(price.get("input_per_million", 0)) / 1_000_000
        + cached * float(price.get("cached_input_per_million", 0)) / 1_000_000
        + cache_write * float(price.get("cache_write_per_million", 0)) / 1_000_000
        + output * float(price.get("output_per_million", 0)) / 1_000_000
        + max(0, reasoning - output) * float(price.get("reasoning_per_million", 0)) / 1_000_000
        + float(price.get("per_call_usd", 0))
    )
    return round(cost, 8), not bool(price.get("confirmed"))


def estimate_analysis_usd(provider: dict, *, context_chars: int, calls: int = 3) -> float | None:
    if not price_known(provider):
        return None
    price = provider.get("price") or {}
    input_tokens = max(2_000, min(60_000, context_chars // 4 + 4_000))
    output_tokens = min(4_000, int(provider.get("max_output", DEFAULT_MAX_OUTPUT)))
    per_call = (
        input_tokens * float(price.get("input_per_million", 0)) / 1_000_000
        + output_tokens * float(price.get("output_per_million", 0)) / 1_000_000
        + float(price.get("per_call_usd", 0))
    )
    return round(per_call * max(1, calls), 8)


def _split_base(provider: dict, suffix: str) -> str:
    base = provider["base_url"].rstrip("/")
    if base.endswith(suffix):
        return base
    return base + suffix


class ProviderClient:
    def __init__(self, provider: dict, key: str | None):
        self.provider = provider
        self.key = key or ""
        self.timeout = int(provider.get("timeout_s", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        self._opener = urllib.request.build_opener()

    # --- wire formats ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "morphd/0.5",
        }
        if self.provider["protocol"] == "anthropic":
            if self.key:
                headers["x-api-key"] = self.key
            headers["anthropic-version"] = ANTHROPIC_VERSION
        elif self.provider["protocol"] == "gemini":
            if self.key:
                headers["x-goog-api-key"] = self.key
        else:
            if self.key:
                headers["Authorization"] = f"Bearer {self.key}"
        return headers

    def _post(self, url: str, payload: dict, *, retries: int = 2) -> tuple[dict, int]:
        data = json.dumps(payload).encode("utf-8")
        last: Exception | None = None
        for attempt in range(retries + 1):
            started = time.monotonic()
            req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    latency = int((time.monotonic() - started) * 1000)
                    try:
                        return json.loads(body.decode("utf-8", errors="replace") or "{}"), latency
                    except ValueError:
                        raise ConsumerError(
                            "PROVIDER_UNAVAILABLE",
                            "模型服务返回了无法解析的响应。",
                            "检查 Base URL 是否指向正确的 API。",
                        )
            except urllib.error.HTTPError as exc:
                body = exc.read()[:4000].decode("utf-8", errors="replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise self._http_error(exc.code, body) from None
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last = exc
                if attempt < retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise ConsumerError(
                    "PROVIDER_UNAVAILABLE",
                    "无法连接模型服务。",
                    "检查网络和 Base URL；harness2-morphd 不会在后台反复重试。",
                ) from exc
        raise ConsumerError(
            "PROVIDER_UNAVAILABLE", "模型服务暂时不可用。", "稍后重试或切换 fallback。"
        ) from last

    def _get(self, url: str) -> tuple[dict, int]:
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        started = time.monotonic()
        try:
            with self._opener.open(req, timeout=min(self.timeout, 20)) as resp:
                body = resp.read()
                return json.loads(body.decode("utf-8", errors="replace") or "{}"), int(
                    (time.monotonic() - started) * 1000
                )
        except urllib.error.HTTPError as exc:
            body = exc.read()[:4000].decode("utf-8", errors="replace")
            raise self._http_error(exc.code, body) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise ConsumerError(
                "PROVIDER_UNAVAILABLE",
                "无法连接模型服务。",
                "检查网络和 Base URL。",
            ) from exc

    def _http_error(self, status: int, body: str) -> ConsumerError:
        safe, _labels = sanitize.redact_text(body)
        detail = safe[:500]
        if self.key:
            detail = detail.replace(self.key, "…")
        if status in (401, 403):
            return ConsumerError(
                "PROVIDER_AUTH_FAILED",
                "这个 Key 无效、过期或没有权限。",
                "到模型页更新 Key；修复前不会产生费用。",
                details={"status": status, "detail": detail},
            )
        if status == 404:
            return ConsumerError(
                "PROVIDER_MODEL_MISSING",
                "服务商或模型不存在。",
                "检查模型 ID 与 Base URL。",
                details={"status": status, "detail": detail},
            )
        if status == 429:
            return ConsumerError(
                "PROVIDER_RATE_LIMITED",
                "服务商限流了。",
                "稍后重试，或降低分析频率。",
                details={"status": status, "detail": detail},
            )
        return ConsumerError(
            "PROVIDER_UNAVAILABLE",
            f"模型服务返回 {status}。",
            "检查服务商状态和模型配置。",
            details={"status": status, "detail": detail},
        )

    @staticmethod
    def _normalise_usage(protocol: str, raw: dict) -> dict:
        usage: dict[str, Any] = {}
        if protocol == "anthropic":
            usage = {
                "input_tokens": raw.get("input_tokens", 0),
                "output_tokens": raw.get("output_tokens", 0),
                "cached_input_tokens": raw.get("cache_read_input_tokens", 0),
                "cache_write_tokens": raw.get("cache_creation_input_tokens", 0),
            }
        elif protocol == "gemini":
            meta = raw.get("usageMetadata", {}) or {}
            usage = {
                "input_tokens": meta.get("promptTokenCount", 0),
                "output_tokens": meta.get("candidatesTokenCount", 0),
                "cached_input_tokens": meta.get("cachedContentTokenCount", 0),
                "reasoning_tokens": meta.get("thoughtsTokenCount", 0),
            }
        else:
            details = raw.get("prompt_tokens_details") or {}
            out_details = raw.get("completion_tokens_details") or {}
            usage = {
                "input_tokens": raw.get("prompt_tokens", 0),
                "output_tokens": raw.get("completion_tokens", 0),
                "cached_input_tokens": details.get("cached_tokens", 0),
                "reasoning_tokens": out_details.get("reasoning_tokens", 0),
            }
        usage = {k: int(v or 0) for k, v in usage.items()}
        usage["present"] = any(usage.values())
        return usage

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        response_format: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        protocol = self.provider["protocol"]
        if protocol == "anthropic":
            return self._complete_anthropic(messages, tools, max_tokens, temperature)
        if protocol == "gemini":
            return self._complete_gemini(messages, tools, response_format, max_tokens, temperature)
        return self._complete_openai(messages, tools, response_format, max_tokens, temperature)

    def _finish(self, text: str, tool_calls: list[dict], usage: dict, latency: int, finish: str,
                started: float) -> Completion:
        cost, estimated = compute_cost(self.provider, usage)
        return Completion(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=cost,
            cost_estimated=estimated,
            latency_ms=latency or int((time.monotonic() - started) * 1000),
            finish_reason=finish,
            provider_id=self.provider["id"],
            model=self.provider["model"],
        )

    def _complete_openai(self, messages, tools, response_format, max_tokens, temperature) -> Completion:
        payload: dict[str, Any] = {
            "model": self.provider["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or DEFAULT_MAX_OUTPUT,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": tool} for tool in tools
            ]
            payload["tool_choice"] = "auto"
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        raw, latency = self._post(_split_base(self.provider, "/chat/completions"), payload)
        try:
            choice = (raw.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            calls = []
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append(
                    {
                        "id": call.get("id") or new_id("call"),
                        "name": fn.get("name", ""),
                        "arguments": args if isinstance(args, dict) else {},
                    }
                )
            usage = self._normalise_usage("openai", raw.get("usage") or {})
            return self._finish(text, calls, usage, latency, str(choice.get("finish_reason", "")), started)
        except (IndexError, TypeError) as exc:
            raise ConsumerError(
                "PROVIDER_UNAVAILABLE", "模型响应结构不符合预期。", "检查该服务是否兼容 OpenAI API。"
            ) from exc

    def _anthropic_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts = []
        converted = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(str(content))
                continue
            if role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("tool_call_id", ""),
                                "content": str(content),
                            }
                        ],
                    }
                )
                continue
            if role == "assistant" and message.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for call in message["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "input": call.get("arguments") or {},
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
                continue
            converted.append({"role": role if role in ("user", "assistant") else "user", "content": content})
        return "\n\n".join(system_parts), converted

    def _complete_anthropic(self, messages, tools, max_tokens, temperature) -> Completion:
        system, converted = self._anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.provider["model"],
            "messages": converted,
            "max_tokens": max_tokens or min(4096, int(self.provider.get("max_output", 8192))),
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
                }
                for tool in tools
            ]
        started = time.monotonic()
        raw, latency = self._post(_split_base(self.provider, "/messages"), payload)
        text_parts = []
        calls = []
        for block in raw.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": block.get("id") or new_id("call"),
                        "name": block.get("name", ""),
                        "arguments": block.get("input") or {},
                    }
                )
        usage = self._normalise_usage("anthropic", raw.get("usage") or {})
        return self._finish(
            "".join(text_parts), calls, usage, latency, str(raw.get("stop_reason", "")), started
        )

    def _gemini_contents(self, messages: list[dict]) -> tuple[dict, list[dict]]:
        system = ""
        contents = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system += ("\n\n" if system else "") + str(message.get("content", ""))
                continue
            if role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": message.get("tool_name", ""),
                                    "response": {"content": str(message.get("content", ""))},
                                }
                            }
                        ],
                    }
                )
                continue
            parts = []
            content = message.get("content", "")
            if content:
                parts.append({"text": str(content)})
            for call in message.get("tool_calls") or []:
                parts.append(
                    {
                        "functionCall": {
                            "name": call.get("name"),
                            "args": call.get("arguments") or {},
                        }
                    }
                )
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        config: dict[str, Any] = {}
        if system:
            config["systemInstruction"] = {"parts": [{"text": system}]}
        return config, contents

    def _complete_gemini(self, messages, tools, response_format, max_tokens, temperature) -> Completion:
        config, contents = self._gemini_contents(messages)
        generation: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens or DEFAULT_MAX_OUTPUT,
        }
        if response_format == "json":
            generation["responseMimeType"] = "application/json"
        config["generationConfig"] = generation
        if tools:
            declarations = []
            for tool in tools:
                schema = dict(tool.get("parameters") or {"type": "object", "properties": {}})
                schema.pop("additionalProperties", None)
                declarations.append(
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": schema,
                    }
                )
            config["tools"] = [{"functionDeclarations": declarations}]
            config["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        url = _split_base(self.provider, f"/models/{self.provider['model']}:generateContent")
        payload = {"contents": contents, **config}
        started = time.monotonic()
        raw, latency = self._post(url, payload)
        text_parts = []
        calls = []
        candidates = raw.get("candidates") or []
        finish = ""
        if candidates:
            finish = str(candidates[0].get("finishReason", ""))
            for part in (candidates[0].get("content") or {}).get("parts") or []:
                if "text" in part:
                    text_parts.append(str(part.get("text", "")))
                if "functionCall" in part:
                    fn = part["functionCall"] or {}
                    calls.append(
                        {
                            "id": new_id("call"),
                            "name": fn.get("name", ""),
                            "arguments": fn.get("args") or {},
                        }
                    )
        usage = self._normalise_usage("gemini", raw)
        return self._finish("".join(text_parts), calls, usage, latency, finish, started)

    # --- connection test ------------------------------------------------------
    def test(self, *, temporary_key: str | None = None) -> dict:
        key = temporary_key if temporary_key is not None else self.key
        if temporary_key is not None:
            self.key = temporary_key
        steps: list[dict] = []
        started = time.monotonic()
        tokens_cost = 0.0
        try:
            reachable = True
            models: list[str] = []
            list_error = ""
            try:
                if self.provider["protocol"] == "openai-compatible":
                    raw, _latency = self._get(_split_base(self.provider, "/models"))
                    models = [
                        str(item.get("id"))
                        for item in (raw.get("data") or [])
                        if isinstance(item, dict) and item.get("id")
                    ]
                elif self.provider["protocol"] == "anthropic":
                    raw, _latency = self._get(_split_base(self.provider, "/models"))
                    models = [
                        str(item.get("id"))
                        for item in (raw.get("data") or [])
                        if isinstance(item, dict) and item.get("id")
                    ]
                else:
                    raw, _latency = self._get(_split_base(self.provider, "/models"))
                    models = [
                        str(item.get("name", "")).split("/")[-1]
                        for item in (raw.get("models") or [])
                        if isinstance(item, dict)
                    ]
            except ConsumerError as exc:
                if exc.code in ("PROVIDER_AUTH_FAILED", "PROVIDER_RATE_LIMITED"):
                    raise
                # Some compatible gateways have no /models endpoint; a chat probe still decides.
                list_error = exc.message
                reachable = exc.code != "PROVIDER_UNAVAILABLE"
            steps.append(
                {
                    "name": "endpoint",
                    "ok": reachable,
                    "detail": "已连接" if reachable else (list_error or "无法连接"),
                }
            )
            steps.append(
                {
                    "name": "auth",
                    "ok": True,
                    "detail": "鉴权通过" if not list_error else "列表接口不可用，将通过对话验证",
                }
            )
            model_ok = (not models) or self.provider["model"] in models
            steps.append(
                {
                    "name": "model",
                    "ok": model_ok,
                    "detail": "模型存在" if model_ok else f"列表中没有 {self.provider['model']}",
                }
            )
            if not model_ok:
                raise ConsumerError(
                    "PROVIDER_MODEL_MISSING",
                    f"服务商没有返回模型 {self.provider['model']}。",
                    "检查模型 ID，或直接保存后测试对话。",
                )
            completion = self.complete(
                [
                    {"role": "system", "content": "You are a connectivity probe. Reply with OK."},
                    {"role": "user", "content": "Reply with OK."},
                ],
                max_tokens=16,
                temperature=0.0,
            )
            tokens_cost += completion.cost_usd or 0.0
            steps.append(
                {
                    "name": "chat",
                    "ok": True,
                    "detail": f"模型已回复（{completion.latency_ms}ms）",
                }
            )
            tool_ok = None
            if self.provider.get("supports_tools"):
                try:
                    probe_completion = self.complete(
                        [{"role": "user", "content": "Call the ping tool with value 1."}],
                        tools=[
                            {
                                "name": "ping",
                                "description": "Return a ping value.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"value": {"type": "integer"}},
                                    "required": ["value"],
                                },
                            }
                        ],
                        max_tokens=64,
                    )
                    tokens_cost += probe_completion.cost_usd or 0.0
                    tool_ok = bool(probe_completion.tool_calls)
                    steps.append(
                        {
                            "name": "tools",
                            "ok": tool_ok,
                            "detail": "支持工具调用" if tool_ok else "未返回工具调用；可改用补丁模式",
                        }
                    )
                except ConsumerError as exc:
                    steps.append({"name": "tools", "ok": False, "detail": exc.message})
            estimated = compute_cost(
                self.provider,
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            )[0]
            return {
                "ok": all(step["ok"] for step in steps if step["name"] in ("endpoint", "auth", "model", "chat")),
                "steps": steps,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model": self.provider["model"],
                "provider": self.provider.get("label", ""),
                "estimated_cost_usd": round(tokens_cost, 8),
                "tool_mode_possible": tool_ok,
                "price_known": price_known(self.provider),
                "price_estimate_note": estimated,
            }
        finally:
            self.key = key


class ProvidersManager:
    def __init__(self, store: ConsumerStore):
        self.store = store
        self.keyvault = KeyVault(store.home)

    def _load(self) -> dict:
        raw = self.store.read_json(self.store.providers_path, default={"version": 1, "providers": []})
        if not isinstance(raw, dict):
            raw = {"version": 1, "providers": []}
        raw.setdefault("providers", [])
        return raw

    def _save(self, payload: dict) -> None:
        with self.store.lock("providers"):
            self.store.write_json(self.store.providers_path, payload)

    def public_providers(self, *, include_ref: bool = False) -> list[dict]:
        out = []
        for provider in self._load().get("providers", []):
            item = {k: v for k, v in provider.items() if k != "key_ref"}
            item["has_key"] = bool(provider.get("key_ref"))
            item["key_hint"] = provider.get("key_hint", "")
            if include_ref:
                item["key_ref"] = provider.get("key_ref", "")
            out.append(item)
        return out

    def get(self, provider_id: str) -> dict:
        for provider in self._load().get("providers", []):
            if provider.get("id") == provider_id:
                return provider
        raise ConsumerError("PROVIDER_NOT_CONFIGURED", "这个服务商不存在。", "在模型页重新添加。")

    def get_with_key(self, provider_id: str) -> tuple[dict, str]:
        provider = self.get(provider_id)
        key = self.keyvault.get(provider.get("key_ref"))
        if not key and not provider.get("local"):
            raise ConsumerError(
                "PROVIDER_AUTH_FAILED",
                "这个服务商还没有可用的 Key。",
                "在模型页重新保存 Key。",
            )
        return provider, key or ""

    def add(self, payload: dict, api_key: str | None = None) -> dict:
        clean = validate_provider(payload)
        providers = self._load()
        if any(p.get("id") == clean["id"] for p in providers["providers"]):
            raise ConsumerError("CONFLICT", "服务商 ID 已存在。", "重试或使用新的 ID。")
        if api_key:
            ref, hint = self.keyvault.set(clean["id"], api_key)
            clean["key_ref"] = ref
            clean["key_hint"] = hint
        providers["providers"].append(clean)
        self._save(providers)
        self.store.append_log("provider_added", provider_id=clean["id"], protocol=clean["protocol"])
        return {k: v for k, v in clean.items() if k != "key_ref"}

    def update(self, provider_id: str, payload: dict, api_key: str | None = None) -> dict:
        existing = self.get(provider_id)
        merged = {**existing, **payload, "id": provider_id}
        clean = validate_provider(merged, provider_id=provider_id)
        if api_key:
            ref, hint = self.keyvault.set(provider_id, api_key)
            clean["key_ref"] = ref
            clean["key_hint"] = hint
            old_ref = existing.get("key_ref")
            if old_ref and old_ref != ref:
                self.keyvault.delete(old_ref)
        else:
            clean["key_ref"] = existing.get("key_ref", "")
            clean["key_hint"] = existing.get("key_hint", "")
        providers = self._load()
        providers["providers"] = [
            clean if p.get("id") == provider_id else p for p in providers["providers"]
        ]
        self._save(providers)
        return {k: v for k, v in clean.items() if k != "key_ref"}

    def delete(self, provider_id: str) -> None:
        providers = self._load()
        target = None
        kept = []
        for provider in providers["providers"]:
            if provider.get("id") == provider_id:
                target = provider
            else:
                kept.append(provider)
        if target is None:
            raise ConsumerError("PROVIDER_NOT_CONFIGURED", "这个服务商不存在。", "刷新模型页。")
        self.keyvault.delete(target.get("key_ref"))
        providers["providers"] = kept
        self._save(providers)
        self.store.append_log("provider_deleted", provider_id=provider_id)

    def test(self, provider_id: str | None, payload: dict | None, temporary_key: str | None = None) -> dict:
        if provider_id:
            provider = self.get(provider_id)
            if temporary_key is not None:
                key = temporary_key
            else:
                key = self.keyvault.get(provider.get("key_ref")) or ""
                if not key and not provider.get("local"):
                    raise ConsumerError(
                        "PROVIDER_AUTH_FAILED",
                        "这个服务商还没有可用的 Key。",
                        "输入临时 Key 测试，或先在模型页保存 Key。",
                    )
            spec = provider
        else:
            if not payload:
                raise ConsumerError("INVALID_REQUEST", "缺少服务商配置。", "填写 Base URL 和模型。")
            spec = validate_provider({**payload, "data_policy_ack": True})
            key = temporary_key or str(payload.get("api_key") or "")
            if not spec.get("local") and not key:
                raise ConsumerError("PROVIDER_AUTH_FAILED", "测试连接需要 Key。", "填写 Key 后重试。")
        result = ProviderClient(spec, key).test(temporary_key=None)
        self.store.append_log(
            "provider_tested", provider_id=spec.get("id"), ok=result.get("ok"), temporary=bool(temporary_key)
        )
        return result

    # --- role resolution ------------------------------------------------------
    def _spec_for_ref(self, ref: str) -> tuple[dict, str]:
        parsed = parse_role(ref)
        if not parsed:
            raise ConsumerError(
                "PROVIDER_NOT_CONFIGURED",
                "模型引用格式无效。",
                "在模型页重新选择递归模型。",
            )
        provider_id, model = parsed
        provider = self.get(provider_id)
        key = self.keyvault.get(provider.get("key_ref"))
        if not key and not provider.get("local"):
            raise ConsumerError(
                "PROVIDER_AUTH_FAILED", "所选模型还没有有效 Key。", "在模型页更新 Key。"
            )
        spec = {**provider, "model": model, "id": provider_id}
        return spec, key or ""

    def clients_for_role(self, settings: dict, role: str) -> list[ProviderClient]:
        models = settings.get("models", {})
        ref = models.get(role) or models.get("proposer")
        if not ref:
            raise ConsumerError(
                "PROVIDER_NOT_CONFIGURED",
                "还没有接入递归模型。",
                "在模型页选择服务商与模型，或使用本地 Ollama。",
            )
        refs = [ref] + [item for item in models.get("fallbacks", []) if item and item != ref][:3]
        clients = []
        local_only = bool(settings.get("privacy", {}).get("local_only"))
        for item in refs:
            spec, key = self._spec_for_ref(item)
            if local_only and not spec.get("local"):
                continue
            clients.append(ProviderClient(spec, key))
        if not clients:
            raise ConsumerError(
                "PROVIDER_NOT_CONFIGURED",
                "仅本地模型模式下没有可用的本地模型。",
                "接入 Ollama/LM Studio，或关闭“仅本地模型”。",
            )
        return clients

    @staticmethod
    def complete_with_fallback(
        clients: list[ProviderClient],
        messages: list[dict],
        **kwargs,
    ) -> Completion:
        last: ConsumerError | None = None
        for index, client in enumerate(clients):
            try:
                completion = client.complete(messages, **kwargs)
                completion.fallback_used = index > 0
                return completion
            except ConsumerError as exc:
                if exc.code not in (
                    "PROVIDER_AUTH_FAILED",
                    "PROVIDER_RATE_LIMITED",
                    "PROVIDER_UNAVAILABLE",
                ):
                    raise
                last = exc
                continue
        if last is not None:
            raise last
        raise ConsumerError("PROVIDER_NOT_CONFIGURED", "没有可用模型。", "在模型页配置模型。")
