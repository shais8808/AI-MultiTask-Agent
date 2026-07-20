# Personal Productivity and Task Execution Agent

A tool-using AI agent — not a chatbot — that manages tasks and notes through
genuine tool-calling, Pydantic-validated arguments, human-in-the-loop approval
for sensitive actions, persistent session memory, and full execution logging.

Built with **FastAPI + LangGraph + Gemini / GitHub Models / OpenRouter** on the
backend and **React (Vite) + Material UI** on the frontend.

---

## Table of contents

- [Problem statement](#problem-statement)
- [Key features](#key-features)
- [Architecture overview](#architecture-overview)
- [Tool catalogue](#tool-catalogue)
- [Technology stack](#technology-stack)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Running locally](#running-locally)
- [Running tests](#running-tests)
- [Example user requests](#example-user-requests)
- [Evaluation results](#evaluation-results)
- [Screenshots](#screenshots)
- [Demo video](#demo-video)
- [Deployment](#deployment)
- [Known limitations](#known-limitations)
- [Future roadmap](#future-roadmap)

---

## Problem statement

Generic chatbots can talk about tasks; they can't reliably act on them. This
project builds an agent that treats every user message as a decision problem —
does it need a tool at all, which one, with what validated arguments, does a
human need to approve it first, and did it actually succeed — rather than an
LLM guessing at plausible-sounding actions. The goal is an agent that is
**dependable and auditable**, not one that merely *appears* capable.

## Key features

- **Explicit decision pipeline** — every message passes through Intent
  Analysis → Tool Selection → Validation → Approval Gate → Tool Execution →
  Response Generation → Execution Logging (see [Architecture](#architecture-overview)).
- **12 tools** across tasks, notes, planning, and reporting (see
  [Tool catalogue](#tool-catalogue)).
- **Human-in-the-loop approval** for sensitive actions (updating/completing
  tasks, bulk task creation from meeting notes) — the graph pauses without
  losing state and resumes exactly where it left off once a decision is made.
- **Structured, validated tool arguments** — every tool call is checked
  against a Pydantic schema before it's proposed to a human or executed.
- **Session memory** — conversation history, the last task list shown, and
  stated preferences persist across turns, enabling references like "mark
  the second one complete."
- **Full execution logging** — every run records its prompt, model, tools
  called, arguments, results, approval status, timing, and outcome, with
  secrets always redacted before persistence.
- **Loop / runaway-cost protection** — bounded agent steps, retries, and
  per-call timeouts.
- **Multi-provider LLM support** — swap between Gemini, GitHub Models, or
  OpenRouter via one config value, with a per-request override from the UI.

## Architecture overview

```
User
  |
  v
Frontend (React)
  |
  v
Agent API (FastAPI)
  |
  v
Agent State (LangGraph, checkpointed per run)
  |
  +-------------------+
  |                    |
  v                    v
 LLM              Tool Registry
                        |
        +---------------+----------------+
        |               |                |
        v               v                v
   Task Tools      Note Tools      Planning / Report Tools
        |               |                |
        +---------------+----------------+
                        |
                        v
                    Database (SQLite)
                        |
                        v
                  Execution Logs
```

**Graph flow** (`backend/app/agent/graph.py`):

```
Intent Analysis -> Tool Selection -> Validation -> Approval Gate
-> Tool Execution -> Response Generation -> Execution Logging
```

- **Intent Analysis** decides whether a tool is even needed — small talk
  never triggers a database call or a tool-selection prompt.
- **Tool Selection** picks the right tool(s) and resolves references like
  "the second one" against the last task list shown to the user.
- **Validation** checks every tool's arguments against its Pydantic schema
  *before* anything sensitive is proposed to a human.
- **Approval Gate** pauses the graph — without losing any state — whenever a
  sensitive action is about to happen, and resumes exactly where it left off
  once a human approves, rejects, or edits it.
- **Execution Logging** writes a full audit row for every run.

Session memory (`services/memory_service.py`) and pending approvals
(`services/approval_service.py`) are process-local, in-memory stores —
see [Known limitations](#known-limitations).

## Tool catalogue

| Tool | Category | Sensitive (requires approval)? |
|---|---|---|
| `create_task` | Task | No |
| `list_tasks` | Task | No |
| `update_task` | Task | **Yes** |
| `complete_task` | Task | **Yes** |
| `save_note` | Note | No |
| `search_notes` | Note | No |
| `generate_work_plan` | Planning | No |
| `detect_overdue_tasks` | Planning | No |
| `extract_meeting_actions` | Report | No (proposal only) |
| `convert_meeting_notes_to_tasks` | Report | **Yes** |
| `generate_weekly_report` | Report | No |
| `draft_follow_up_email` | Report | No (draft only, never sent) |

## Technology stack

**Backend**
- Python 3.12, FastAPI, Uvicorn
- LangGraph (stateful agent graph + checkpointing)
- LangChain (`langchain-google-genai`, `langchain-openai`)
- Pydantic / Pydantic Settings
- SQLAlchemy + SQLite
- Pytest

**Frontend**
- React 18 + Vite
- Material UI (MUI)
- Axios

**LLM providers** (config-switchable, no code change): Gemini (native), GitHub
Models (Llama/DeepSeek/etc. via OpenAI-compatible endpoint), OpenRouter.

## Installation

Prerequisites: Python 3.11+, Node.js 18+, an API key for at least one
supported LLM provider.

```bash
git clone <your-repo-url>
cd productivity-agent
```

### Backend

```bash
cd backend
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# edit .env and set the API key for your chosen LLM_PROVIDER
```

### Frontend

```bash
cd frontend
npm install
```

## Environment variables

Set these in `backend/.env` (see `backend/.env.example` for the full
template):

| Variable | Description |
|---|---|
| `LLM_PROVIDER` | `gemini` \| `github` \| `openrouter` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Required if `LLM_PROVIDER=gemini` |
| `GITHUB_TOKEN` / `GITHUB_MODEL` | Required if `LLM_PROVIDER=github` |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | Required if `LLM_PROVIDER=openrouter` |
| `DATABASE_URL` | SQLAlchemy database URL (defaults to local SQLite) |
| `MAX_AGENT_STEPS` | Loop-prevention cap on graph steps per run (default `8`) |
| `MAX_RETRIES` | Max retries per LLM call (default `2`) |
| `REQUEST_TIMEOUT_SECONDS` | Per-LLM-call timeout (default `30`) |
| `CORS_ORIGINS` | Comma-separated origins allowed to call the API |
| `API_HOST` / `API_PORT` | Uvicorn bind address/port |

**Never commit your real `.env` file.** Only `.env.example` (with placeholder
values) belongs in version control.

## Running locally

**Backend** (from `backend/`, with the virtualenv activated):

```bash
uvicorn app.main:app --reload
```
API at `http://localhost:8000` — interactive docs at `/docs`, health check
at `/health`.

**Frontend** (from `frontend/`, in a second terminal):

```bash
npm run dev
```
UI at `http://localhost:5173` — proxies `/api/*` and `/health` to the
backend automatically (see `vite.config.js`).

## Running tests

```bash
cd backend
python -m pytest
```

22 automated tests cover task CRUD, tool input validation, approval
pause/approve/resume/reject, meeting-note extraction, model/provider
selection, and step-limit / loop-prevention enforcement.

## Example user requests

```
Create a task to finish the quarterly report by Friday, high priority
Show me all high-priority tasks due this week
Mark the second one as complete
Generate a work plan for today based on my current tasks
Here are my meeting notes: ...  -> extract action items -> approve -> create tasks
Search my notes for the client proposal
Prepare a weekly productivity report
Find tasks that are overdue and recommend what I should work on first
```

## Evaluation results

Measured by running [`backend/run_evaluation.py`](backend/run_evaluation.py) against the live agent (`POST /api/chat` + `POST /api/approvals/{id}`, real LLM calls, no mocks). **Current numbers are from a 5-test smoke sample** (one test per category, GitHub Models provider — Gemini's free-tier quota was exhausted mid-run) and should be treated as directional, not final, until the full 30-test dataset is run:

| Metric | Target | Result (n=5) |
|---|---|---|
| Tool selection accuracy | ≥ 85% | 80.0% |
| Argument accuracy | ≥ 80% | 80.0% |
| Task completion rate | ≥ 80% | 80.0% |
| Approval compliance | 100% | 50.0%¹ |
| Invalid action rate | < 10% | 20.0%¹ |
| Average response time | — | 7,542 ms |
| Recovery rate | — | 100.0% |

¹ Both driven by a single ambiguous edge case, not an approval bypass — see the caveat in [Document 5](backend/docs/document_5_evaluation_metrics.md#why-approval-compliance-and-invalid-action-rate-look-worse-than-they-are-at-n5).

To reproduce or extend to the full 30 cases:
```bash
cd backend
python run_evaluation.py --llm-provider github --delay 2   # or omit --only to run all 30
python compute_metrics.py
```

**Full documentation:**
- [Document 4 — Evaluation Dataset](backend/docs/document_4_evaluation_dataset.md) (30 test cases, actual results for the 5-test run)
- [Document 5 — Evaluation Metrics](backend/docs/document_5_evaluation_metrics.md) (methodology + full breakdown)
- [Document 7 — Security Review](backend/docs/document_7_security_review.md)
- [Document 8 — Builder Journal](backend/docs/document_8_builder_journal.md)
- [Agent Design Document](backend/docs/agent_design.md)

## Screenshots

_Add screenshots of the chat interface, approval modal, task panel, notes
panel, and execution logs panel here._

## Demo video

_TBD — to be added later._

## Deployment

_Not yet deployed. A unified `Dockerfile` is included at the repo root for
single-container deployment (Render / Railway / Hugging Face Spaces) —
add the live URL here once deployed._

## Known limitations

- **Single-process, in-memory state**: session memory
  (`services/memory_service.py`), pending approvals
  (`services/approval_service.py`), and the LangGraph checkpointer are all
  process-local. A multi-worker or multi-replica deployment would need
  Redis-backed equivalents.
- **No multi-user support**: the system has no authentication or per-user
  data isolation — it's designed for a single user.
- **Notes search is keyword-based, not semantic**: `search_notes` matches
  on individual keywords across title/content/tags rather than semantic
  similarity.
- **No duplicate tool-call detection**: loop prevention is enforced via a
  hard step cap, but the graph does not separately detect/dedupe repeated
  identical tool calls within a run.
- **LLM-dependent free tiers rate-limit quickly**: Gemini's free tier caps
  at a small number of requests per day; expect fallback responses once
  exhausted (the agent degrades gracefully rather than crashing).

## Future roadmap

- Redis-backed session/approval state for multi-process deployment
- Semantic search over notes (embeddings + vector similarity)
- Multi-user auth and per-user data isolation
- Duplicate/repeated tool-call detection within a single run
- Per-tool execution timeout (currently only LLM calls are timeout-bounded)
- Click-to-expand note detail view in the frontend
- `create_reminder` and calendar-integration tools
