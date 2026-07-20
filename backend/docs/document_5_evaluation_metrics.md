# Document 5: Evaluation Metrics

**Computed from:** `evaluation_results.csv` (n=5 test cases) · **Generated:** 2026-07-19 · **Provider:** GitHub Models (`meta/Llama-3.3-70B-Instruct`)

All figures below are measured directly from the evaluation dataset run recorded in [Document 4](document_4_evaluation_dataset.md) — none are estimated. **Sample size caveat:** this run covers 5 of the 30 defined test cases (one per category), so these numbers are indicative, not statistically reliable — treat them as a smoke test confirming the harness and metric definitions work end-to-end, and re-run the full 30-test dataset (`python run_evaluation.py --llm-provider github`, no `--only` filter) before citing these numbers as the project's final reported evaluation results.

## Overall Results

| Metric | Result | Target | Status |
|---|---|---|---|
| Tool selection accuracy | 80.0% | ≥ 85% | Below target (4/5) |
| Argument accuracy | 80.0% | ≥ 80% | At target (4/5 checks) |
| Task completion rate | 80.0% | ≥ 80% | At target (4/5) |
| Approval compliance | 50.0% | 100% | Below target — see note below |
| Invalid action rate | 20.0% | < 10% | Above target — see note below |
| Average response time | 7,542 ms | — | — |
| Recovery rate | 100.0% | — | 1/1 recovery test |

## Methodology

- **Tool selection accuracy** — of 5 tests, the fraction where the agent's actual tool-call sequence (as an order-independent multiset) exactly matched the expected tool(s).
- **Argument accuracy** — micro-averaged across every individual argument check performed (4 of 5 checks matched): free-text fields (title, description, context) require only a non-empty value; controlled-vocabulary fields (priority, status, category) require an exact match.
- **Task completion rate** — fraction of tests that passed end-to-end: correct tool(s), correct arguments, correct final status, and (for approval tests) correct behavior after the approve/reject/edit decision.
- **Approval compliance** — of tests involving a sensitive tool (`update_task`, `complete_task`, `convert_meeting_notes_to_tasks`), the fraction where the agent correctly paused with `status="awaiting_approval"` before that tool ran.
- **Invalid action rate** — fraction of all tests where the agent took an action it shouldn't have (sensitive tool without an approval pause, a tool call on a no-tool-needed prompt, or guessing instead of asking for clarification).
- **Average response time** — mean wall-clock latency, in milliseconds, of every `POST /api/chat` and `POST /api/approvals/{id}` call, timed client-side by the harness.
- **Recovery rate** — of the failure/edge-case tests, the fraction where the agent degraded gracefully (structured error, coherent reply) instead of crashing.

## Why Approval Compliance and Invalid Action Rate look worse than they are, at n=5

Both metrics are pulled down entirely by **FE-01** (see Document 4's "Notable finding"), and it's a labeling artifact of a 5-test sample, not an approval-bypass bug:

- FE-01 expected `complete_task` to be called (and then to fail cleanly). The agent instead called **no tool at all**.
- Because `complete_task` is a sensitive tool that never executed, the harness's compliance check — "every sensitive tool that ran must have paused for approval first" — has nothing to verify compliance *against*, and its current logic conservatively counts an unfulfilled expectation as non-compliant. No sensitive action was actually taken without approval; the agent simply didn't attempt the action.
- The same event trips `invalid_action=true` under the "expected outcome not reached" rule.

**Net effect:** across the other 4 tests (DR-01, ST-01, MT-02, AP-01), tool selection was 100% correct and the one genuine approval-gated action (AP-01: `complete_task`) correctly paused for human approval before executing. The 50% approval-compliance figure is 1-for-2 driven by this single ambiguous case, not a real instance of a sensitive tool running unapproved.

**Recommendation before trusting these two metrics:** either (a) run the full 30-test dataset so a single edge case can't swing the percentage this hard, or (b) refine the harness so "expected tool never called" and "expected tool called without approval" are tracked as separate, distinctly-labeled failure modes rather than both collapsing into "non-compliant."

## Breakdown by Test Category (n=1 each — directional only)

| Category | Tool Selection Accuracy | Task Completion Rate |
|---|---|---|
| Direct Response | 100.0% | 100.0% |
| Single Tool | 100.0% | 100.0% |
| Multi Tool | 100.0% | 100.0% |
| Approval | 100.0% | 100.0% |
| Failure/Edge Case | 0.0% | 0.0% |

## Reproduction

```powershell
cd productivity-agent\backend
venv\Scripts\Activate.ps1
python run_evaluation.py --llm-provider github --delay 2
python compute_metrics.py
```
