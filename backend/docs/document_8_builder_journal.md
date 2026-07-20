# Document 8: Builder Journal

## What I built

A tool-using productivity agent (FastAPI + LangGraph + Gemini/GitHub/OpenRouter) with 12 tools across task, note, planning, and reporting — not a chatbot. Explicit pipeline: Intent Analysis → Tool Selection → Validation → Approval Gate → Tool Execution → Response Generation → Execution Logging. Human-in-the-loop approval for sensitive actions (update/complete task, bulk task creation), Pydantic-validated arguments everywhere, full audit logging with secret redaction. On top of that: a full evaluation harness (`run_evaluation.py` + `compute_metrics.py`) that drives the live API end-to-end — including resolving approval prompts — and a written security review.

## Hardest technical problem

Getting the approval pause/resume cycle to behave correctly *and* to be evaluable automatically. The graph pauses mid-run at `awaiting_approval`, returns to the client, and later resumes from a checkpoint once a human decision arrives — with `tool_calls`/`tool_results` accumulated via an `operator.add` reducer across both HTTP round-trips.

## How I solved it

Built the eval harness to drive the real `/api/chat` → `/api/approvals/{id}` cycle instead of mocking it, so it exercises the actual pause/resume path. That surfaced the reducer behavior directly: both the initial `awaiting_approval` response and the resume response legitimately return the *full* accumulated tool list, not just the delta. Fixed by deduping on `tool_call_id` (a stable UUID per call) rather than naively concatenating both responses.

## Tool-calling errors observed

- Harness double-counted `complete_task` as two calls instead of one, before the dedupe fix — a false negative, not an agent bug.
- `complete_task` on a title-only reference (`'Finish Q3 report'`) resolved to a literal `task_id` equal to the title text, which then failed with a clean `TaskNotFoundError` — correct failure handling, but revealed the model isn't doing real ID resolution when no prior `list_tasks` result is in context.
- One LLM call chain (`generate_json` → `json.loads`) failed to parse cleanly under load; the retry/backoff in `llm_service.py` absorbed it without surfacing to the user.

## Surprising agent behavior

Given a prompt referencing an implausible task ID (`'nonexistent-id-12345'`), GitHub Models' Llama-3.3-70B declined to call `complete_task` at all — it answered directly instead of attempting the tool and letting the DB report "not found." Arguably *more* correct than the test assumed, but it broke a test case built around the assumption that the agent always attempts the call. Different providers, different judgment calls on the same prompt.

## What failed during testing

The full 30-test run never finished: Gemini's free-tier quota hit `limit: 0` mid-run (confirmed in `agent_runs.log`), forcing a pivot to a 5-test smoke sample on GitHub Models via the per-request provider override. Also found — not from a test case, but from evaluating the code itself — that `generate_weekly_report`, `generate_work_plan`, `extract_meeting_actions`, and `draft_follow_up_email` don't honor that per-request override internally; they always call the *default* provider, so switching providers mid-evaluation only partially works.

## What I would redesign

1. **Route the direct REST CRUD endpoints through the same approval/audit path as the agent tools.** Right now `DELETE /api/tasks/{id}` and friends bypass approval and logging entirely — the single biggest gap found while writing the security review.
2. **Thread the provider/model override into every `get_llm_service()` call**, not just the three in `agent/nodes.py` — right now switching providers is only half-effective.
3. **Re-validate `edited_arguments` through the dedicated `validation_node`** instead of relying solely on each tool's internal defensive check.
4. **Split "agent should refuse without calling a tool" from "agent should call the tool and get a clean failure"** into two distinct edge-case categories in the eval dataset — FE-01 conflated them.

## Lessons learned about reliability

- **Graceful degradation earns its keep.** Every LLM-backed tool (`generate_work_plan`, `generate_weekly_report`) has a deterministic fallback path, and it's why the system never hard-failed even when Gemini's quota ran out mid-report-generation — it just quietly used the templated summary.
- **Defense-in-depth catches what upstream skips.** Tools re-validating their own arguments is what kept the edited-approval validation gap (found in the security review) from being a real bypass.
- **Small samples lie convincingly.** A 5-test run showed 50% approval compliance — driven entirely by one ambiguous case, not an actual bypass. Percentages from n=5 are a smoke test, not a result.
- **A harness bug looks identical to an agent bug until you check the raw data.** The AP-01 "duplicate call" would have been reported as an agent defect if I hadn't pulled `actual_tool_results` and traced it back to how the state reducer accumulates across HTTP responses.

## Goals for Week 4

- Run the full 30-test dataset (not just 5) across all three providers, so per-category pass rates are statistically meaningful.
- Close the REST-route approval/audit gap (redesign item #1) — highest-priority fix from the security review.
- Add basic rate limiting and a shared-secret auth check ahead of any non-localhost deployment.
- Thread provider/model overrides through the remaining four tool-level LLM calls.
- Re-run FE-01 and FE-03 as two variants each (obviously-fake vs. plausible-but-nonexistent reference) to separate "refused correctly" from "attempted and failed cleanly."
