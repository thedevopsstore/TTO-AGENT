"""Main entry point for TTO checklist validation workflow."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import time
from typing import Any

from strands import Agent
from strands_tools import workflow

from mcp_registry import create_mcp_clients
from report import consolidate_item_results, format_report
from workflow_tasks import build_tto_tasks


ORCHESTRATOR_SYSTEM_PROMPT = """
You are a TTO checklist validator responsible for validating infrastructure
handover from Solution Architects to Operations teams.

You orchestrate validation tasks across ServiceNow, Confluence, and GitHub systems.

Tool usage guidance:
- For ServiceNow lookups, always query by UAI first, then cross-reference with CI ID.
  Use search/query tools before fetch/get tools to locate the correct record first.
- For Confluence, retrieve the page content before checking for attachments or diagrams.
  When validating an architecture diagram, look for draw.io file attachments specifically.
- For GitHub, prefer tools that retrieve specific file paths over broad search tools when
  you know the directory structure. Look in /terraform, /cloudformation, or /iac directories
  for IaC files when validating baseline and template compliance.
- When a dependency task has provided a URL or identifier, use that directly rather than
  searching for it again.

Always produce structured output per checklist item:
  - Item number and name
  - Status: PASS | FAIL | NA
  - Evidence: what you found
  - Action: recommended remediation if FAIL, empty if PASS
""".strip()

MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

TERMINAL_WORKFLOW_STATES = {"completed", "failed", "error", "cancelled"}


def _extract_workflow_state(status_obj: Any) -> str | None:
    """Extract best-effort workflow state from workflow status payload."""
    if isinstance(status_obj, dict):
        for key in ("workflow_status", "status", "state"):
            value = status_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        content = status_obj.get("content")
        if isinstance(content, str):
            lowered = content.lower()
            for marker in TERMINAL_WORKFLOW_STATES.union({"running", "in_progress"}):
                if marker in lowered:
                    return marker
    if isinstance(status_obj, str):
        lowered = status_obj.lower()
        for marker in TERMINAL_WORKFLOW_STATES.union({"running", "in_progress"}):
            if marker in lowered:
                return marker
    return None


def _wait_for_completion(
    orchestrator: Agent,
    workflow_id: str,
    timeout_seconds: int = 1200,
    poll_seconds: int = 3,
) -> tuple[Any, dict[str, str]]:
    """
    Poll workflow status until completion or timeout.

    Returns final status payload and any task-level failure map discovered in status snapshots.
    """
    started_at = time.time()
    task_failures: dict[str, str] = {}
    last_status: Any = None

    while True:
        status_obj = orchestrator.tool.workflow(action="status", workflow_id=workflow_id)
        last_status = status_obj

        # Try to capture task failures in flexible status structures.
        if isinstance(status_obj, dict):
            tasks = status_obj.get("tasks")
            if isinstance(tasks, list):
                for task in tasks:
                    if not isinstance(task, dict):
                        continue
                    task_id = str(task.get("task_id", ""))
                    task_status = str(task.get("status", "")).lower()
                    if task_id and task_status in {"failed", "error"}:
                        reason = str(task.get("error") or task.get("message") or "Task failed.")
                        task_failures[task_id] = reason
            elif isinstance(tasks, dict):
                for task_id, task in tasks.items():
                    if not isinstance(task, dict):
                        continue
                    task_status = str(task.get("status", "")).lower()
                    if task_status in {"failed", "error"}:
                        reason = str(task.get("error") or task.get("message") or "Task failed.")
                        task_failures[str(task_id)] = reason

        state = _extract_workflow_state(status_obj)
        if state in TERMINAL_WORKFLOW_STATES:
            return status_obj, task_failures

        if time.time() - started_at > timeout_seconds:
            task_failures["__workflow__"] = f"Workflow timeout after {timeout_seconds} seconds."
            return last_status, task_failures

        time.sleep(poll_seconds)


def run_validation(project_id: str) -> str:
    """Create, run, and report the TTO validation workflow."""
    mcp_clients = create_mcp_clients()

    with ExitStack() as stack:
        for client in mcp_clients.values():
            stack.enter_context(client)

        orchestrator = Agent(
            model=MODEL_ID,
            tools=[workflow, *mcp_clients.values()],
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        )

        workflow_id = f"tto_validation_{project_id}"
        tasks = build_tto_tasks(project_id, mcp_clients)

        orchestrator.tool.workflow(
            action="create",
            workflow_id=workflow_id,
            tasks=tasks,
        )
        orchestrator.tool.workflow(action="start", workflow_id=workflow_id)

        status_payload, task_failures = _wait_for_completion(orchestrator, workflow_id)
        item_results = consolidate_item_results(status_payload, task_failures=task_failures)
        return format_report(project_id=project_id, item_results=item_results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Strands SDK TTO checklist validation workflow.")
    parser.add_argument("project_id", help="Project identifier used to build workflow id and task prompts.")
    args = parser.parse_args()

    try:
        report_text = run_validation(args.project_id)
        print(report_text)
    except Exception as exc:  # pragma: no cover - runtime integration protection
        print("Unexpected validation error:")
        print(json.dumps({"error": str(exc)}, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
