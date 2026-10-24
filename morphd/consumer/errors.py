"""Stable error codes returned by the local API and the consumer services."""

from __future__ import annotations

from dataclasses import dataclass, field

ERROR_STATUS = {
    "AUTH_REQUIRED": 401,
    "CSRF_INVALID": 403,
    "FORBIDDEN": 403,
    "PROVIDER_AUTH_FAILED": 422,
    "PROVIDER_RATE_LIMITED": 429,
    "PROVIDER_UNAVAILABLE": 502,
    "PROVIDER_NOT_CONFIGURED": 422,
    "PROVIDER_MODEL_MISSING": 422,
    "PROVIDER_CONTENT_REFUSED": 422,
    "PROVIDER_INVALID_REQUEST": 422,
    "BUDGET_EXCEEDED": 422,
    "BUDGET_UNKNOWN_PRICE": 422,
    "HOST_NOT_FOUND": 404,
    "HOST_UNSUPPORTED": 422,
    "HOST_RUNNING": 409,
    "ADAPT_FAILED": 422,
    "APPLY_CONFLICT": 409,
    "CANDIDATE_STALE": 409,
    "CANDIDATE_UNSAFE": 422,
    "CANDIDATE_NOT_FOUND": 404,
    "JOB_CANCELED": 409,
    "INVALID_STATE": 409,
    "INVALID_REQUEST": 400,
    "VAULT_LOCKED": 423,
    "VAULT_ERROR": 422,
    "EGRESS_BLOCKED": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INTERNAL": 500,
}


@dataclass
class ConsumerError(RuntimeError):
    code: str
    message: str
    action: str = ""
    details: dict = field(default_factory=dict)
    status: int | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return self.status or ERROR_STATUS.get(self.code, 500)

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "action": self.action,
                "details": self.details,
            }
        }


class SettingsError(ConsumerError):
    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(
            code="INVALID_REQUEST",
            message=message,
            action="检查输入后重试",
            details=details or {},
        )


class StoreError(ConsumerError):
    def __init__(self, message: str, *, code: str = "INTERNAL", action: str = ""):
        super().__init__(code=code, message=message, action=action)
