"""Tests for splitting documents into chunks."""

import pytest

from ingestion.chunking import MAX_CHARS, chunk_document, chunk_documents, split_sections
from ingestion.documents import DEFAULT_KNOWLEDGE_DIR, Document, load_documents


def doc(body: str) -> Document:
    return Document(source="runbooks/t.md", title="Test doc", type="runbook", body=body,
                    services=("incident-demo",), alerts=("HighErrorRate",))


def test_sections_follow_h2_and_h3_headings():
    body = "# Test doc\n\nIntro.\n\n## Diagnose\n\nLook.\n\n### Logs\n\nRead.\n\n## Fix\n\nRoll back.\n"
    assert split_sections(body) == [
        ("", "Intro."), ("Diagnose", "Look."), ("Diagnose > Logs", "Read."), ("Fix", "Roll back."),
    ]


def test_hash_lines_in_code_blocks_are_not_headings():
    body = "## Commands\n\n```bash\n# list pods\nkubectl get pods\n```\n"
    assert split_sections(body) == [("Commands", "```bash\n# list pods\nkubectl get pods\n```")]


def test_chunk_text_starts_with_its_context_and_keeps_source():
    chunks = chunk_document(doc("## Fix\n\nRoll back.\n"))
    assert len(chunks) == 1
    c = chunks[0]
    assert c.text == "Test doc > Fix\n\nRoll back."
    assert (c.chunk_id, c.source, c.section, c.doc_type) == ("runbooks/t.md#0", "runbooks/t.md", "Fix", "runbook")
    assert c.alerts == ("HighErrorRate",)


def test_long_section_splits_on_paragraphs_and_never_inside_code():
    paragraphs = [f"Paragraph {i}." + " word" * 60 for i in range(6)]
    code = "```bash\n" + "\n".join(f"kubectl get pods -n ns{i}" for i in range(15)) + "\n\n# still code\n```"
    body = "## Long\n\n" + "\n\n".join(paragraphs[:3] + [code] + paragraphs[3:]) + "\n"
    chunks = chunk_document(doc(body), max_chars=800)

    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 800
        assert c.text.startswith("Test doc > Long\n\n")
        assert c.text.count("```") % 2 == 0  # code block not cut in half
    assert sum(c.text.count(p) for c in chunks for p in paragraphs) == len(paragraphs)  # nothing lost
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_oversized_single_block_is_hard_split():
    chunks = chunk_document(doc("## Huge\n\n" + "x" * 2000 + "\n"), max_chars=500)
    assert all(len(c.text) <= 500 for c in chunks)
    assert "".join(c.text.split("\n\n", 1)[1] for c in chunks) == "x" * 2000


def test_identical_text_gets_identical_hash():
    a, b = chunk_document(doc("## A\n\nSame.\n")), chunk_document(doc("## A\n\nSame.\n"))
    assert a[0].content_hash == b[0].content_hash
    assert chunk_document(doc("## A\n\nChanged.\n"))[0].content_hash != a[0].content_hash


# --- the real knowledge base -------------------------------------------------

@pytest.fixture(scope="module")
def chunks():
    return chunk_documents(load_documents(DEFAULT_KNOWLEDGE_DIR))


def test_real_chunks_are_bounded_unique_and_traceable(chunks):
    assert len(chunks) > 40
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    for c in chunks:
        assert len(c.text) <= MAX_CHARS, c.chunk_id
        assert c.text.count("```") % 2 == 0, c.chunk_id
        assert c.text.startswith(c.title)
        assert (DEFAULT_KNOWLEDGE_DIR / c.source).is_file()


def test_database_question_has_matching_chunks(chunks):
    """Keyword stand-in for 'How do we troubleshoot database connection failures?'
    until semantic search exists (Milestone 12)."""
    sources = {c.source for c in chunks if "connection refused" in c.text.lower()}
    assert "runbooks/database-errors.md" in sources
