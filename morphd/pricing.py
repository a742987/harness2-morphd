"""Versioned price table and usage normalisation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import RUNTIMES
from .store import sha256_file


class PricingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rates:
    input_per_million: float
    cached_input_per_million: float
    cache_write_per_million: float
    output_per_million: float
    reasoning_per_million: float
    per_call_usd: float = 0.0


@dataclass(frozen=True)
class RuntimePrice:
    runtime: str
    rates: Rates
    estimate_input_tokens: int
    estimate_output_tokens: int


@dataclass(frozen=True)
class ProposerPrice:
    channel: str
    rates: Rates
    estimate_input_tokens: int
    estimate_output_tokens: int


@dataclass(frozen=True)
class Pricing:
    version: int
    confirmed: bool
    currency: str
    updated: str
    runtimes: dict[str, RuntimePrice]
    proposer: dict[str, ProposerPrice]
    unknown_usage_usd: float
    path: Path
    digest: str

    @classmethod
    def load(cls, path: Path) -> "Pricing":
        path = Path(path)
        if not path.is_file():
            raise PricingError(
                f"price table not found at {path}; harness2-morphd refuses to start without one"
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PricingError(f"cannot read price table {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise PricingError(f"price table {path} must be a mapping")
        version = raw.get("version")
        if version != 1:
            raise PricingError(f"unsupported price-table version {version!r}")
        confirmed = bool(raw.get("confirmed", False))
        runtimes: dict[str, RuntimePrice] = {}
        for runtime in RUNTIMES:
            src = (raw.get("runtimes") or {}).get(runtime)
            if not isinstance(src, dict):
                raise PricingError(f"price table is missing runtime {runtime!r}")
            rates = _rates(src, f"runtimes.{runtime}")
            est = src.get("estimate") or {}
            runtimes[runtime] = RuntimePrice(
                runtime=runtime,
                rates=rates,
                estimate_input_tokens=_nonneg_int(est.get("input_tokens"), 12000),
                estimate_output_tokens=_nonneg_int(est.get("output_tokens"), 1500),
            )
        proposer_raw = raw.get("proposer") or {}
        est = proposer_raw.get("estimate") or {}
        prop_in = _nonneg_int(est.get("input_tokens"), 20000)
        prop_out = _nonneg_int(est.get("output_tokens"), 4000)
        proposer: dict[str, ProposerPrice] = {}
        for channel, src in (proposer_raw.get("channels") or {}).items():
            if isinstance(src, dict):
                proposer[str(channel)] = ProposerPrice(
                    channel=str(channel),
                    rates=_rates(src, f"proposer.channels.{channel}"),
                    estimate_input_tokens=prop_in,
                    estimate_output_tokens=prop_out,
                )
        unknown = float(raw.get("unknown_usage_usd", 0.25))
        return cls(
            version=1,
            confirmed=confirmed,
            currency=str(raw.get("currency", "USD")),
            updated=str(raw.get("updated", "")),
            runtimes=runtimes,
            proposer=proposer,
            unknown_usage_usd=unknown,
            path=path,
            digest=sha256_file(path),
        )

    def require_confirmed(self) -> None:
        if not self.confirmed:
            raise PricingError(
                f"price table {self.path} is marked confirmed: false. P3 must review the rates "
                "and set `confirmed: true` before any real run; --dry-run remains available."
            )

    def estimate_call_usd(self, runtime: str) -> float:
        price = self.runtimes[runtime]
        rates = price.rates
        cost = (
            price.estimate_input_tokens * rates.input_per_million
            + price.estimate_output_tokens * rates.output_per_million
        ) / 1_000_000
        return round(cost + rates.per_call_usd, 6)

    def estimate_session_usd(self, channel: str) -> float:
        price = self.proposer.get(channel)
        if price is None:
            return round(self.unknown_usage_usd * 5, 6)
        rates = price.rates
        cost = (
            price.estimate_input_tokens * rates.input_per_million
            + price.estimate_output_tokens * rates.output_per_million
        ) / 1_000_000
        return round(cost + rates.per_call_usd, 6)

    def usage_cost(self, runtime: str, usage: dict | None) -> float | None:
        norm = normalize_usage(runtime, usage)
        if norm.get("total_cost_usd") is not None:
            return round(float(norm["total_cost_usd"]), 6)
        if not norm["present"]:
            return None
        rates = self.runtimes[runtime].rates
        if runtime == "dsh":
            uncached = norm["input_tokens"]
        else:
            uncached = max(0, norm["input_tokens"] - norm["cached_input_tokens"])
        cost = (
            uncached * rates.input_per_million
            + norm["cached_input_tokens"] * rates.cached_input_per_million
            + norm["cache_write_tokens"] * rates.cache_write_per_million
            + norm["output_tokens"] * rates.output_per_million
            + norm["reasoning_tokens"] * rates.reasoning_per_million
            + rates.per_call_usd * 1_000_000
        ) / 1_000_000
        return round(cost, 6)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "confirmed": self.confirmed,
            "currency": self.currency,
            "updated": self.updated,
            "path": str(self.path),
            "sha256": self.digest,
        }


def _rates(src: dict, where: str) -> Rates:
    def val(name: str, default: float = 0.0) -> float:
        raw = src.get(name, default)
        try:
            out = float(raw)
        except (TypeError, ValueError) as exc:
            raise PricingError(f"{where}.{name} must be a number, got {raw!r}") from exc
        if out < 0:
            raise PricingError(f"{where}.{name} must be non-negative")
        return out

    return Rates(
        input_per_million=val("input_per_million"),
        cached_input_per_million=val("cached_input_per_million"),
        cache_write_per_million=val("cache_write_per_million"),
        output_per_million=val("output_per_million"),
        reasoning_per_million=val("reasoning_per_million"),
        per_call_usd=val("per_call_usd"),
    )


def _nonneg_int(value, default: int) -> int:
    try:
        out = int(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return max(0, out)


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_usage(runtime: str, usage: dict | None) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    if runtime == "dsh":
        inp = _int(usage.get("inputTokens", usage.get("input_tokens")))
        cached = _int(usage.get("cacheReadTokens", usage.get("cached_input_tokens")))
        cache_write = _int(usage.get("cacheWriteTokens", usage.get("cache_write_tokens")))
        out = _int(usage.get("outputTokens", usage.get("output_tokens")))
        reasoning = _int(usage.get("reasoningTokens", usage.get("reasoning_output_tokens")))
    else:
        inp = _int(usage.get("input_tokens"))
        cached = _int(usage.get("cached_input_tokens", usage.get("cache_read_input_tokens")))
        cache_write = _int(
            usage.get("cache_write_tokens", usage.get("cache_creation_input_tokens"))
        )
        out = _int(usage.get("output_tokens"))
        reasoning = _int(usage.get("reasoning_output_tokens"))
    total_cost = usage.get("total_cost_usd")
    try:
        total_cost = float(total_cost) if total_cost is not None else None
    except (TypeError, ValueError):
        total_cost = None
    present = any((inp, cached, cache_write, out, reasoning, total_cost is not None))
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "cache_write_tokens": cache_write,
        "output_tokens": out,
        "reasoning_tokens": reasoning,
        "total_cost_usd": total_cost,
        "present": present,
    }


def merge_usage(usages: list[dict | None]) -> dict:
    out = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_cost_usd": None,
    }
    any_present = False
    total_cost = 0.0
    saw_cost = False
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            out[key] += _int(usage.get(key))
        if usage.get("total_cost_usd") is not None:
            saw_cost = True
            try:
                total_cost += float(usage["total_cost_usd"])
            except (TypeError, ValueError):
                pass
        if any(_int(usage.get(k)) for k in out if k != "total_cost_usd"):
            any_present = True
    if saw_cost:
        out["total_cost_usd"] = round(total_cost, 6)
    out["present"] = any_present or saw_cost
    return out


__all__ = [
    "Pricing",
    "PricingError",
    "normalize_usage",
    "merge_usage",
]
