"""incident-ai backend: receives alerts and manages incidents."""

import os

from fastapi import FastAPI

from app.api import alerts, chat, incidents
from app.logging_config import setup_logging
from app.services.incident_store import IncidentStore

APP_VERSION = os.getenv("APP_VERSION", "dev")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def create_app() -> FastAPI:
    log = setup_logging(LOG_LEVEL)

    app = FastAPI(title="incident-ai backend", version=APP_VERSION)
    app.state.store = IncidentStore()

    app.include_router(alerts.router)
    app.include_router(incidents.router)
    app.include_router(chat.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "healthy", "version": APP_VERSION}

    log.info("starting", extra={"fields": {"version": APP_VERSION}})
    return app


app = create_app()
