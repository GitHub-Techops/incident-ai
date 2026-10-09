#!/usr/bin/env bash
# Copy this repository's history (all branches and tags) into the backend pod
# as a git bundle, so the Git investigation tools can read it.
# The backend fetches from the bundle on its next deployment-evidence request.
# Usage: scripts/sync-git-mirror.sh
set -euo pipefail

NS=incident-ai
DEST=/data/git/incident-ai.bundle
cd "$(git rev-parse --show-toplevel)"

BUNDLE=$(mktemp --suffix=.bundle)
trap 'rm -f "$BUNDLE"' EXIT
git bundle create --quiet "$BUNDLE" --branches --tags

# A running pod that isn't being deleted (just after a backend rollout, the old
# pod is still "Running" while it terminates).
POD=$(kubectl get pods -n "$NS" -l app.kubernetes.io/name=incident-backend \
  --field-selector=status.phase=Running \
  -o go-template='{{range .items}}{{if not .metadata.deletionTimestamp}}{{.metadata.name}}{{"\n"}}{{end}}{{end}}' | head -n 1)
[ -n "$POD" ] || { echo "no running incident-backend pod" >&2; exit 1; }
# Copy under a temporary name, then rename: the backend never sees a half-written bundle.
kubectl cp -n "$NS" -c incident-backend "$BUNDLE" "$POD:$DEST.tmp"
kubectl exec -n "$NS" -c incident-backend "$POD" -- mv "$DEST.tmp" "$DEST"

echo "git history copied to $POD: HEAD $(git rev-parse --short HEAD), tags: $(git tag --list 'demo-app/*' | tr '\n' ' ')"
