"""One BenchSpec shape for all three runtimes, with runtime-specific surfaces."""

from __future__ import annotations

from pathlib import Path

from .. import _upstream
from ..config import Settings


def _spec():
    return _upstream.core("spec")


LEAK_GLOBS = (
    "RUBRICS.json",
    "rubrics/**",
    "expected/**",
    "reference/**",
    "solution/**",
    "task.json",
    "criteria.json",
    "criteria/**",
)


def make_prompts(domain_word: str = "domain") -> object:
    return _spec().BenchPrompts(
        work=(
            "real software work in this domain: implement the requested change, keep the "
            "workspace coherent, and leave the requested artifacts in place."
        ),
        domain_word=domain_word,
        inv_levers=(
            "Workspace instructions that make expectations explicit; tool guidance that names "
            "the right tool at the right moment; reusable skills with clear triggers; small, "
            "deterministic scripts for repeated steps."
        ),
        dom_levers=(
            "Domain-specific conventions, validation habits, and failure recovery guidance that "
            "a practitioner in this domain would expect. Keep it actionable and specific."
        ),
        yardstick=(
            "The work is done when the requested artifacts exist, the run terminates normally, "
            "and the agent does not need user corrections or retries to get there."
        ),
        deliverable_word="deliverable",
        domain_guidance=(
            "Prefer edits that reduce avoidable tool failures, repeated commands, and user "
            "corrections while preserving the ability to actually produce the artifacts."
        ),
        selection_checks=(
            "Reject a candidate that only looks faster because it stopped doing the work. "
            "Require the artifacts to be present and the path to termination to be clean."
        ),
        selection_lead="Choose the harness whose work is delivered with fewer corrections.",
        selection_conformance="full",
        prompt_pack="adapt",
    )


def make_spec(
    *,
    runtime: str,
    substrate: str,
    settings: Settings,
    install_mode: str,
    allow_subagents: bool,
    proposer_channel: str = "claude_cli",
    proposer_model: str = "",
    selector_model: str = "",
    name: str = "md",
):
    channels = {
        "proposer": _spec().ChannelSpec(proposer_channel, proposer_model),
        "selector": _spec().ChannelSpec(proposer_channel, selector_model or proposer_model),
    }
    return _spec().BenchSpec(
        name=name,
        runs_root=Path(settings.runtime_runs_root(runtime)),
        install_mode=install_mode,
        prompts=make_prompts(),
        channels=channels,
        domain_word="domain",
        leak_globs=LEAK_GLOBS,
        allow_subagents=allow_subagents,
        reserved_scripts=frozenset(),
        proposer_deliverables="contents",
        state_deliverable=False,
        obs_trunc=settings.obs_trunc,
    )


__all__ = ["LEAK_GLOBS", "make_prompts", "make_spec"]
