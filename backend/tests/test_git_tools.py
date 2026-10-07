"""Tests for the Git tools and the deployment evidence collector.

They run the real `git` binary against a small repository built in a temp dir.
"""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kubernetes import client as k

from app.api.deps import get_git_tools, get_k8s_tools
from app.main import create_app
from app.services.evidence import collect_deployment_evidence
from app.tools import git as git_module
from app.tools.errors import InvalidTargetError, NamespaceNotAllowedError
from app.tools.git import GitError, GitTools
from tests.fake_k8s import APP, NS, FakeApps, deployment, make_tools, replicaset

FIXTURES = Path(__file__).parent / "fixtures"
INCIDENT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


class Repo:
    """A throwaway repository with a demo-app v1 -> v2 history."""

    def __init__(self, path: Path):
        self.path = path
        self.clock = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
        path.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "Dev One", "GIT_AUTHOR_EMAIL": "dev@example.com",
               "GIT_COMMITTER_NAME": "Dev One", "GIT_COMMITTER_EMAIL": "dev@example.com",
               "GIT_AUTHOR_DATE": self.clock.isoformat(), "GIT_COMMITTER_DATE": self.clock.isoformat()}
        return subprocess.run(["git", *args], cwd=self.path, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str, files: dict[str, str]) -> str:
        self.clock += timedelta(minutes=5)
        for name, content in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(content)
        self.git("add", *files)
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Repo:
    r = Repo(tmp_path / "repo")
    r.v1 = r.commit("demo-app: first version", {"demo-app/app.py": "PORT = 5432\n", "README.md": "lab\n"})
    r.git("tag", "-a", "demo-app/v1", "-m", "v1")  # annotated tag
    r.unrelated = r.commit("backend: unrelated change", {"backend/x.py": "x = 1\n"})
    r.refactor = r.commit("demo-app: move DB code", {"demo-app/db.py": "def connect(): ...\n"})
    r.v2 = r.commit("demo-app: configurable DB port\n\nRead the port from the environment.",
                    {"demo-app/app.py": "PORT = int(env('PORT', '5433'))\n"})
    r.git("tag", "demo-app/v2")  # lightweight tag
    return r


@pytest.fixture
def git(repo, tmp_path) -> GitTools:
    tools = GitTools(str(repo.path), str(tmp_path / "cache.git"), {APP: "demo-app"})
    tools.sync()
    return tools


# --- Git tools ---------------------------------------------------------------

def test_commits_between_only_include_the_services_path(git, repo):
    commits = git.get_commits_between(APP, repo.v1, repo.v2)
    assert [c.sha for c in commits] == [repo.v2, repo.refactor]  # newest first, no backend commit
    assert commits[0].subject == "demo-app: configurable DB port"
    assert commits[0].body == "Read the port from the environment."
    assert commits[0].author == "Dev One"
    assert commits[0].tags == ["demo-app/v2"]
    assert [(f.path, f.status) for f in commits[0].files] == [("demo-app/app.py", "M")]


def test_diff_shows_the_code_change(git, repo):
    diff = git.get_diff(APP, repo.v1[:7], repo.v2)
    assert diff.from_sha == repo.v1
    assert [(f.path, f.status) for f in diff.files] == [("demo-app/app.py", "M"), ("demo-app/db.py", "A")]
    assert (diff.additions, diff.deletions) == (2, 1)
    assert "-PORT = 5432" in diff.patch and "+PORT = int(env('PORT', '5433'))" in diff.patch
    assert "backend/" not in diff.patch
    assert diff.truncated is False


def test_diff_is_truncated(git, repo, monkeypatch):
    monkeypatch.setattr(git_module, "MAX_PATCH_CHARS", 50)
    diff = git.get_diff(APP, repo.v1, repo.v2)
    assert diff.truncated is True
    assert diff.patch.endswith("(diff truncated)")


def test_get_commit_works_for_the_first_commit_and_annotated_tags(git, repo):
    commit = git.get_commit(APP, repo.v1)
    assert commit.tags == ["demo-app/v1"]
    assert [f.path for f in commit.files] == ["demo-app/app.py"]  # README is outside demo-app/


def test_recent_commits_touching_the_service(git, repo):
    commits = git.get_recent_commits(APP, repo.v2, limit=5)
    assert [c.sha for c in commits] == [repo.v2, repo.refactor, repo.v1]


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "HEAD", "demo-app/v1", "abc123; rm -rf /", "ABCDEF1", ""])
def test_rejects_anything_that_is_not_a_sha(git, ref):
    with pytest.raises(InvalidTargetError):
        git.get_diff(APP, ref, ref)


def test_unknown_sha_is_a_git_error(git):
    with pytest.raises(GitError):
        git.get_commit(APP, "0" * 40)


def test_rejects_service_without_configured_path(git, repo):
    with pytest.raises(InvalidTargetError):
        git.get_commit("payments", repo.v2)


def test_sync_from_a_bundle_and_pick_up_new_bundles(repo, tmp_path):
    bundle = tmp_path / "incident-ai.bundle"
    tools = GitTools(str(bundle), str(tmp_path / "cache.git"), {APP: "demo-app"})
    with pytest.raises(GitError, match="sync-git-mirror"):
        tools.sync()

    repo.git("bundle", "create", "--quiet", str(bundle), "--branches", "--tags")
    tools.sync()
    assert tools.get_commit(APP, repo.v2).sha == repo.v2

    v3 = repo.commit("demo-app: v3", {"demo-app/app.py": "PORT = 5432\n"})
    repo.git("bundle", "create", "--quiet", str(bundle) + ".tmp", "--branches", "--tags")
    os.replace(str(bundle) + ".tmp", bundle)  # what sync-git-mirror.sh does
    os.utime(bundle, (1, 1))  # make sure the mtime differs, even on coarse clocks
    tools.sync()
    assert tools.get_commit(APP, v3).sha == v3


