"""Bridge the consumer BYOK proposer into the upstream ``run_sweep`` protocol.

The strict/enterprise engine path uses upstream Propose–Probe–Compose. This adapter keeps
that protocol while letting the model channel be any user-configured OpenAI-compatible,
Anthropic, Gemini, or local endpoint. It never exposes criteria or hidden terms: the
upstream recursion layer only passes the editable context/harness subset.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import _upstream, budget, pricing
from .consumer.proposer import BYOKProposer
from .consumer.providers import ProviderClient, ProvidersManager, estimate_analysis_usd
from .consumer.service import ConsumerService
from .consumer.settings import SettingsManager


def _consumer_home() -> Path:
    return Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd")).expanduser()


class BYOKUpstreamProposer:
    """Drop-in proposer for ``harness2.core.schedule.run_sweep`` backed by BYOK."""

    def __init__(
        self,
        *,
        spec,
        adapter,
        guard: budget.BudgetGuard,
        price: pricing.Pricing,
        audit_dir: Path,
        consumer_home: Path | None = None,
        max_rounds: int = 8,
    ) -> None:
        proposer_mod = _upstream.core("proposer")
        self._reply = proposer_mod.Reply
        self._error = proposer_mod.ProposerError
        self.spec = spec
        self.adapter = adapter
        self.guard = guard
        self.price = price
        self.audit_dir = Path(audit_dir)
        self.attempts = 2
        self.project = ""
        self.region = ""
        home = Path(consumer_home or _consumer_home())
        self.consumer = ConsumerService(home)
        settings = SettingsManager(self.consumer.store).load()
        self.clients: list[ProviderClient] = self.consumer.providers.clients_for_role(
            settings, "proposer"
        )
        self._label = threading.local()
        self._inner = BYOKProposer(
            store=self.consumer.store,
            managers=self.consumer.providers,
            clients=self.clients,
            work_root=self.audit_dir / "byok",
            cache_root=self.consumer.store.home / "runtime" / "cache" / "proposals",
            charge=self._charge,
            precall=self._precall,
            max_rounds=max_rounds,
        )

    # --- guard ----------------------------------------------------------------
    def _label_value(self) -> str:
        return str(getattr(self._label, "value", "session"))

    def _precall(self, phase: str) -> None:
        self.guard.reserve(kind="session", label=f"{self._label_value()}.{phase}")

    def _charge(self, completion, phase: str) -> None:
        usage = completion.usage or {}
        cost = completion.cost_usd
        estimated = bool(completion.cost_estimated)
        if cost is None or not usage.get("present"):
            # Never treat missing usage as free. Prefer the provider's one-call estimate,
            # then the documented unknown-usage fallback.
            try:
                estimate = estimate_analysis_usd(
                    self.clients[0].provider,
                    context_chars=8_000,
                    calls=1,
                )
            except Exception:
                estimate = None
            cost = estimate if estimate is not None else 0.25
            estimated = True
        self.guard.charge(
            cost,
            kind="session",
            label=f"{self._label_value()}.{phase}",
            estimated=estimated,
        )

    # --- protocol -------------------------------------------------------------
    def call(
        self,
        mission: str,
        context_dir: Path | str | None,
        label: str,
        *,
        system: str = "",
        timeout: int | None = None,
        max_attempts: int | None = None,
        harvest: str | None = None,
    ):
        self._label.value = label
        context = Path(context_dir) if context_dir is not None else self.audit_dir
        try:
            if harvest:
                seed = Path(context) / harvest
                if not seed.is_dir():
                    seed = Path(context)
                result = self._inner.call(
                    mission,
                    seed,
                    label,
                    system=system,
                    timeout=float(timeout or 1800),
                )
                if not result.get("ok"):
                    raise self._error(
                        f"[{label}] {result.get('error') or 'no changes'}",
                        attempts=int(result.get("attempts") or 1),
                        cost_usd=float(result.get("cost_usd") or 0.0),
                    )
                return self._reply(
                    label=label,
                    text=str(result.get("summary") or result.get("text") or ""),
                    ok=True,
                    cost_usd=float(result.get("cost_usd") or 0.0),
                    attempts=int(result.get("attempts") or 1),
                    cached=bool(result.get("cached")),
                    channel="byok",
                    workspace=Path(result["workspace"]),
                    audit=self.audit_dir,
                )
            return self._selection(
                mission, context, label, system=system, timeout=timeout, max_attempts=max_attempts
            )
        except self._error:
            raise
        except Exception as exc:
            raise self._error(
                f"[{label}] byok: {type(exc).__name__}: {exc}",
                attempts=1,
                cost_usd=0.0,
            ) from exc

    def _selection(
        self, mission: str, context: Path, label: str, *, system: str, timeout, max_attempts=None
    ):
        """Selection/experience calls expect text, not an edited harness tree."""
        text_parts: list[str] = []
        if context.is_dir():
            for path in sorted(context.rglob("*"))[:400]:
                if path.is_file() and path.stat().st_size <= 200_000:
                    try:
                        body = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    text_parts.append(f"\n===== {path.relative_to(context).as_posix()} =====\n{body}")
                    if sum(len(part) for part in text_parts) > 180_000:
                        break
        messages = [
            {
                "role": "system",
                "content": system
                or "You select the best candidate. Follow the required output block exactly.",
            },
            {"role": "user", "content": f"{mission}\n\n{''.join(text_parts)}"},
        ]
        # C.3.9: honor max_attempts — retry an empty selection, reserving and charging budget
        # once per real model call so a retry is never billed as a single call.
        attempts_allowed = max(1, int(max_attempts or 1))
        total_cost = 0.0
        for attempt in range(1, attempts_allowed + 1):
            self._precall("select")
            completion = ProvidersManager.complete_with_fallback(
                self.clients,
                messages,
                max_tokens=min(4096, int(self.clients[0].provider.get("max_output", 4096))),
                temperature=0.0,
            )
            self._charge(completion, "select")
            total_cost += float(completion.cost_usd or 0.0)
            if (completion.text or "").strip():
                return self._reply(
                    label=label,
                    text=completion.text,
                    ok=True,
                    cost_usd=round(total_cost, 6),
                    attempts=attempt,
                    cached=False,
                    channel="byok",
                    audit=self.audit_dir,
                )
        raise self._error(
            f"[{label}] byok selection returned no text after {attempts_allowed} attempt(s)",
            attempts=attempts_allowed,
            cost_usd=round(total_cost, 6),
        )
