# Knowledge base

Operational documents the incident agent searches (RAG). Every retrieved chunk
keeps its source file, so any recommendation can be traced back to a document.

| Folder | What goes there |
|---|---|
| `runbooks/` | What to do when a specific alert or failure happens |
| `troubleshooting/` | How to investigate: tools, queries, commands |
| `architecture/` | How the systems are built: services, dependencies, configuration |
| `incidents/` | Postmortems of incidents that actually happened |

## Writing a document

Markdown with a front matter block:

```yaml
---
title: High HTTP error rate          # required
type: runbook                         # required: runbook | troubleshooting | architecture | incident
services: [incident-demo]             # services it applies to; [any] if generic
alerts: [HighErrorRate]               # alerts it answers, if any
tags: [http, 5xx]
---
```

`type` must match the folder. Use `##` and `###` headings for sections: each
section becomes one or more search chunks, so make sections self-contained
("Check recent deployments", not "Step 3").

Postmortems describe incidents that really happened in the lab, with the
evidence that was collected. Never invent incidents.

Check your changes with `cd rag && .venv/bin/python -m ingestion` and
`.venv/bin/pytest -q`.

This README is not ingested.
