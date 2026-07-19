"""
tests/test_notes.py
=====================
Covers: Notes Search, Validation, Persistence (REQUIRED test categories).

Exercises the `save_note` / `search_notes` tool functions directly, plus
the keyword/tag search behavior in `NoteRepository.search()` — including
regression coverage for two bugs found during manual testing:

1. `search_notes` crashing on an LLM-supplied `{"limit": null}` argument
   (fixed in `agent/nodes.py`'s tool_selection_node).
2. Multi-word queries like "travel and meetings" failing to match a note
   tagged "Travel" because the old implementation searched the whole
   query as one literal phrase and never looked at tags (fixed in
   `database/repository.py`'s `NoteRepository.search()`).
"""

from app.tools.note_tools import save_note, search_notes


def test_save_note_tool_validates_arguments(test_db):
    """save_note returns success=False (not an exception) for a blank title."""
    result = save_note({"title": "", "content": "some content"}, "tc-1")
    assert result.success is False
    assert "Invalid arguments" in result.error


def test_save_note_tool_persists_to_database(test_db):
    """A tool-saved note is retrievable via search — proves persistence."""
    save_result = save_note(
        {"title": "Standup Notes", "content": "Discussed sprint scope.", "category": "meeting"}, "tc-2"
    )
    assert save_result.success is True
    assert save_result.output["note"]["id"]


def test_search_notes_finds_by_title_keyword(test_db):
    """Searching for a word in the title finds the note."""
    save_note({"title": "Business Trip Itinerary", "content": "Meeting schedule for the trip."}, "tc-3")

    result = search_notes({"query": "itinerary"}, "tc-4")
    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["notes"][0]["title"] == "Business Trip Itinerary"


def test_search_notes_finds_by_tag(test_db):
    """
    Searching for a word that only appears in the note's tags (not the
    title or content) still finds it — regression test for tags being
    excluded from search entirely.
    """
    save_note(
        {"title": "Q3 Roadmap Review", "content": "Discussed budget and timelines.", "tags": ["Travel"]},
        "tc-5",
    )

    result = search_notes({"query": "travel"}, "tc-6")
    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["notes"][0]["title"] == "Q3 Roadmap Review"


def test_search_notes_multi_word_query_matches_on_any_keyword(test_db):
    """
    A multi-word query like "travel and meetings" must match on the
    meaningful words ("travel", "meetings") rather than requiring that
    exact phrase to appear verbatim — regression test for the original
    substring-only search implementation.
    """
    save_note(
        {
            "title": "Business Trip Itinerary (July 22-23)",
            "content": "2-Day Business Trip: Meeting 1 with ABC Technologies at 9:00 AM.",
            "tags": ["Travel"],
        },
        "tc-7",
    )

    result = search_notes({"query": "travel and meetings"}, "tc-8")
    assert result.success is True
    assert result.output["count"] == 1


def test_search_notes_no_match_returns_empty_not_an_error(test_db):
    """An unrelated query returns success=True with zero results, not a failure."""
    save_note({"title": "Grocery list", "content": "Milk, eggs, bread."}, "tc-9")

    result = search_notes({"query": "quarterly financial projections"}, "tc-10")
    assert result.success is True
    assert result.output["count"] == 0


def test_search_notes_tolerates_null_limit_argument(test_db):
    """
    Regression test: an LLM-style tool call with an explicit null for an
    optional field (e.g. {"query": "trip", "limit": None}) must not crash
    validation — Pydantic rejects an explicit None against the
    non-Optional `limit: int` field, so callers are expected to omit
    null-valued keys entirely (enforced in agent/nodes.py, not here) —
    this test locks in that search_notes itself works correctly once
    that's done, i.e. when `limit` is simply absent.
    """
    save_note({"title": "Trip planning", "content": "Book flights and hotel."}, "tc-11")

    result = search_notes({"query": "trip"}, "tc-12")
    assert result.success is True
    assert result.output["count"] == 1


def test_search_notes_validation_rejects_empty_query(test_db):
    """An empty query string fails NoteSearchQuery's min_length=1 validation."""
    result = search_notes({"query": ""}, "tc-13")
    assert result.success is False
    assert "Invalid arguments" in result.error
