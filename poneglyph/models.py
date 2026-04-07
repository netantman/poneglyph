"""Pydantic schemas for request validation and response serialization."""

from pydantic import BaseModel, Field


# ---------- Topics ----------

class TopicCreate(BaseModel):
    """Form data for creating a new topic."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    keywords: str = ""           # comma-separated, parsed into JSON list
    priority_keywords: str = ""  # comma-separated
    problem_statements: str = "" # newline-separated
    sources: list[str] = Field(default_factory=lambda: ["arxiv"])
    pdf_policy: str = "link_only"


class TopicUpdate(BaseModel):
    """Form data for updating a topic."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    keywords: str = ""
    priority_keywords: str = ""
    problem_statements: str = ""
    sources: list[str] = Field(default_factory=lambda: ["arxiv"])
    pdf_policy: str = "link_only"
    is_active: bool = True


# ---------- Helpers ----------

def parse_comma_list(raw: str) -> list[str]:
    """Split a comma-separated string into a cleaned list."""
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_newline_list(raw: str) -> list[str]:
    """Split a newline-separated string into a cleaned list."""
    return [s.strip() for s in raw.splitlines() if s.strip()]
