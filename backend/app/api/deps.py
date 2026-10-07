"""Shared FastAPI dependencies."""

from fastapi import Request

from app.services.incident_store import IncidentStore


def get_store(request: Request) -> IncidentStore:
    # One store per app instance (created in create_app), so each test gets a fresh one.
    return request.app.state.store
