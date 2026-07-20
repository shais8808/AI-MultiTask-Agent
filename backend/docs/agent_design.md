# Agent Design Document

## Personal Productivity and Task Execution Agent

---

## 1. Problem Statement

Generic chatbots can talk *about* tasks — summarize them, discuss priorities,
answer questions — but they cannot reliably *act* on them. Asking a raw LLM
to "just call the right function" produces a system that is impossible to
trust: it may invent tool calls that don't exist, pass malformed arguments,
silently skip a destructive confirmation step, or loop indefinitely when
confused.

This project treats every user message as a **decision problem**, not a
free-form generation problem. Before anything happens, the system explicitly
decides: does this message need a tool at all? Which tool? With what
validated arguments? Does a human need to approve this before it runs? Did
it actually succeed? The goal is an agent that is **dependable and
auditable** — every action can be traced back to a specific tool call, a
specific set of validated arguments, and (for sensitive actions) a specific
human decision — rather than one that merely *appears* capable.

## 2. Target Users

A single professional managing their own day-to-day work: tasks, notes,
meeting follow-ups, and weekly planning. The system is explicitly scoped as
**single-user** — there is no authentication, no multi-tenancy, and no
per-user data isolation. This is a deliberate scope boundary, not an
oversight (see [Security Considerations](#10-security-considerations)).

## 3. Use Cases

The agent supports two categories of interaction:

**Direct task/note management**
- Create, list, update, and complete tasks
- Save and search notes
- Detect overdue tasks

**Reasoning-driven workflows**
- Generate a prioritized work plan from current pending/in-progress tasks
- Extract action items from raw meeting notes, then (on approval) convert
  them into real tasks
- Generate a weekly productivity report — completion stats, a narrative
  summary, and a separate list of recommended priorities for next week
- Draft a follow-up email based on given context (draft only — the system
  has no ability to send email)

Every use case above maps to exactly one registered tool (see
[Tool Catalogue](#6-tool-catalogue)); the agent never has a way to take an
action that isn't backed by a concrete, validated tool.

## 4. Agent Responsibilities

The agent is responsible for, in order, on every user message:

1. **Interpret intent** — classify the message as `chat_only`,
   `tool_required`, or `clarification_needed` before doing anything else.
2. **Decide whether a tool is needed** — small talk and conceptual
   questions ("what's the difference between high and critical priority?")
   must never trigger a database call or a tool-selection LLM call.
3. **Select the correct tool(s)** and construct their arguments, resolving
   relative references ("the second one") against the task list last
   shown to the user.
4. **Validate** every constructed argument set against that tool's Pydantic
   schema *before* proposing it to a human or executing it.
5. **Request human approval** for any sensitive (state-changing) action
   before it runs.
6. **Execute** the tool(s) and capture the result — success or failure —
   without ever letting an unhandled exception escape.
7. **Generate a natural-language response** summarizing what happened.
8. **Log the complete run** — prompt, model, tools, arguments, results,
   approval status, timing, and outcome — exactly once per run.

## 5. Agent Boundaries

What the agent must **never** do:

- Call a tool "just in case" when the message doesn't require one.
- Guess at an ambiguous reference (e.g. "mark it done" with no prior
  context) — it must ask for clarification instead of picking arbitrarily.
- Execute `update_task`, `complete_task`, or
  `convert_meeting_notes_to_tasks` without an explicit, recorded human
  approval decision.
- Run more than `MAX_AGENT_STEPS` (default 8) node-visits in a single turn
  — the run is forcibly stopped with a clear error rather than looping.
- Actually send an email, message, or notification anywhere — no tool in
  the registry has that capability; `draft_follow_up_email` only returns
  text.
- Write a secret (API key, token) into a persisted log or an LLM prompt.
- Assume knowledge of, or act on behalf of, any user other than the one in
  the current session — there is no cross-session or cross-user data
  access.

## 6. Tool Catalogue

12 tools across four domains (full per-tool schemas in
`tool_specification.md`):

| Tool | Domain | Sensitive? |
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

All tools share one contract: accept a raw arguments dict, validate it
against a Pydantic schema, and return a `ToolResult` (`success`, `output`,
`error`) — **never** a raised exception. `router.py`'s `dispatch_tool`
catches anything a tool implementation fails to catch itself, so a bug in
one tool can never crash the graph.

## 7. State Model

The agent threads a single `AgentState` (`agent/state.py`, a LangGraph
`TypedDict`) through every node of one run:

- **Identity**: `session_id`, `run_id`
- **Input**: `conversation` (accumulates via `operator.add`), `user_message`
- **Session memory carried into the run**: `referenced_tasks` (last task
  list shown, for ordinal resolution), `preferences`, `llm_provider` /
  `llm_model` overrides
- **Reasoning**: `intent`, `clarification_question`
- **Tool pipeline**: `tool_calls` / `tool_results` (both accumulate)
- **Approval**: `pending_approval`, `approval_decision`
- **Control flow**: `step_count`, `max_steps_exceeded`, `retry_count`
- **Output**: `final_response`, `status`, `error`

This is deliberately distinct from two other, longer-lived state stores:

- **Session memory** (`services/memory_service.py`) — conversation history,
  referenced tasks, and preferences that persist *across* multiple runs
  within one chat session (process-local, in-memory).
- **Execution log** (`database` `ExecutionLog` table) — the permanent,
  per-run audit record, written once a run completes.

**Why the reducer choice matters (a real bug this design caught):**
`tool_calls`/`tool_results` use LangGraph's additive `operator.add` reducer,
so a node's returned partial state gets *appended* to, not replace, the
existing list. When resuming a paused run after an approval decision,
passing the *full* checkpointed state back into `graph.invoke()` would
re-append the already-recorded `tool_calls` onto themselves — duplicating
every previously-selected call. `resume_run()` (`agent/graph.py`) instead
passes only the delta (`{"approval_decision": ..., "status": "in_progress"}`)
on resume, letting the additive reducer merge it onto the existing
checkpoint exactly once. This was caught by integration testing, not code
review — see `tests/test_agent_approval.py`.

## 8. Approval Model

Three tools are gated behind human approval: `update_task`, `complete_task`,
`convert_meeting_notes_to_tasks`. Approval is enforced structurally, not by
convention:

1. `tool_selection_node` tags each `ToolCall` with `requires_approval` by
   looking it up in the tool registry (`router.tool_requires_approval`) —
   a new sensitive tool is gated by setting one flag in its registry
   entry, so there is no way to forget to add a check somewhere else.
2. `approval_gate_node` checks for any sensitive call with no recorded
   decision. If found, it builds an `ApprovalRequest`, sets
   `status="awaiting_approval"`, and the graph reaches `END` — the run
   *pauses*, preserving all state (including already-selected,
   already-validated tool calls) via LangGraph's `MemorySaver`
   checkpointer, keyed by `run_id`.
3. The frontend renders the Approval Modal (tool name, arguments, a
   human-readable action summary) and the human clicks **Approve**,
   **Reject**, or **Edit** (supplying replacement arguments).
4. `POST /api/approvals/{approval_id}` resolves the decision and calls
   `resume_run()`, which re-enters the graph at `approval_gate_node`
   directly — `intent_node`, `tool_selection_node`, and `validation_node`
   are **not** re-run, so approval doesn't burn extra LLM calls or steps
   against the run's step limit.
5. On **Reject**, `_after_approval_gate()` in `agent/graph.py` routes
   straight to `response_generation_node`, bypassing `tool_execution_node`
   entirely — the tool call structurally cannot execute.

**A second bug this model caught:** an earlier version of the rejection
handler only checked `status in ("error", "awaiting_approval")` to decide
whether to skip execution; on rejection the status was `"completed"`
(the run genuinely finished, it just didn't do the sensitive thing), which
matched neither, so the router fell through and executed the "rejected"
tool call anyway. The fix routes on the explicit `approval_decision.decision`
value instead of inferring from an overloaded `status` field. Also caught by
`tests/test_agent_approval.py`, not code review.

## 9. Error Handling Strategy

Errors are handled in layers, closest to the source first:

- **Argument validation**: every tool validates its own arguments against a
  Pydantic schema (`TaskCreate`, `NoteSearchQuery`, etc.) and returns
  `ToolResult(success=False, ...)` on failure — never raises.
- **Pre-execution re-validation**: `validation_node` re-checks every
  selected tool call's arguments *before* the approval gate, catching
  malformed LLM-generated arguments early with a clear error instead of
  failing deep inside a tool after a human has already approved it.
- **Null-argument normalization**: `tool_selection_node` strips any
  argument the LLM sets to an explicit `null` before validation — every
  schema in this project treats "field omitted" and "field is null"
  identically, but Pydantic rejects an explicit `None` against a
  non-Optional field type instead of applying its default.
- **LLM failures**: `services/llm_service.py` normalizes every failure mode
  (missing key, network error, malformed response) into an
  `LLMServiceError` subtype, with bounded retries (`MAX_RETRIES`) and a
  hard per-call timeout (`REQUEST_TIMEOUT_SECONDS`).
- **Tool execution timeout**: `router.dispatch_tool_with_timeout` runs each
  tool call in a worker thread bounded by `REQUEST_TIMEOUT_SECONDS`, so a
  hung tool call can't hang the whole run.
- **Tool dispatch safety net**: `dispatch_tool` catches any exception a
  tool implementation fails to catch itself and converts it into a failed
  `ToolResult`.
- **Graceful degradation, not hard failure**: `generate_work_plan` and
  `generate_weekly_report` fall back to a deterministic priority/due-date
  sort if the LLM is unavailable; `response_generation_node` falls back to
  a templated summary of raw tool results if response generation itself
  fails.
- **Loop prevention**: every node increments a shared step counter first;
  once `step_count > MAX_AGENT_STEPS`, the run is forced to `status="error"`
  with a clear "too many steps" message instead of continuing.
- **Duplicate tool-call detection**: `tool_selection_node` deduplicates
  identical `(tool_name, arguments)` pairs before they reach validation,
  approval, or execution.
- **API boundary**: `main.py`'s global exception handler catches anything
  that reaches it and returns a generic `{"error": "internal_server_error"}`
  — the real exception and stack trace are logged server-side only, never
  returned to the client.

## 10. Security Considerations

- **Secret handling**: API keys are read once from environment
  configuration and never logged. `logging/run_logger.py`'s `_scrub()`
  recursively redacts any dict key matching `api_key`, `password`,
  `token`, `secret`, etc. before a run's `arguments`/`results` are written
  to the database — enforced at the single place all execution data is
  persisted, not scattered across every tool.
- **Input validation**: every tool argument is Pydantic-validated; all
  free-text fields have explicit length caps (task titles ≤255 chars,
  note content ≤20,000 chars) to bound both storage and LLM prompt size.
- **No raw SQL**: all database access goes through SQLAlchemy's ORM query
  builder via the repository layer — no string-concatenated SQL exists
  anywhere in the codebase.
- **Approval gate as the primary safety control**: it exists specifically
  to catch an LLM hallucinating a destructive or unintended action before
  it reaches the database, not merely as a UX nicety.
- **Error message hygiene**: unhandled exceptions never return a stack
  trace or internal path to the client (see
  [Error Handling Strategy](#9-error-handling-strategy)).
- **Known boundary — single-process state**: session memory
  (`memory_service.py`), pending approvals (`approval_service.py`), and
  the LangGraph checkpointer are all in-memory, process-local singletons.
  This means the deployment **must** run as a single instance/worker — a
  multi-replica deployment would give each replica its own inconsistent
  view of pending approvals and session state. This is documented, not
  hidden, and is the natural next architectural step if the project moved
  toward multi-user production use (alongside adding authentication and
  per-user data isolation, neither of which exists today by design).
