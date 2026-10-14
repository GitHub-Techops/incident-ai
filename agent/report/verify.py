"""Check the model's claims against the evidence it was given. No LLM involved.

Why: in Milestones 13 and 14 the model cited "line 18" and "line 14" for code
that is on lines 23 and 11. A diff doesn't contain those numbers; the model
made them up, and a prompt rule against it didn't stop it. So claims are
checked in code:

  1. Each fact must come with a quote, and the quote must appear in the input
     (alert, evidence or knowledge), ignoring case, whitespace and markdown.
  2. Specific details in a statement (line numbers, commit SHAs, ports and
     other numbers, file paths) must appear somewhere in the input. A quote
     can be real while the statement around it adds an invented detail.

What it does not check: whether the reasoning is right. A verified fact is
"this was really in the evidence", not "this explains the incident".
"""

import re

from schemas import Fact

MIN_QUOTE_CHARS = 8

LINE_NUMBER = re.compile(r"\blines?\s+(\d+)", re.IGNORECASE)
SHA = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
# 3+ digits standing alone: not inside a word/SHA, a time, a date or a decimal.
NUMBER = re.compile(r"(?<![\w.:\-/])\d{3,}(?![\w:\-/%]|\.\d)")
PATH = re.compile(r"\b[\w.-]+/[\w./-]*[\w-]\.\w+\b")  # e.g. demo-app/app/main.py


def normalize(text: str) -> str:
    """Lowercase, unescape JSON (\\" quotes, \\n and \\t), drop markdown backticks, collapse whitespace.

    The model sometimes writes a multi-line quote with literal "\\n" sequences
    (live run INC-20261014-440715: a correctly copied table row was rejected
    before \\n was handled).
    """
    text = text.replace('\\"', '"').replace("\\n", "\n").replace("\\t", " ").replace("`", "").lower()
    # Diff markers at the start of lines: a quote spanning several "+" lines is
    # copied without them (live run INC-20261014-fa803e). Applied to both sides.
    text = re.sub(r"(?m)^\s*[+-](?![+-])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_quote(quote: str) -> str:
    """normalize(), plus stray JSON punctuation trimmed from the edges: the model
    once returned a correct diff line followed by '},{'."""
    return normalize(quote).strip(" {}[],;")


def details(statement: str) -> list[str]:
    """The specific, checkable details a statement contains."""
    found = [f"line {n}" for n in LINE_NUMBER.findall(statement)]
    found += SHA.findall(statement.lower())
    found += PATH.findall(statement)
    found += NUMBER.findall(statement)
    return list(dict.fromkeys(found))  # unique, in order


def detail_issues(statement: str, corpus: str) -> list[str]:
    issues = []
    for detail in details(statement):
        if detail.startswith("line "):
            ok = re.search(rf"\blines?\s+{detail.split()[1]}\b", corpus) is not None
        else:
            ok = normalize(detail) in corpus
        if not ok:
            issues.append(f"'{detail}' does not appear in the evidence")
    return issues


def check_fact(fact: Fact, corpus: str) -> list[str]:
    """Problems with one fact; empty list = verified. `corpus` must be normalize()d."""
    issues = []
    quote = normalize_quote(fact.quote)
    if len(quote) < MIN_QUOTE_CHARS:
        issues.append("no quote from the evidence")
    elif quote not in corpus:
        issues.append("the quote does not appear in the evidence")
    return issues + detail_issues(fact.statement, corpus)
