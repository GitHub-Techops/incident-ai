"""The claim checks, on the shapes of mistakes the model really made."""

import json
from pathlib import Path

import pytest

from report.build import capped_confidence, check_analysis
from report.verify import check_fact, details, normalize
from schemas import Fact, RootCauseAnalysis

# Real evidence text, as the model received it (fixture JSON + formatted diff).
EVIDENCE = normalize('''
"sample_error_log": "{\\"level\\": \\"ERROR\\", \\"msg\\": \\"order failed\\", \\"error\\": \\"database connection refused\\", \\"db_host\\": \\"orders-db:5433\\"}"
- Commit cd1ffef by Simranjeet Singh: demo-app: make the orders DB host and port configurable (files: demo-app/app/main.py)
@@ -12,10 +12,15 @@ from fastapi import FastAPI, Request, Response
+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5433"))
+LISTEN_ADDRESS = ("orders-db", 5432)
error_rate: [all] baseline 0.0%, incident 50.6%, peak 67.3%; 188 error lines
''')


def fact(statement, quote, source="deployment"):
    return Fact(statement=statement, source=source, quote=quote)


def test_true_fact_with_exact_quote_is_verified():
    f = fact("The default DB port in main.py is 5433.", '+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5433"))')
    assert check_fact(f, EVIDENCE) == []


def test_quote_from_json_evidence_matches_without_escapes():
    # The model copies the log line unescaped; the evidence held it as an escaped JSON string.
    f = fact("Logs show connection refused.", '"error": "database connection refused", "db_host": "orders-db:5433"',
             "kubernetes")
    assert check_fact(f, EVIDENCE) == []


def test_invented_line_number_is_flagged_even_with_a_real_quote():
    # What gemma4 did in Milestones 13 and 14: real code, made-up line number.
    f = fact("In main.py the default DB_PORT is 5433 (line 18).",
             '+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5433"))')
    assert check_fact(f, EVIDENCE) == ["'line 18' does not appear in the evidence"]


def test_paraphrased_or_missing_quote_is_flagged():
    assert check_fact(fact("Port is wrong.", "the port is set to 5433 by default"), EVIDENCE) == [
        "the quote does not appear in the evidence"]
    assert "no quote from the evidence" in check_fact(fact("Port is wrong.", ""), EVIDENCE)


@pytest.mark.parametrize("statement, bad", [
    ("Commit abc1234 changed the port.", "abc1234"),
    ("The database listens on 6543.", "6543"),
    ("The change is in demo-app/app/config.py.", "demo-app/app/config.py"),
])
def test_invented_details_are_flagged(statement, bad):
    assert check_fact(fact(statement, "+LISTEN_ADDRESS = (\"orders-db\", 5432)"), EVIDENCE) == [
        f"'{bad}' does not appear in the evidence"]


def test_details_found_in_the_evidence_pass():
    statement = "Commit cd1ffef changed demo-app/app/main.py; 188 error lines; port 5433 vs 5432."
    assert check_fact(fact(statement, "+LISTEN_ADDRESS = (\"orders-db\", 5432)"), EVIDENCE) == []


def test_times_dates_and_decimals_are_not_treated_as_details():
    assert details("At 2026-10-07T11:04:45Z the error rate was 50.6% (0.506) for 1m17s") == []


def analysis(facts, root_cause="v2 sets the default DB port to 5433", confidence=0.95):
    return RootCauseAnalysis(summary="s", facts=facts, inferences=["i"], root_cause=root_cause,
                             confidence=confidence, recommended_actions=[], rollback_recommended=True,
                             rollback_reason="r", knowledge_sources=[])


def test_confidence_is_capped_by_unsupported_claims():
    good = fact("Default port 5433.", '+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5433"))')
    bad = fact("Port set on line 18.", '+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5433"))')

    issues, root, other = check_analysis(analysis([good]), EVIDENCE)
    assert capped_confidence(0.95, issues, root, other) == (0.95, None)

    issues, root, other = check_analysis(analysis([good, bad]), EVIDENCE)
    value, note = capped_confidence(0.95, issues, root, other)
    assert value == 0.6 and "1 claim(s) could not be verified" in note

    issues, root, other = check_analysis(analysis([good], root_cause="line 18 sets port 5433"), EVIDENCE)
    assert root == ["'line 18' does not appear in the evidence"]
    assert capped_confidence(0.95, issues, root, other)[0] == 0.5


# --- regressions from the live run on INC-20261014-440715 ---------------------

ARCHITECTURE_DOC = normalize("""| Dependency | Address | Notes |
|---|---|---|
| Orders database (PostgreSQL) | `orders-db:5432` | The only database the service uses |""")


def test_quote_with_literal_backslash_n_line_breaks_matches():
    # The model's quote of a table, exactly as it returned it: "\\n" sequences, not newlines.
    quote = ("| Dependency | Address | Notes |\\n|---|---|---|\\n| Orders database (PostgreSQL) | "
             "`orders-db:5432` | The only database the service uses")
    f = fact("The orders database is documented as listening on orders-db:5432.", quote, "knowledge")
    assert check_fact(f, ARCHITECTURE_DOC) == []


REAL_DIFF = normalize(json.loads((Path(__file__).parents[1] / "fixtures" / "v2-incident-evidence.json")
                                 .read_text())["deployment"]["diff"])


def test_diff_line_with_trailing_json_junk_matches():
    # INC-20261014-fa803e: the exact diff line, then '},{' leaked from the JSON output.
    f = fact("The default DB_PORT in the new code is 5433.",
             '+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5433"))},{')
    assert check_fact(f, REAL_DIFF) == []


def test_quote_spanning_several_diff_lines_matches_without_plus_markers():
    # INC-20261014-fa803e: three "+" lines of orders_db.py, joined by the model.
    f = fact("The database is simulated on orders-db:5432.",
             "The lab has no real database. connect() behaves like a client connecting to one that listens "
             "on orders-db:5432: any other address is refused, and FAIL_MODE makes the database unreachable.")
    assert check_fact(f, REAL_DIFF) == []


def test_lenient_matching_still_rejects_changed_content():
    f = fact("The default DB_PORT is 5433.", '+DB_PORT = int(os.getenv("ORDERS_DB_PORT", "5434"))},{')
    assert check_fact(f, REAL_DIFF) == ["the quote does not appear in the evidence"]


def test_invented_line_number_in_the_summary_is_flagged():
    a = analysis([], confidence=0.95)
    a.summary = "The default value of 5433 (line 21 of demo-app/app/main.py) was not overridden."
    a.inferences = ["v2 uses port 5433", "the database listens on 6543"]
    _, _, other = check_analysis(a, EVIDENCE + " demo-app/app/main.py")
    assert other == ["summary: 'line 21' does not appear in the evidence",
                     "inference \"the database listens on 6543\": '6543' does not appear in the evidence"]
    assert capped_confidence(0.95, [], [], other)[0] == 0.6
