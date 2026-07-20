# Document 4: Agent Evaluation Dataset

**Run date:** 2026-07-19 · **LLM provider:** GitHub Models (`meta/Llama-3.3-70B-Instruct`), selected via the per-request `llm_provider` override in `POST /api/chat` because the configured Gemini key had exhausted its free-tier daily quota (`limit: 0`, confirmed in `backend/logs/agent_runs.log`).

**Scope of this run:** the full dataset defines **30** test cases (5 Direct Response / 8 Single Tool / 8 Multi Tool / 5 Approval / 4 Failure-Edge-Case) in `backend/agent_evaluation_dataset.json`. This document reports **actual, measured results for a 5-test smoke sample** — one test per category — executed against the live backend via `backend/run_evaluation.py`. The remaining 25 are specified (expected tool/arguments/outcome) but not yet executed; re-run `python run_evaluation.py --llm-provider github` with no `--only` filter to complete the full set and extend this table.

Every test ran through the real HTTP API (`POST /api/chat`, and `POST /api/approvals/{id}` where relevant) against a live LangGraph agent — not mocked.

---

| Test ID | User Prompt | Expected Tool | Expected Arguments | Expected Outcome | Actual Outcome | Pass/Fail |
|---|---|---|---|---|---|---|
| DR-01 | "Hi, what can you help me with?" | none | — | `status="completed"`, no tool call | `status="completed"`, `tool_calls=[]`. Response time 6.4s | **PASS** |
| ST-01 | "Create a task to finish the quarterly report by July 25th, high priority" | `create_task` | `{title:"*", priority:"high"}` | `status="completed"`, task created successfully | `create_task` called, `success=true`, task created. Response time 14.9s | **PASS** |
| MT-02 | "What's overdue, and can you also give me a weekly report?" | `detect_overdue_tasks` + `generate_weekly_report` | `{}` and `{}` | Both tools execute successfully in one run | Both tools called in order, both `success=true`. Response time 11.9s | **PASS** |
| AP-01 | "Mark the task 'Finish Q3 report' as complete." → approve | `complete_task` | `{task_id:"*"}` | `status="awaiting_approval"` first; after approval, tool executes | Correctly paused for approval, resumed correctly on approve. **However**, the underlying tool call itself returned `success=false`, `error="Task with id='Finish Q3 report' was not found."` — expected, since no task with that exact title had been created in this sample run (ST-01 created a differently-titled task). Response time 4.5s | **PASS** (tool-selection / approval-flow behavior correct; failure is a missing-seed-data artifact of running an isolated 5-test sample, not an agent defect) |
| FE-01 | "Mark task 'nonexistent-id-12345' as complete." | `complete_task` (expected to attempt the call and receive a not-found error from the DB layer) | `{task_id:"nonexistent-id-12345"}` | Tool executes, fails cleanly with a not-found error, agent explains gracefully | Agent did **not** call any tool at all — it responded directly without attempting `complete_task`. `tool_calls=[]`, `status="completed"`. Response time 3.1s | **FAIL** — see finding below |

## Notable finding: FE-01

The test was designed assuming the agent would always attempt `complete_task` and let the repository layer's `TaskNotFoundError` surface the failure. With Llama-3.3-70B (via GitHub Models) on this prompt, the agent instead recognized `'nonexistent-id-12345'` as an implausible/placeholder identifier and answered directly, without emitting a tool call. This is arguably reasonable behavior (it avoided a pointless DB round-trip) but it means the test's original expected-tool assumption doesn't hold for this model/provider — worth deciding explicitly whether "refuses without calling the tool" should also count as a pass for this kind of prompt, or whether the dataset should be split into two variants (a clearly-fake ID vs. a well-formed-but-nonexistent ID) to test both paths. Not re-run against Gemini to compare, since that provider's quota was exhausted for the day.

## How to reproduce / extend this run

```powershell
cd productivity-agent\backend
venv\Scripts\Activate.ps1
python run_evaluation.py --llm-provider github --delay 2
python compute_metrics.py
```
Omit `--only` to run all 30. `evaluation_results.csv` merges new results into existing ones by Test ID, so partial runs can be resumed incrementally.
