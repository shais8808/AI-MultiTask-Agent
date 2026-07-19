"""
schemas/note.py
================

Why this file exists
---------------------
Mirrors `schemas/task.py` but for the Note entity — validated boundary
objects for `save_note` / `search_notes` tools and the `/api/notes` routes.

How it interacts with the rest of the system
-----------------------------------------------
- `database/repository.py` -> `NoteRepository` converts ORM `Note` rows
  into `NoteRead` before returning them.
- `tools/note_tools.py` uses `NoteCreate` as input and `NoteRead` /
  `NoteSearchResult` as output.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class NoteCreate(BaseModel):
    """Input schema for the `save_note` tool / `POST /api/notes`."""

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=20000)
    category: str = Field(default="general", max_length=100)
    tags: List[str] = Field(default_factory=list)

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank or whitespace-only")
        return v.strip()


class NoteSearchQuery(BaseModel):
    """Input schema for the `search_notes` tool."""

    query: str = Field(..., min_length=1, max_length=500)
    category: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class NoteRead(BaseModel):
    """Output schema representing a Note as returned to callers."""

    id: str
    title: str
    content: str
    category: str
    tags: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteSearchResult(BaseModel):
    """Output schema for `search_notes`."""

    notes: List[NoteRead]
    count: int
