"""Backend configuration, read once from environment variables."""

import os
from dataclasses import dataclass, field


def _csv(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _mapping(value: str) -> dict[str, str]:
    """"a=x,b=y" -> {"a": "x", "b": "y"}"""
    pairs = (part.split("=", 1) for part in value.split(",") if "=" in part)
    return {key.strip(): val.strip() for key, val in pairs}


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
    # In-cluster Prometheus. For local runs: port-forward and use http://localhost:9090.
    prometheus_url: str = "http://monitoring-kube-prometheus-prometheus.monitoring:9090"
    prometheus_timeout_seconds: float = 10.0
    # How far before the alert the metrics window starts.
    metrics_lookback_minutes: int = 10
    # Git history: a bundle copied in by scripts/sync-git-mirror.sh (in the cluster),
    # or a repository path for local runs, e.g. GIT_SOURCE=.. from backend/.
    git_source: str = "/data/git/incident-ai.bundle"
    git_cache_dir: str = "/data/git/cache.git"
    # service name -> its directory in the repository.
    git_service_paths: dict[str, str] = field(default_factory=lambda: {"incident-demo": "demo-app"})

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            app_version=os.getenv("APP_VERSION", defaults.app_version),
            log_level=os.getenv("LOG_LEVEL", defaults.log_level),
            allowed_namespaces=_csv(os.getenv("ALLOWED_NAMESPACES", "incident-lab")),
            log_tail_lines=int(os.getenv("LOG_TAIL_LINES", defaults.log_tail_lines)),
            prometheus_url=os.getenv("PROMETHEUS_URL", defaults.prometheus_url),
            prometheus_timeout_seconds=float(
                os.getenv("PROMETHEUS_TIMEOUT_SECONDS", defaults.prometheus_timeout_seconds)),
            metrics_lookback_minutes=int(
                os.getenv("METRICS_LOOKBACK_MINUTES", defaults.metrics_lookback_minutes)),
            git_source=os.getenv("GIT_SOURCE", defaults.git_source),
            git_cache_dir=os.getenv("GIT_CACHE_DIR", defaults.git_cache_dir),
            git_service_paths=(_mapping(os.environ["GIT_SERVICE_PATHS"]) if "GIT_SERVICE_PATHS" in os.environ
                               else defaults.git_service_paths),
        )