# --- deployment evidence -----------------------------------------------------

def rs(revision: int, commit: str | None, minutes_from_incident: int, image: str = f"{APP}:v1",
       env: list[k.V1EnvVar] | None = None):
    annotations = {"incident-ai.dev/git-commit": commit, "incident-ai.dev/git-ref": "demo-app/vX"} if commit else None
    return replicaset(f"{APP}-rs{revision}", str(revision), env or [], replicas=0,
                      created=INCIDENT + timedelta(minutes=minutes_from_incident),
                      image=image, annotations=annotations)


def k8s_with(*replicasets, current: str):
    return make_tools(apps=FakeApps(deployment([], revision=current), replicasets))


def test_finds_the_code_change_deployed_before_the_incident(git, repo):
    k8s = k8s_with(
        rs(1, repo.v1, -60),
        rs(2, repo.v2, -3, image=f"{APP}:v2"),
        rs(3, repo.v1, 5),  # the fix: back to v1 after the alert
        current="3",
    )
    evidence = collect_deployment_evidence(k8s, git, NS, APP, INCIDENT)

    assert evidence.errors == []
    assert evidence.active_revision.revision == 2
    assert evidence.previous_revision.revision == 1
    assert evidence.deployed_before_incident_seconds == 180
    assert evidence.current_commit.sha == repo.v2
    assert evidence.previous_commit.sha == repo.v1
    assert [c.sha for c in evidence.commits] == [repo.v2, repo.refactor]
    assert "+PORT = int(env('PORT', '5433'))" in evidence.diff.patch
    assert [r.revision for r in evidence.revisions_after_incident] == [3]

    facts = evidence.facts
    assert facts[0].startswith(f"revision 2 ({APP}:v2, commit {repo.v2[:7]}) was deployed 3m00s before the alert")
    assert f"image: {APP}:v1 -> {APP}:v2" in facts[1]
    assert f"git commit: {repo.v1[:7]} -> {repo.v2[:7]}" in facts[1]
    assert "2 commit(s), 2 file(s) changed, +2 -1" in facts[2]
    assert facts[-1].startswith("revision 3") and "5m00s after the alert" in facts[-1]


def test_config_only_change_has_no_code_diff(git, repo):
    fail_mode = [k.V1EnvVar(name="FAIL_MODE", value="true")]
    k8s = k8s_with(rs(1, repo.v2, -30), rs(2, repo.v2, -1, env=fail_mode), current="2")
    evidence = collect_deployment_evidence(k8s, git, NS, APP, INCIDENT)

    assert evidence.diff is None and evidence.commits == []
    assert "env FAIL_MODE: <unset> -> true" in evidence.facts[1]
    assert "changed only the Kubernetes deployment, not the code" in evidence.facts[2]


def test_revisions_without_git_annotations_are_reported(git, repo):
    k8s = k8s_with(rs(1, None, -30), current="1")
    evidence = collect_deployment_evidence(k8s, git, NS, APP, INCIDENT)
    assert evidence.active_revision.revision == 1
    assert evidence.errors == ["revision 1 has no git commit annotation (not deployed with scripts/deploy-demo.sh)"]


def test_previous_revision_without_commit_falls_back_to_recent_commits(git, repo):
    k8s = k8s_with(rs(1, None, -30), rs(2, repo.v2, -3), current="2")
    evidence = collect_deployment_evidence(k8s, git, NS, APP, INCIDENT)
    assert evidence.diff is None
    assert [c.sha for c in evidence.commits] == [repo.v2, repo.refactor, repo.v1]
    assert "previous revision has no git commit annotation" in evidence.errors[0]


def test_rejects_disallowed_namespace(git):
    with pytest.raises(NamespaceNotAllowedError):
        collect_deployment_evidence(make_tools(), git, "kube-system", APP, INCIDENT)


# --- API ---------------------------------------------------------------------

def test_deployment_evidence_endpoint(git, repo):
    alert_start = datetime(2026, 10, 7, 8, 30, tzinfo=UTC)  # the fixture's startsAt
    k8s = k8s_with(
        replicaset(f"{APP}-rs1", "1", [], 0, created=alert_start - timedelta(hours=1),
                   annotations={"incident-ai.dev/git-commit": repo.v1}),
        replicaset(f"{APP}-rs2", "2", [], 2, created=alert_start - timedelta(minutes=2),
                   image=f"{APP}:v2", annotations={"incident-ai.dev/git-commit": repo.v2}),
        current="2",
    )
    app = create_app()
    app.dependency_overrides[get_k8s_tools] = lambda: k8s
    app.dependency_overrides[get_git_tools] = lambda: git
    client = TestClient(app)

    firing = json.loads((FIXTURES / "alertmanager_firing.json").read_text())
    incident_id = client.post("/webhook/alert", json=firing).json()["created"][0]
    r = client.post(f"/incidents/{incident_id}/evidence/deployment")

    assert r.status_code == 200
    assert r.json()["active_revision"]["revision"] == 2
    assert r.json()["diff"]["files"][0]["path"] == "demo-app/app.py"
    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["deployment_evidence"]["current_commit"]["sha"] == repo.v2
    assert "was deployed 2m00s before the alert" in incident["timeline"][-1]["event"]
