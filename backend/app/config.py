"""Backend configuration, read once from environment variables."""

import os
from dataclasses import dataclass


def _csv(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    app_version: str = "dev"
    log_level: str = "INFO"
    # Namespaces the investigation tools may read. Kubernetes RBAC enforces the
    # same limit in the cluster; this check fails fast, with a clear message,
    # before any API call is made.
    allowed_namespaces: frozenset[str] = frozenset({"incident-lab"})
    # How many recent log lines to fetch per pod.
    log_tail_lines: int = 100

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_version=os.getenv("APP_VERSION", "dev"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            allowed_namespaces=_csv(os.getenv("ALLOWED_NAMESPACES", "incident-lab")),
            log_tail_lines=int(os.getenv("LOG_TAIL_LINES", "100")),
        )
