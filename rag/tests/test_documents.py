"""Tests for loading knowledge documents, plus checks on the real knowledge base."""

from pathlib import Path

import pytest
import yaml

from ingestion.documents import DEFAULT_KNOWLEDGE_DIR, DOC_TYPES, DocumentError, load_documents, parse_document

REPO = Path(__file__).resolve().parents[2]

VALID = """---
title: High HTTP error rate
type: runbook
services: [incident-demo]
alerts: [HighErrorRate]
tags: [http]
date: 2026-10-07
---

# High HTTP error rate

Body.
"""


def test_parse_valid_document():
    doc = parse_document(VALID, "runbooks/x.md")
    assert doc.title == "High HTTP error rate"
    assert doc.type == "runbook"
    assert doc.services == ("incident-demo",)
    assert doc.alerts == ("HighErrorRate",)
    assert doc.extra == {"date": "2026-10-07"}
    assert doc.body.startswith("# High HTTP error rate")


@pytest.mark.parametrize("text, message", [
    ("# no front matter\n", "must start with"),
    ("---\ntitle: x\ntype: runbook\n", "not closed"),
    ("---\ntype: runbook\n---\nbody\n", "'title' is required"),
    ("---\ntitle: x\ntype: blog\n---\nbody\n", "'type' must be one of"),
    ("---\ntitle: x\ntype: runbook\ntags: http\n---\nbody\n", "'tags' must be a list"),
    ("---\ntitle: [unclosed\n---\nbody\n", "invalid front matter"),
])
def test_rejects_malformed_documents(text, message):
    with pytest.raises(DocumentError, match=message):
        parse_document(text, "runbooks/x.md")


def test_type_must_match_folder_and_readme_is_skipped(tmp_path):
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "README.md").write_text("not a document")
    (tmp_path / "runbooks" / "README.md").write_text("not a document either")
    (tmp_path / "runbooks" / "ok.md").write_text(VALID)
    assert [d.source for d in load_documents(tmp_path)] == ["runbooks/ok.md"]

    (tmp_path / "runbooks" / "wrong.md").write_text(VALID.replace("type: runbook", "type: incident"))
    with pytest.raises(DocumentError, match="does not match folder"):
        load_documents(tmp_path)


def test_rejects_unknown_folder(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "x.md").write_text(VALID)
    with pytest.raises(DocumentError, match="must be in one of the folders"):
        load_documents(tmp_path)


# --- the real knowledge base -------------------------------------------------

@pytest.fixture(scope="module")
def knowledge():
    return load_documents(DEFAULT_KNOWLEDGE_DIR)


def test_knowledge_base_loads_and_covers_every_folder(knowledge):
    assert {d.type for d in knowledge} == set(DOC_TYPES.values())
    sources = {d.source for d in knowledge}
    for required in ["runbooks/high-error-rate.md", "runbooks/database-errors.md",
                     "runbooks/crashloopbackoff.md", "runbooks/deployment-failure.md",
                     "runbooks/incident-response.md", "troubleshooting/kubernetes.md"]:
        assert required in sources


def alert_rules() -> list[dict]:
    rules = []
    for path in sorted((REPO / "k8s" / "alerts").glob("*.yaml")):
        for manifest in yaml.safe_load_all(path.read_text()):
            for group in manifest["spec"]["groups"]:
                rules += [r for r in group["rules"] if "alert" in r]
    return rules


def test_every_alert_runbook_annotation_points_to_a_runbook():
    rules = alert_rules()
    assert rules
    for rule in rules:
        runbook = rule["annotations"]["runbook"]
        assert (DEFAULT_KNOWLEDGE_DIR / "runbooks" / runbook).is_file(), f"{rule['alert']}: {runbook} missing"


def test_documents_only_reference_alerts_that_exist(knowledge):
    known = {r["alert"] for r in alert_rules()}
    for doc in knowledge:
        assert set(doc.alerts) <= known, f"{doc.source}: unknown alerts {set(doc.alerts) - known}"
