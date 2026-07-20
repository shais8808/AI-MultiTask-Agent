"""
run_evaluation.py
==================

Terminal-driven evaluation harness for the Personal Productivity Agent.

What it does
------------
Reads `agent_evaluation_dataset.json` (30 test cases), sends each test's
prompt(s) to the live agent's `POST /api/chat` endpoint, resolves any
approval prompts it hits along the way (approve/reject/edit, per the
test's `approval_decision`), times every round trip, and compares what
actually happened against each test's expected tool sequence /
arguments / final status / success. Writes one row per test to a CSV
with every signal `compute_metrics.py` needs to produce Document 5
(Evaluation Metrics): pass/fail, per-argument match counts, approval
compliance, invalid-action flags, response latency, and graceful-
recovery outcome.

This talks to a REAL running backend + REAL LLM (not the pytest
fake-LLM fixtures) — it's evaluating the agent's actual decision-making.

Usage
-----
    python run_evaluation.py
    python run_evaluation.py --delay 3
    python run_evaluation.py --only AP-01,AP-02

Requires the backend to already be running in another terminal:
    uvicorn app.main:app --reload
"""

import argparse
import csv
import json
import sys
import time
import uuid
from collections import Counter
from typing import Any, Dict, List

import httpx

SENSITIVE_TOOLS = {"update_task", "complete_task", "convert_meeting_notes_to_tasks"}


