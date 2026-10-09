"""Structured layout evidence for a PRD imported without its own headings.

The Agent decides structure only. It is never asked to return document text, so
the imported wording cannot be rewritten, hallucinated or truncated by this
call — the service re-splits the original paragraphs around the chosen lines.
"""

from pydantic import BaseModel, ConfigDict, Field


class ImportedHeadingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 1-based position in the candidate list supplied in the prompt.
    n: int = Field(ge=1, le=4000)
    level: int = Field(ge=1, le=3)
    # Empty keeps the source line exactly as written.
    title: str = Field(default="", max_length=60)


class ImportedHeadingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=300)
    headings: list[ImportedHeadingChoice] = Field(default_factory=list, max_length=600)
