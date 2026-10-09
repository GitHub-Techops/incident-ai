#!/usr/bin/env bash
# Build incident-demo from a Git tag and deploy it, recording which commit runs.
#
#   scripts/deploy-demo.sh v2     # builds tag demo-app/v2 -> image incident-demo:v2
#
# The image is built from the tagged commit (git archive), not from the working
# tree, so image == commit. The commit, tag and time are written as pod template
# annotations: each Kubernetes revision then says exactly which code it ran,
# and the backend's Git tools can diff one revision against the previous one.
set -euo pipefail

VERSION="${1:?usage: $0 <version, e.g. v1 or v2>}"
[[ "$VERSION" =~ ^v[0-9]+$ ]] || { echo "version must look like v1, v2, ..." >&2; exit 1; }

NS=incident-lab
REF="demo-app/$VERSION"
IMAGE="incident-demo:$VERSION"
cd "$(git rev-parse --show-toplevel)"

COMMIT=$(git rev-parse --verify --quiet "refs/tags/$REF^{commit}") \
  || { echo "no git tag $REF" >&2; exit 1; }
echo "==> $REF is commit ${COMMIT:0:7}: $(git log -1 --format=%s "$COMMIT")"

echo "==> building $IMAGE from that commit"
git archive --format=tar "$COMMIT:demo-app" | docker build --quiet \
  --build-arg APP_VERSION="$VERSION" \
  --label org.opencontainers.image.revision="$COMMIT" \
  --label org.opencontainers.image.version="$VERSION" \
  -t "$IMAGE" - >/dev/null

echo "==> loading $IMAGE into kind"
kind load docker-image "$IMAGE" --name incident-ai >/dev/null

echo "==> copying git history to the backend"
scripts/sync-git-mirror.sh

DEPLOYED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "==> deploying $IMAGE to $NS"
# One patch = one rollout = one new revision. deployed-at makes every deploy a
# new ReplicaSet (so its creation time is the deploy time), even a redeploy.
kubectl patch deployment incident-demo -n "$NS" --type=strategic -p "$(cat <<JSON
{
  "metadata": {"annotations": {"kubernetes.io/change-cause": "deploy-demo.sh $VERSION (commit ${COMMIT:0:7})"}},
  "spec": {"template": {
    "metadata": {
      "labels": {"app.kubernetes.io/version": "$VERSION"},
      "annotations": {
        "incident-ai.dev/git-commit": "$COMMIT",
        "incident-ai.dev/git-ref": "$REF",
        "incident-ai.dev/deployed-at": "$DEPLOYED_AT"
      }
    },
    "spec": {"containers": [{"name": "incident-demo", "image": "$IMAGE"}]}
  }}
}
JSON
)"
kubectl rollout status deployment/incident-demo -n "$NS" --timeout=120s
# The new ReplicaSet has its own copy of change-cause now. Remove it from the
# Deployment, or Kubernetes copies it onto every later revision (kubectl set env,
# rollout restart, ...) and labels those as this release.
kubectl annotate deployment incident-demo -n "$NS" kubernetes.io/change-cause- >/dev/null
kubectl rollout history deployment/incident-demo -n "$NS" | tail -n 3
