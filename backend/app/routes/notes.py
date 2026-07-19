"""
routes/notes.py
=================

Why this file exists
---------------------
Direct REST access to notes for the frontend's Notes Panel, mirroring the
task routes' rationale — this is the human-driven path, separate from the
agent's `save_note`/`search_notes` tools.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.repository import NoteNotFoundError, NoteRepository
from app.schemas.note import NoteCreate, NoteRead, NoteSearchResult

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[NoteRead])
async def list_notes(limit: int = 100, db: Session = Depends(get_db)) -> list[NoteRead]:
    repo = NoteRepository(db)
    notes = repo.list_all(limit=limit)
    db.commit()
    return notes


@router.get("/search", response_model=NoteSearchResult)
async def search_notes(query: str, category: str | None = None, limit: int = 20, db: Session = Depends(get_db)) -> NoteSearchResult:
    repo = NoteRepository(db)
    notes = repo.search(query=query, category=category, limit=limit)
    db.commit()
    return NoteSearchResult(notes=notes, count=len(notes))


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(note_id: str, db: Session = Depends(get_db)) -> NoteRead:
    repo = NoteRepository(db)
    try:
        note = repo.get(note_id)
        db.commit()
        return note
    except NoteNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", response_model=NoteRead, status_code=201)
async def create_note(data: NoteCreate, db: Session = Depends(get_db)) -> NoteRead:
    repo = NoteRepository(db)
    note = repo.create(data)
    db.commit()
    return note
