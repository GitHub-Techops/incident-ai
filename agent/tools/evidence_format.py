"""Turn the backend's evidence JSON into compact text for the model.

Why: the raw evidence is large (log tails, every metric point, full ReplicaSet
details) and the model's context is 12k tokens, shared with runbooks and the
answer. These functions keep what an SRE would read first and drop the rest.
Everything kept is copied, never summarized by an LLM, so it stays factual.
"""

MAX_ERROR_LINES = 5
MAX_EVENTS = 8
MAX_REVISIONS = 4
MAX_DIFF_CHARS = 6000


def _value(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "ratio":
        return f"{v:.1%}"
    if unit == "seconds":
        return f"{v * 1000:.0f} ms"
    if unit == "bytes":
        return f"{v / 2**20:.0f} MiB"
    if unit == "cores":
        return f"{v * 1000:.0f} mCPU"
    return f"{v:.3g} {unit}"


def format_alert(incident: dict) -> str:
    alert = incident.get("alert", {})
    labels = alert.get("labels", {})
    return "\n".join([
        f"Incident {incident['incident_id']} ({incident.get('status')}): {incident.get('alert_name')}",
        f"Service {incident.get('service')} in namespace {incident.get('namespace')}, severity {incident.get('severity')}",
        f"Summary: {incident.get('summary')}",
        f"Description: {incident.get('description')}",
        f"Alert started firing at {alert.get('startsAt')}" + (
            f", resolved at {incident['resolved_at']}" if incident.get("resolved_at") else ""),
        f"Alert labels: {', '.join(f'{k}={v}' for k, v in sorted(labels.items()))}",
    ])


def format_kubernetes(ev: dict) -> str:
    out = [f"## Kubernetes evidence (collected {ev['collected_at']})", "Pods:"]
    for p in ev.get("pods", []):
        states = ", ".join(f"{c['name']} {c['state']}" + (f" ({c['reason']})" if c.get("reason") else "")
                           + (f", last terminated: {c['last_termination_reason']}" if c.get("last_termination_reason") else "")
                           for c in p.get("containers", []))
        out.append(f"- {p['name']}: {p['phase']}, ready={p['ready']}, restarts={p['restarts']}; {states}")

    for logs in ev.get("logs", []):
        label = f"{logs['pod']}{' (previous container)' if logs.get('previous') else ''}"
        out.append(f"Logs of {label}: {logs['error_line_count']} error lines out of {logs['line_count']}")
        seen = []
        for line in reversed(logs.get("error_lines", [])):  # most recent first, distinct only
            if line not in seen:
                seen.append(line)
            if len(seen) == MAX_ERROR_LINES:
                break
        out += [f"  {line}" for line in seen]

    events = ev.get("events", [])
    if events:
        out.append("Events (newest first):")
        warnings = [e for e in events if e.get("type") == "Warning"]
        for e in (warnings + [e for e in events if e not in warnings])[:MAX_EVENTS]:
            out.append(f"- {e.get('last_seen')} {e.get('type')} {e.get('reason')} "
                       f"{e.get('object_kind')}/{e.get('object_name')}: {e.get('message')} (x{e.get('count')})")

    d = ev.get("deployment")
    if d:
        out.append(f"Deployment {d['name']}: revision {d.get('revision')}, ready {d['ready_replicas']}/"
                   f"{d['desired_replicas']}, images {', '.join(d['images'])}")
        if d.get("env"):
            out.append(f"  env set on the deployment: {d['env']}")
        if d.get("env_from"):
            out.append(f"  env loaded from: {', '.join(d['env_from'])}")
        out += [f"  condition {c}" for c in d.get("conditions", [])]

    history = ev.get("deployment_history", [])
    if history:
        out.append("Rollout history (newest first):")
        for r in history[:MAX_REVISIONS]:
            changes = "; ".join(r.get("changes_from_previous", [])) or "no change recorded"
            commit = f", commit {r['git_commit'][:7]}" if r.get("git_commit") else ""
            out.append(f"- revision {r['revision']}{' (current)' if r.get('current') else ''} created "
                       f"{r.get('created_at')}, {', '.join(r['images'])}{commit}: {changes}")

    for s in ev.get("services", []):
        out.append(f"Service {s['name']}: {s['ready_endpoints']} ready endpoints, "
                   f"{s['not_ready_endpoints']} not ready")
    for cm in ev.get("configs", []):
        out.append(f"ConfigMap {cm['name']}: {cm.get('data')}")
    if ev.get("errors"):
        out.append(f"Collection gaps: {'; '.join(ev['errors'])}")
    return "\n".join(out)


def format_metrics(ev: dict) -> str:
    out = [f"## Metrics evidence: {ev['window_start']} to {ev['window_end']}, alert at {ev['incident_time']}"
           + (f", resolved at {ev['resolved_time']}" if ev.get("resolved_time") else ""),
           "baseline = before the alert (excluding the 2 minutes just before it), "
           "incident = while firing, recovery = after it resolved"]
    for m in ev.get("metrics", []):
        out.append(f"{m['name']} ({m['description']}):")
        if not m.get("series"):
            out.append("  no data")
        for s in m["series"]:
            sm, unit = s["summary"], m["unit"]
            labels = ", ".join(f"{k}={v}" for k, v in s.get("labels", {}).items()) or "all"
            line = (f"  [{labels}] baseline {_value(sm.get('baseline_avg'), unit)}, "
                    f"incident {_value(sm.get('incident_avg'), unit)}")
            if sm.get("recovery_avg") is not None:
                line += f", recovery {_value(sm['recovery_avg'], unit)}"
            line += f", peak {_value(sm.get('peak'), unit)} at {sm.get('peak_at')}"
            out.append(line)
    if ev.get("errors"):
        out.append(f"Collection gaps: {'; '.join(ev['errors'])}")
    return "\n".join(out)


def format_deployment(ev: dict) -> str:
    out = ["## Deployment and code changes"]
    out += [f"- {fact}" for fact in ev.get("facts", [])]
    diff = ev.get("diff")
    if diff:
        patch = diff["patch"]
        if len(patch) > MAX_DIFF_CHARS:
            patch = patch[:MAX_DIFF_CHARS] + "\n... (diff truncated)"
        out.append(f"Diff {diff['from_sha'][:7]}..{diff['to_sha'][:7]} in {diff['path']}/:\n```diff\n{patch}```")
    if ev.get("errors"):
        out.append(f"Collection gaps: {'; '.join(ev['errors'])}")
    return "\n".join(out)
