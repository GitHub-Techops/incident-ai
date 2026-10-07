#!/usr/bin/env bash
# Opens local tunnels to the lab UIs. Nothing is exposed beyond localhost.
# Usage: ./scripts/port-forward.sh     (Ctrl+C stops all tunnels)
set -euo pipefail

trap 'kill 0' EXIT

kubectl port-forward -n monitoring   svc/monitoring-grafana                      3000:80   >/dev/null &
kubectl port-forward -n monitoring   svc/monitoring-kube-prometheus-prometheus   9090:9090 >/dev/null &
kubectl port-forward -n monitoring   svc/monitoring-kube-prometheus-alertmanager 9093:9093 >/dev/null &
kubectl port-forward -n incident-lab svc/incident-demo                           8081:80   >/dev/null &

cat <<EOF
Tunnels open (Ctrl+C to stop):
  Grafana       http://localhost:3000   (user: admin)
  Prometheus    http://localhost:9090
  Alertmanager  http://localhost:9093
  incident-demo http://localhost:8081

Grafana password:
  kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d; echo
EOF

wait
