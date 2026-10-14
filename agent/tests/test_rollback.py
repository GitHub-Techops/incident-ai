"""The rollback rules: when the evidence supports a rollback, and to which revision."""

from nodes.rollback import assess_rollback, rollback_proposal

V1 = {"revision": 33, "images": ["incident-demo:v1"], "git_commit": "8b8c46c" + "0" * 33}
V2 = {"revision": 34, "images": ["incident-demo:v2"], "git_commit": "cd1ffef" + "0" * 33,
      "changes_from_previous": ["image: incident-demo:v1 -> incident-demo:v2", "git commit: 8b8c46c -> cd1ffef"]}


def evidence(**overrides):
    return {"active_revision": V2, "previous_revision": V1, "deployed_before_incident_seconds": 65,
            "revisions_after_incident": [], **overrides}


def test_recent_real_change_with_model_agreement_is_justified():
    a = assess_rollback(evidence(), model_recommends=True)
    assert a["justified"] and a["blockers"] == []
    assert (a["from_revision"], a["to_revision"]) == (34, 33)  # from the evidence, not the model
    assert "deployed 1.1 min before the alert" in a["supporting"][0]


def test_model_recommendation_alone_is_not_enough():
    a = assess_rollback(evidence(deployed_before_incident_seconds=3 * 3600), model_recommends=True)
    assert not a["justified"]
    assert "no recent change to undo" in a["blockers"][0]
    assert a["note"].startswith("The model recommended a rollback, but")


def test_restart_only_revision_is_not_rolled_back():
    restart = {**V2, "changes_from_previous": ["annotation kubectl.kubernetes.io/restartedAt: a -> b"]}
    a = assess_rollback(evidence(active_revision=restart), model_recommends=True)
    assert not a["justified"]
    assert any("nothing but a restart" in b for b in a["blockers"])


def test_changes_after_the_alert_block_the_rollback():
    a = assess_rollback(evidence(revisions_after_incident=[{"revision": 35}]), model_recommends=True)
    assert not a["justified"]
    assert any("revision 35" in b for b in a["blockers"])


def test_no_previous_revision_or_model_disagreement():
    assert not assess_rollback(evidence(previous_revision=None), model_recommends=True)["justified"]
    a = assess_rollback(evidence(), model_recommends=False)
    assert not a["justified"] and a["blockers"] == []
    assert "did not recommend" in a["note"]


def test_proposal_names_one_deployment_and_one_target():
    p = rollback_proposal("incident-lab", "incident-demo", assess_rollback(evidence(), True),
                          {"rollback_reason": "v2 introduced it"})
    assert (p["action"], p["namespace"], p["deployment"], p["to_revision"]) == \
        ("rollback_deployment", "incident-lab", "incident-demo", 33)
    assert "revision 33 (incident-demo:v1, commit 8b8c46c)" in p["summary"]
