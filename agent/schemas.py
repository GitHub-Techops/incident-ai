"""What the model must return. The final IncidentReport (report/schema.py) is
assembled from this plus what code knows for certain (alert, rollback rules,
approval) and the claim verification."""

from typing import Literal

from pydantic import BaseModel, Field

FactSource = Literal["alert", "kubernetes", "metrics", "deployment", "knowledge"]


class Fact(BaseModel):
    statement: str = Field(description="The observation, in one sentence")
    source: FactSource = Field(description="Which part of the input shows it")
    quote: str = Field(description="The exact text copied from the evidence that shows this fact, "
                                   "character for character (a log line, a fact line, a diff line, a metric line)")


class RootCauseAnalysis(BaseModel):
    summary: str = Field(description="One or two sentences: what is broken and the impact")
    facts: list[Fact] = Field(description="Observations taken directly from the evidence, each with its quote")
    inferences: list[str] = Field(description="Conclusions drawn from the facts, not directly observed")
    root_cause: str = Field(description="The most likely root cause, as specific as the evidence allows")
    confidence: float = Field(ge=0, le=1, description="Between 0 and 1: how directly the evidence shows the root cause")
    recommended_actions: list[str]
    rollback_recommended: bool
    rollback_reason: str
    # Required, not defaulted: optional fields are left out of the schema's
    # "required" list, and the model then skipped this one (Milestone 14 run).
    knowledge_sources: list[str] = Field(
        description="Source files of the knowledge documents you relied on, e.g. runbooks/database-errors.md; "
                    "empty if none were used")
