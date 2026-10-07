"""incident-ai backend: receives alerts and manages incidents."""

from fastapi import FastAPI

from app.api import alerts, chat, incidents
from app.config import Settings
from app.logging_config import setup_logging
from app.services.incident_store import IncidentStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    log = setup_logging(settings.log_level)

    app = FastAPI(title="incident-ai backend", version=settings.app_version)
    app.state.settings = settings
    app.state.store = IncidentStore()
    # Created on first use, see api/deps.py.
    app.state.k8s_tools = None
    app.state.prometheus_tools = None

    app.include_router(alerts.router)
    app.include_router(incidents.router)
    app.include_router(chat.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "healthy", "version": settings.app_version}

    log.info("starting", extra={"fields": {
        "version": settings.app_version,
        "allowed_namespaces": sorted(settings.allowed_namespaces),
        "prometheus_url": settings.prometheus_url,
    }})
    return app


app = create_app()
