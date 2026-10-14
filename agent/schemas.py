"""What the model must return. Milestone 16 grows this into the full IncidentReport."""

from pydantic import BaseModel, Field


class RootCauseAnalysis(BaseModel):
    summary: str = Field(description="One or two sentences: what is broken and the impact")
    facts: list[str] = Field(description="Observations taken directly from the evidence, each citing its source")
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
