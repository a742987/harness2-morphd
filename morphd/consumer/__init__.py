"""Consumer product layer for morphd v0.5.

The package keeps the engine (tasks, replay, security, evaluation) independent from the
host-facing product surface: settings, BYOK providers, host discovery/adaptation, session
ingestion, the in-host job runner, local API and the local web UI.
"""

from __future__ import annotations

APP_VERSION = "0.5.0"
API_VERSION = 1
TASK_CONTRACT_VERSION = 1
SURFACE_VERSION = 1

__all__ = ["APP_VERSION", "API_VERSION", "SURFACE_VERSION", "TASK_CONTRACT_VERSION"]
