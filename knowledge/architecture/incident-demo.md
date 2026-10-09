---
title: incident-demo service architecture
type: architecture
services: [incident-demo]
alerts: [HighErrorRate]
tags: [orders, database, configuration, dependencies, endpoints]
---

# incident-demo service architecture

## Purpose

`incident-demo` is the orders service of the lab. It accepts orders on
`/api/order` and stores them in the orders database. It runs in the
`incident-lab` namespace with 2 replicas behind the `incident-demo` Service.

## Endpoints

| Endpoint | What it does | Depends on |
|---|---|---|
| `/` | Service name and version | nothing |
| `/health` | Shallow health check used by the probes | nothing (does not check the database) |
| `/api/order` | Creates an order | orders database |
| `/metrics` | Prometheus metrics | nothing |

Because `/health` does not test the database, a database problem makes
`/api/order` fail while pods stay Ready and nothing restarts.

## Dependencies

| Dependency | Address | Notes |
|---|---|---|
| Orders database (PostgreSQL) | `orders-db:5432` | The only database the service uses |

In this lab the orders database is simulated inside the application: it
behaves like a PostgreSQL server listening on `orders-db:5432`. Connections to
any other address are refused, exactly like a real client pointed at the wrong
host or port.

## Configuration

Configuration comes from environment variables. The ConfigMap
`incident-demo-config` is loaded with `envFrom`; variables set directly on the
Deployment override it.

| Variable | Default | Meaning |
|---|---|---|
| `APP_VERSION` | set at build time | Version reported by `/`, logs and `app_info` |
| `FAIL_MODE` | `false` | Lab switch: `true` makes the orders database unreachable |

## Traffic

The `traffic-generator` deployment calls `/api/order` about twice per second
(one request, then a 0.5 s pause), so there is always traffic to measure and an
order failure shows up in the error rate within a minute. `/health` traffic
(about 0.6 requests per second) comes from the Kubernetes readiness and
liveness probes of the two pods. Because orders are most of the traffic, an
`/api/order` outage puts the overall 5xx ratio at roughly 55-70%, not 100%
(failing orders are also slower, so fewer of them are sent).

## Releases

Versions are Git tags `demo-app/v1`, `demo-app/v2`, ... The release script
`scripts/deploy-demo.sh <version>` builds the image from the tagged commit and
records the commit and deployment time on the pod template.

## Logs

One JSON object per line. Failed orders log at level ERROR with
`msg: order failed`, an `error` field and the database address in `db_host`.
Every request with a 5xx status also logs a `request` line at level ERROR.
