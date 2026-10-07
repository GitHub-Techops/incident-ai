"""Shared FastAPI dependencies."""

from fastapi import Request

from app.services.incident_store import IncidentStore
from app.tools.git import GitTools
from app.tools.kubernetes import KubernetesTools
from app.tools.prometheus import PrometheusClient, PrometheusTools


def get_store(request: Request) -> IncidentStore:
    # One store per app instance (created in create_app), so each test gets a fresh one.
    return request.app.state.store


def get_k8s_tools(request: Request) -> KubernetesTools:
    # Created on first use rather than at startup, so the app (and its tests)
    # can start without a cluster. Tests replace this via dependency_overrides.
    state = request.app.state
    if state.k8s_tools is None:
        settings = state.settings
        state.k8s_tools = KubernetesTools.from_environment(
            settings.allowed_namespaces, settings.log_tail_lines
        )
    return state.k8s_tools


def get_git_tools(request: Request) -> GitTools:
    state = request.app.state
    if state.git_tools is None:
        settings = state.settings
        state.git_tools = GitTools(settings.git_source, settings.git_cache_dir, settings.git_service_paths)
    return state.git_tools


def get_prometheus_tools(request: Request) -> PrometheusTools:
    state = request.app.state
    if state.prometheus_tools is None:
        settings = state.settings
        client = PrometheusClient(settings.prometheus_url, settings.prometheus_timeout_seconds)
        state.prometheus_tools = PrometheusTools(client, settings.allowed_namespaces)
    return state.prometheus_tools