def load_dataset(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def args_match(expected: Dict[str, Any], actual: Dict[str, Any]) -> bool:
    """A '*' expected value means 'any non-empty value is fine'. Anything
    else must match (case-insensitive for strings)."""
    for key, expected_val in expected.items():
        actual_val = actual.get(key)
        if expected_val == "*":
            if actual_val in (None, "", [], {}):
                return False
            continue
        if isinstance(expected_val, str) and isinstance(actual_val, str):
            if expected_val.lower() != actual_val.lower():
                return False
        elif expected_val != actual_val:
            return False
    return True


def run_test(
    client: httpx.Client,
    base_url: str,
    test: Dict[str, Any],
    llm_provider: str = None,
    llm_model: str = None,
) -> Dict[str, Any]:
    session_id = str(uuid.uuid4())
    # Keyed by tool_call_id / tool_call_id to dedupe: both the initial
    # `awaiting_approval` response and the post-approval resume response
    # return the FULL accumulated tool_calls/tool_results list (the graph
    # state uses an operator.add reducer), so the same call legitimately
    # appears in both HTTP responses and must not be double-counted.
    collected_tool_calls: "Dict[str, Dict[str, Any]]" = {}
    collected_tool_results: "Dict[str, Dict[str, Any]]" = {}
    response_times_ms: List[float] = []
    saw_awaiting_approval_for: set = set()
    final_status = None
    error_note = ""
    hard_error = False  # HTTP-level failure / unhandled exception, as opposed to a clean success=false ToolResult

    for turn_message in test["turns"]:
        chat_payload = {"session_id": session_id, "message": turn_message}
        if llm_provider:
            chat_payload["llm_provider"] = llm_provider
        if llm_model:
            chat_payload["model"] = llm_model

        t0 = time.monotonic()
        try:
            resp = client.post(
                f"{base_url}/api/chat",
                json=chat_payload,
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            error_note = f"HTTP error calling /api/chat: {exc}"
            final_status = "error"
            hard_error = True
            break
        response_times_ms.append((time.monotonic() - t0) * 1000)

        body = resp.json()
        for tc in body.get("tool_calls", []):
            collected_tool_calls[tc["tool_call_id"]] = tc
        for tr in body.get("tool_results", []):
            collected_tool_results[tr["tool_call_id"]] = tr
        final_status = body.get("status")
        if final_status == "error":
            hard_error = True

        if final_status == "awaiting_approval":
            pending = body.get("pending_approval") or {}
            approval_id = pending.get("approval_id")
            pending_tool_name = (pending.get("tool_call") or {}).get("tool_name")
            if pending_tool_name:
                saw_awaiting_approval_for.add(pending_tool_name)

            decision_word = test.get("approval_decision", "approved")
            payload = {"approval_id": approval_id, "decision": decision_word}
            if decision_word == "edited":
                payload["edited_arguments"] = test.get("edited_arguments", {})

            t1 = time.monotonic()
            try:
                resume_resp = client.post(
                    f"{base_url}/api/approvals/{approval_id}", json=payload, timeout=60.0
                )
                resume_resp.raise_for_status()
            except httpx.HTTPError as exc:
                error_note = f"HTTP error calling /api/approvals: {exc}"
                final_status = "error"
                hard_error = True
                break
            response_times_ms.append((time.monotonic() - t1) * 1000)

            resume_body = resume_resp.json()
            for tc in resume_body.get("tool_calls", []):
                collected_tool_calls[tc["tool_call_id"]] = tc
            for tr in resume_body.get("tool_results", []):
                collected_tool_results[tr["tool_call_id"]] = tr
            final_status = resume_body.get("status")
            if final_status == "error":
                hard_error = True

    collected_tool_calls = list(collected_tool_calls.values())
    collected_tool_results = list(collected_tool_results.values())
    actual_tool_sequence = [tc.get("tool_name") for tc in collected_tool_calls]
    expected_tool_sequence = test.get("expected_tool_sequence", [])

    # --- Tool selection ---
    tool_selection_correct = Counter(actual_tool_sequence) == Counter(expected_tool_sequence)

    # --- Argument accuracy (micro-averaged: count matched vs. total checks) ---
    arg_checks_total = 0
    arg_checks_matched = 0
    used_indices = set()
    for tool_name, expected_args in test.get("expected_arguments", {}).items():
        arg_checks_total += 1
        matched = False
        for i, tc in enumerate(collected_tool_calls):
            if i in used_indices or tc.get("tool_name") != tool_name:
                continue
            if args_match(expected_args, tc.get("arguments", {})):
                used_indices.add(i)
                matched = True
                break
        if matched:
            arg_checks_matched += 1
    argument_checks_ok = (arg_checks_matched == arg_checks_total)

    # --- Final status ---
    status_ok = final_status == test.get("expected_final_status")

    # --- Tool-result success expectation (used by the failure/edge-case tests) ---
    success_ok = True
    if "expect_tool_success" in test:
        expected_success = test["expect_tool_success"]
        relevant_results = [
            tr for tr in collected_tool_results if tr.get("tool_name") in expected_tool_sequence
        ]
        success_ok = bool(relevant_results) and any(
            tr.get("success") == expected_success for tr in relevant_results
        )

    # --- Approval compliance: every sensitive tool that ran must have paused for approval first ---
    sensitive_tools_expected = [t for t in expected_tool_sequence if t in SENSITIVE_TOOLS]
    approval_required = bool(sensitive_tools_expected)
    sensitive_tools_executed = [t for t in actual_tool_sequence if t in SENSITIVE_TOOLS]
    approval_compliant = all(t in saw_awaiting_approval_for for t in sensitive_tools_executed) if sensitive_tools_executed else (not approval_required)

    # --- Invalid action: agent acted when it shouldn't have, or skipped a required approval pause ---
    invalid_action = False
    if not approval_compliant:
        invalid_action = True
    if not expected_tool_sequence and actual_tool_sequence:
        invalid_action = True  # direct-response test unexpectedly called a tool
    if test.get("expected_final_status") == "clarification_needed" and final_status != "clarification_needed":
        invalid_action = True  # agent guessed instead of asking (e.g. ambiguous "the third one")

    # --- Recovery: for tests engineered to hit a failure, did the agent degrade gracefully (no crash)? ---
    is_recovery_test = test.get("is_recovery_test", False)
    recovery_ok = (not hard_error) and final_status in ("completed", "clarification_needed") if is_recovery_test else None

    overall_pass = (
        tool_selection_correct and argument_checks_ok and status_ok and success_ok
        and approval_compliant and not invalid_action and not hard_error
    )

    return {
        "test_id": test["test_id"],
        "category": test["category"],
        "prompts": " | ".join(test["turns"]),
        "expected_tools": ", ".join(expected_tool_sequence) or "(none)",
        "expected_final_status": test.get("expected_final_status"),
        "actual_tools": ", ".join(actual_tool_sequence) or "(none)",
        "actual_final_status": final_status,
        "actual_tool_results": json.dumps(
            [{"tool_name": tr.get("tool_name"), "success": tr.get("success"), "error": tr.get("error")}
             for tr in collected_tool_results]
        ),
        "tool_selection_correct": tool_selection_correct,
        "arg_checks_total": arg_checks_total,
        "arg_checks_matched": arg_checks_matched,
        "status_ok": status_ok,
        "success_ok": success_ok,
        "approval_required": approval_required,
        "approval_compliant": approval_compliant,
        "invalid_action": invalid_action,
        "is_recovery_test": is_recovery_test,
        "recovery_ok": recovery_ok,
        "response_times_ms": json.dumps([round(t, 1) for t in response_times_ms]),
        "avg_response_time_ms": round(sum(response_times_ms) / len(response_times_ms), 1) if response_times_ms else "",
        "pass_fail": "PASS" if overall_pass else "FAIL",
        "notes": error_note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent evaluation dataset against a live backend.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="agent_evaluation_dataset.json")
    parser.add_argument("--output", default="evaluation_results.csv")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds to sleep between tests (avoid rate limits).")
    parser.add_argument("--only", default=None, help="Comma-separated Test IDs to run, e.g. AP-01,AP-02")
    parser.add_argument("--llm-provider", default=None, help="Override LLM provider for this run: gemini, github, or openrouter.")
    parser.add_argument("--llm-model", default=None, help="Override LLM model for this run.")
    args = parser.parse_args()

    tests = load_dataset(args.dataset)
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        tests = [t for t in tests if t["test_id"] in wanted]

    fieldnames = [
        "test_id", "category", "prompts", "expected_tools", "expected_final_status",
        "actual_tools", "actual_final_status", "actual_tool_results",
        "tool_selection_correct", "arg_checks_total", "arg_checks_matched",
        "status_ok", "success_ok", "approval_required", "approval_compliant",
        "invalid_action", "is_recovery_test", "recovery_ok",
        "response_times_ms", "avg_response_time_ms", "pass_fail", "notes",
    ]

    results = []
    with httpx.Client() as client:
        try:
            health = client.get(f"{args.base_url}/health", timeout=10.0)
            health.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Cannot reach backend at {args.base_url}/health ({exc}). "
                  f"Start it first with `uvicorn app.main:app --reload`.", file=sys.stderr)
            sys.exit(1)

        for i, test in enumerate(tests, start=1):
            print(f"[{i}/{len(tests)}] Running {test['test_id']} ({test['category']})...")
            result = run_test(client, args.base_url, test, llm_provider=args.llm_provider, llm_model=args.llm_model)
            results.append(result)
            print(f"    -> {result['pass_fail']} (expected {result['expected_tools']!r}, "
                  f"got {result['actual_tools']!r}, status={result['actual_final_status']}, "
                  f"{result['avg_response_time_ms']}ms)")
            time.sleep(args.delay)

    # Merge into any existing CSV from a prior partial/--only run, keyed by test_id,
    # so compute_metrics.py can always be pointed at one complete file.
    existing: Dict[str, Dict[str, Any]] = {}
    try:
        with open(args.output, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["test_id"]] = row
    except FileNotFoundError:
        pass
    for r in results:
        existing[r["test_id"]] = r

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing.values():
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    total = len(results)
    passed = sum(1 for r in results if r["pass_fail"] == "PASS")
    print(f"\n{passed}/{total} tests passed this run ({passed / total:.0%}). "
          f"Results written to {args.output} ({len(existing)} total rows on disk).")
    print("Next: python compute_metrics.py --input evaluation_results.csv")


if __name__ == "__main__":
    main()
