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


class TopicUpdate(BaseModel):
    """Form data for updating a topic."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    keywords: str = ""
    priority_keywords: str = ""
    problem_statements: str = ""
    is_active: bool = True


# ---------- Helpers ----------

def _dedup_preserve_order(items: list[str]) -> list[str]:
    """Deduplicate a list case-insensitively, preserving order and original case."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def parse_comma_list(raw: str) -> list[str]:
    """Split a comma-separated string into a cleaned, deduplicated list."""
    return _dedup_preserve_order([s.strip() for s in raw.split(",") if s.strip()])


def parse_newline_list(raw: str) -> list[str]:
    """Split a newline-separated string into a cleaned list."""
    return [s.strip() for s in raw.splitlines() if s.strip()]
