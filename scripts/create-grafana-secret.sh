#!/usr/bin/env bash
# Creates the Grafana admin Secret with a random password (run once, before
# installing kube-prometheus-stack). The password never touches Git.
set -euo pipefail

NAMESPACE=monitoring

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

if kubectl get secret grafana-admin -n "$NAMESPACE" >/dev/null 2>&1; then
  echo "Secret grafana-admin already exists; leaving it unchanged."
else
  kubectl create secret generic grafana-admin -n "$NAMESPACE" \
    --from-literal=admin-user=admin \
    --from-literal=admin-password="$(openssl rand -base64 18)"
fi

echo "Read the password with:"
echo "  kubectl get secret grafana-admin -n $NAMESPACE -o jsonpath='{.data.admin-password}' | base64 -d; echo"
