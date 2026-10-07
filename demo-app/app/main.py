"""incident-demo: a deliberately breakable service for the incident-ai lab."""

import json
import logging
import os
import random
import sys
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# --- Configuration (from environment variables, set by the ConfigMap/Deployment later) ---
APP_NAME = "incident-demo"
APP_VERSION = os.getenv("APP_VERSION", "v1")
FAIL_MODE = os.getenv("FAIL_MODE", "false").lower() == "true"

# Paths we label metrics with. Anything else becomes "other" so random URLs
# (scanners, typos) can't create unlimited metric series.
KNOWN_PATHS = {"/", "/health", "/api/order", "/metrics"}
# Healthy hits on these paths are not logged (probes/scrapes would flood the logs).
QUIET_PATHS = {"/health", "/metrics"}


# --- Structured logging: one JSON object per line ---
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": APP_NAME,
            "version": APP_VERSION,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
log = logging.getLogger(APP_NAME)
log.addHandler(handler)
log.setLevel(logging.INFO)
log.propagate = False

# --- Prometheus metrics ---
REQUESTS = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"]
)
APP_INFO = Gauge("app_info", "Application info; value is always 1", ["version"])
APP_INFO.labels(version=APP_VERSION).set(1)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
log.info("starting", extra={"fields": {"fail_mode": FAIL_MODE}})


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    """Record a metric and a log line for every request."""
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    path = request.url.path if request.url.path in KNOWN_PATHS else "other"
    status = response.status_code

    if path != "/metrics":
        REQUESTS.labels(method=request.method, path=path, status=str(status)).inc()
        LATENCY.labels(method=request.method, path=path).observe(duration)

    if not (path in QUIET_PATHS and status < 400):
        level = logging.ERROR if status >= 500 else logging.INFO
        log.log(level, "request", extra={"fields": {
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round(duration * 1000, 1),
        }})
    return response


@app.get("/")
def root() -> dict:
    return {"service": APP_NAME, "version": APP_VERSION}


@app.get("/health")
def health() -> dict:
    # Shallow check on purpose: it does NOT test the database,
    # so it stays healthy during the FAIL_MODE incident.
    return {"status": "healthy", "version": APP_VERSION}


@app.get("/api/order")
def create_order():
    if FAIL_MODE:
        time.sleep(random.uniform(0.2, 0.5))  # simulate waiting on a DB connection
        log.error("order failed", extra={"fields": {
            "error": "database connection refused",
            "db_host": "orders-db:5432",
        }})
        return JSONResponse(status_code=500, content={"error": "internal server error"})

    time.sleep(random.uniform(0.01, 0.05))  # simulate normal work
    return {"order_id": uuid.uuid4().hex[:8], "status": "created", "version": APP_VERSION}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)