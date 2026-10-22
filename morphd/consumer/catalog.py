"""Static provider/model catalog.

Prices are conservative reference values and are always marked as estimates. Cloud
providers default to ``confirmed: false``; the UI must ask the user to confirm or edit.
"""

from __future__ import annotations

from copy import deepcopy

PROVIDER_CATALOG = {
    "openai": {
        "label": "OpenAI",
        "protocol": "openai-compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "context_window": 1_000_000,
        "max_output": 32_768,
        "supports_tools": True,
        "supports_json": True,
        "price": {
            "input_per_million": 0.40,
            "cached_input_per_million": 0.10,
            "cache_write_per_million": 0.40,
            "output_per_million": 1.60,
            "reasoning_per_million": 0.0,
            "per_call_usd": 0.0,
            "confirmed": False,
        },
        "data_policy": "Requests go directly to OpenAI under your account and its data policies.",
        "help_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Anthropic",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5",
        "context_window": 200_000,
        "max_output": 64_000,
        "supports_tools": True,
        "supports_json": True,
        "price": {
            "input_per_million": 3.00,
            "cached_input_per_million": 0.30,
            "cache_write_per_million": 3.75,
            "output_per_million": 15.00,
            "reasoning_per_million": 0.0,
            "per_call_usd": 0.0,
            "confirmed": False,
        },
        "data_policy": "Requests go directly to Anthropic under your account and its data policies.",
        "help_url": "https://console.anthropic.com/settings/keys",
    },
    "gemini": {
        "label": "Google Gemini",
        "protocol": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.5-flash",
        "context_window": 1_000_000,
        "max_output": 65_536,
        "supports_tools": True,
        "supports_json": True,
        "price": {
            "input_per_million": 0.30,
            "cached_input_per_million": 0.075,
            "cache_write_per_million": 0.30,
            "output_per_million": 2.50,
            "reasoning_per_million": 2.50,
            "per_call_usd": 0.0,
            "confirmed": False,
        },
        "data_policy": "Requests go directly to Google under your account and its data policies.",
        "help_url": "https://aistudio.google.com/app/apikey",
    },
    "deepseek": {
        "label": "DeepSeek",
        "protocol": "openai-compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "context_window": 128_000,
        "max_output": 8_192,
        "supports_tools": True,
        "supports_json": True,
        "price": {
            "input_per_million": 0.28,
            "cached_input_per_million": 0.028,
            "cache_write_per_million": 0.28,
            "output_per_million": 0.42,
            "reasoning_per_million": 0.42,
            "per_call_usd": 0.0,
            "confirmed": False,
        },
        "data_policy": "Requests go directly to DeepSeek under your account and its data policies.",
        "help_url": "https://platform.deepseek.com/api_keys",
    },
    "openrouter": {
        "label": "OpenRouter",
        "protocol": "openai-compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "context_window": 200_000,
        "max_output": 32_768,
        "supports_tools": True,
        "supports_json": True,
        "price": {
            "input_per_million": 0.0,
            "cached_input_per_million": 0.0,
            "cache_write_per_million": 0.0,
            "output_per_million": 0.0,
            "reasoning_per_million": 0.0,
            "per_call_usd": 0.0,
            "confirmed": False,
        },
        "data_policy": "Requests go to OpenRouter and the upstream model provider you select.",
        "help_url": "https://openrouter.ai/keys",
    },
    "ollama": {
        "label": "Ollama / local",
        "protocol": "openai-compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.2",
        "context_window": 32_768,
        "max_output": 8_192,
        "supports_tools": False,
        "supports_json": True,
        "price": {
            "input_per_million": 0.0,
            "cached_input_per_million": 0.0,
            "cache_write_per_million": 0.0,
            "output_per_million": 0.0,
            "reasoning_per_million": 0.0,
            "per_call_usd": 0.0,
            "confirmed": True,
        },
        "data_policy": "Runs on this computer; harness2-morphd never sends these requests to the cloud.",
        "help_url": "https://ollama.com",
        "local": True,
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "protocol": "openai-compatible",
        "base_url": "",
        "model": "",
        "context_window": 128_000,
        "max_output": 8_192,
        "supports_tools": True,
        "supports_json": True,
        "price": {
            "input_per_million": 0.0,
            "cached_input_per_million": 0.0,
            "cache_write_per_million": 0.0,
            "output_per_million": 0.0,
            "reasoning_per_million": 0.0,
            "per_call_usd": 0.0,
            "confirmed": False,
        },
        "data_policy": "Requests go directly to the endpoint you configure.",
        "help_url": "",
    },
}

MODEL_PRESETS = {
    "frugal": {
        "label": "省钱",
        "analysis_depth": "light",
        "provider": "deepseek",
        "rationale": "低价模型 + 轻量分析；没有信号时不调用模型。",
    },
    "balanced": {
        "label": "均衡",
        "analysis_depth": "adaptive",
        "provider": "openai",
        "rationale": "日常默认；只在会话出现失败/纠正时分析。",
    },
    "quality": {
        "label": "效果优先",
        "analysis_depth": "full",
        "provider": "anthropic",
        "rationale": "更强模型 + 完整递归；成本更高。",
    },
    "local": {
        "label": "本地",
        "analysis_depth": "adaptive",
        "provider": "ollama",
        "rationale": "只使用本机模型，不向云端发送内容。",
    },
}


def template(provider: str) -> dict:
    return deepcopy(PROVIDER_CATALOG.get(provider, PROVIDER_CATALOG["custom"]))


def provider_help() -> list[dict]:
    return [
        {
            "id": key,
            "label": value["label"],
            "protocol": value["protocol"],
            "base_url": value["base_url"],
            "default_model": value["model"],
            "model": value["model"],
            "context_window": value["context_window"],
            "max_output": value["max_output"],
            "supports_tools": value["supports_tools"],
            "supports_json": value["supports_json"],
            "price": value["price"],
            "help_url": value.get("help_url", ""),
            "data_policy": value.get("data_policy", ""),
            "local": bool(value.get("local")),
        }
        for key, value in PROVIDER_CATALOG.items()
    ]
