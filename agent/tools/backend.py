"""Client for the incident backend's API.

The agent never talks to Kubernetes, Prometheus or Git itself: it asks the
backend, whose tools enforce the namespace allow-list, read-only RBAC and
input validation (CLAUDE.md: the backend/tool layer enforces safety).
"""

import json
import re
import urllib.error
import urllib.request

INCIDENT_ID = re.compile(r"^INC-\d{8}-[0-9a-f]{6}$")
EVIDENCE_KINDS = ("kubernetes", "metrics", "deployment")


class BackendError(RuntimeError):
    pass


class BackendClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str) -> dict | list:
        request = urllib.request.Request(self.base_url + path, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise BackendError(f"{method} {path}: HTTP {exc.code} {exc.read().decode()[:300]}") from exc
        except urllib.error.URLError as exc:
            raise BackendError(f"cannot reach the backend at {self.base_url}: {exc.reason} "
                               "(kubectl port-forward -n incident-ai svc/incident-backend 8080:80)") from exc

    @staticmethod
    def _check_id(incident_id: str) -> None:
        # The id goes into a URL path: accept only the backend's own format.
        if not INCIDENT_ID.match(incident_id):
            raise BackendError(f"not an incident id: {incident_id!r}")

    def list_incidents(self, status: str | None = None) -> list[dict]:
        return self._request("GET", "/incidents" + (f"?status={status}" if status in ("open", "resolved") else ""))

    def get_incident(self, incident_id: str) -> dict:
        self._check_id(incident_id)
        return self._request("GET", f"/incidents/{incident_id}")

    def collect_evidence(self, incident_id: str, kind: str) -> dict:
        """Ask the backend to collect evidence now; it also attaches it to the incident."""
        self._check_id(incident_id)
        if kind not in EVIDENCE_KINDS:
            raise BackendError(f"unknown evidence kind {kind!r}")
        return self._request("POST", f"/incidents/{incident_id}/evidence/{kind}")
