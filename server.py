"""A2A server entrypoint: three specialist agents chained via Python.

Architecture (Option B — Python-chained specialist agents):

    A2A Agent (one skill)  ->  validate_tto_checklist(project_task_number)
                                   |
                                   +-- Planner Agent (LLM)
                                   |       tools = [servicenow_mcp_tools...,
                                   |                build_tto_task_list]
                                   |       - calls servicenow_project_task_detail
                                   |       - calls build_tto_task_list(fields)
                                   |       deliverable: the exact list[dict]
                                   |       produced by build_tto_task_list
                                   |
                                   +-- Runner Agent (tool carrier)
                                   |       tools = [*all_mcp_tools, workflow]
                                   |       Python drives workflow create/start
                                   |       then polls the on-disk state
                                   |
                                   +-- Report Agent (LLM)
                                           tools = []
                                           turns the raw per-task results
                                           into a human-readable markdown report

Every handoff between agents carries REAL Python objects (TTOFields, list[dict],
dict state). No LLM ever re-serializes structured data in a prompt string,
which is where previous designs broke.

The planner's task list is captured out-of-band: `build_tto_task_list` is
re-wrapped as a per-request @tool that stashes its output into a dict the
enclosing Python function owns. That way the LLM MUST call the tool to
finish its job, and Python gets the exact return value back — not a
paraphrase.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from strands import Agent, tool
from strands.multiagent.a2a import A2AServer
from strands_tools import workflow

from . import tools as tools_module
from .mcp_sessions import open_sessions
from .settings import Settings
from .tools import TTOFields, build_tto_task_list

log = logging.getLogger(__name__)

WORKFLOW_DIR = Path(
    os.getenv("STRANDS_WORKFLOW_DIR", Path.home() / ".strands" / "workflows")
)
TERMINAL_STATES = {"completed", "error"}


PLANNER_PROMPT = """
You are the TTO Planner. Your single deliverable is a task list produced by
calling `build_tto_task_list`. Follow these steps in order, every time:

STEP 1 — Fetch the record.
Call `servicenow_project_task_detail` with the project task number you are
given (format 'GEVPRJTASK...........'). Do not answer from memory and do not
use any other ServiceNow tool for this step.

STEP 2 — Parse the work notes.
The checklist lives in the `work_notes` field of the response. It contains
lines like:

    App CI ID: 1101672345
    UAI: uai3071168
    Box link: https://...
    Github Link: https://...
    Confluence link: https://...
    Application Environment CI: 1101672999
    Used cloud services: ALB, ECR, ECS, Postgres, KMS, S3, IAM

Extract these TTO fields:
  - business_application_ci_id  (required) — the "App CI ID" value, a numeric
    CMDB id from INSIDE the work notes. It is NEVER the project task number
    (GEVPRJTASK...). If you are about to pass "GEVPRJTASK..." here, stop and
    re-parse.
  - uai                         (required) — e.g. "uai3071168"
  - box_link, github_link, confluence_link, application_environment_ci
  - cloud_services — parse "ALB, ECR, …" into ["ALB","ECR",…]
Missing optional fields MUST be null. Never invent, never copy the project
task number.

STEP 3 — Build the task list.
Call `build_tto_task_list` EXACTLY ONCE, passing a `fields` argument that
matches the TTOFields schema. The tool's return value is your deliverable.
Do not modify it, do not restate it.

STEP 4 — Acknowledge.
Reply with a single short sentence confirming you called build_tto_task_list.
Do not re-emit the task list in text.
""".strip()


RUNNER_PROMPT = """
You are the TTO Runner. You hold the workflow tool and every MCP tool that
workflow tasks may need. The Python layer drives you directly; do not
improvise actions on your own.
""".strip()


REPORT_PROMPT = """
You are the TTO Report writer. Given a ServiceNow Project Task's TTO
validation results, produce a concise, professional markdown report.

Structure:

1. A header with the project task number, business application CI, workflow
   id, and an at-a-glance posture line (e.g. "Posture: 5 PASS, 1
   NEEDS_REVIEW, 0 FAIL, 0 MISSING — 6 checks total").
2. A "Checklist" section with one subsection per task_id, in the original
   order. For each task include:
     - **Status**: PASS / FAIL / NEEDS_REVIEW / MISSING (use the value you
       are given).
     - **Evidence**: bullet list of facts the task gathered from tools.
     - **Notes**: the task's one-line rationale, if any.
3. An "Actions required" section: bullet list of every FAIL or NEEDS_REVIEW
   item with the concrete remediation step implied by its evidence.

Rules:
- Only use the facts provided. Do not invent values, ids, or links.
- If a task result is missing or empty, say so explicitly — do not imply it
  passed.
- Do not add filler commentary or executive summary beyond the posture line.
""".strip()


A2A_PROMPT = """
You are the TTO Checklist Validator. The caller will ask you to validate the
TTO checklist for a ServiceNow Project Task (format: 'GEVPRJTASK...........').
Extract that project task number from the request and call
`validate_tto_checklist(project_task_number=<that number>)`. Return its
output verbatim.
""".strip()


def _read_workflow_state(wf_id: str) -> dict[str, Any]:
    """Read the persisted workflow state from disk.

    The `workflow` tool persists full state (including per-task `result` blocks)
    to ${STRANDS_WORKFLOW_DIR:-~/.strands/workflows}/<workflow_id>.json after
    every task transition. The tool's `action="status"` only returns a rendered
    text panel, so we go to the source.
    """
    path = WORKFLOW_DIR / f"{wf_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _all_terminal(state: dict[str, Any]) -> bool:
    """True when every task in task_results has reached completed or error."""
    results = state.get("task_results") or {}
    if not results:
        return False
    return all(r.get("status") in TERMINAL_STATES for r in results.values())


def _poll_workflow(wf_id: str, timeout: int = 900, interval: float = 3.0) -> dict[str, Any]:
    """Poll the on-disk workflow state until every task is terminal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _read_workflow_state(wf_id)
        if _all_terminal(state):
            return state
        time.sleep(interval)
    raise TimeoutError(f"Workflow {wf_id} did not reach a terminal state within {timeout}s")


def _task_summary(planned: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-task {status, result} dict in the planner's original task order."""
    results = state.get("task_results") or {}
    summary: dict[str, dict[str, Any]] = {}
    for task in planned:
        tid = task["task_id"]
        entry = results.get(tid)
        if not entry:
            summary[tid] = {"status": "MISSING", "result": None}
            continue
        summary[tid] = {
            "status": entry.get("status"),
            "result": entry.get("result"),
        }
    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    settings = Settings()

    with ExitStack() as stack:
        tools_by_source, registry = open_sessions(settings, stack)
        tools_module.REGISTRY = registry

        if not tools_by_source.get("servicenow"):
            raise RuntimeError("ServiceNow MCP is required but has no tools available.")

        servicenow_tools = list(tools_by_source["servicenow"])
        all_mcp_tools = [t for tools in tools_by_source.values() for t in tools]

        # Tool carrier for the workflow engine. Python drives it; its LLM is
        # never called for reasoning.
        runner = Agent(
            name="TTO Runner",
            model=settings.model,
            system_prompt=RUNNER_PROMPT,
            tools=[*all_mcp_tools, workflow],
        )

        # Stateless narrative writer. Reused across requests.
        report_agent = Agent(
            name="TTO Report",
            model=settings.model,
            system_prompt=REPORT_PROMPT,
            tools=[],
        )

        @tool
        def validate_tto_checklist(project_task_number: str) -> dict[str, Any]:
            """Validate the TTO checklist on a ServiceNow Project Task.

            Args:
                project_task_number: ServiceNow `pm_project_task` number, e.g.
                    'GEVPRJTASK0481346'. Its work notes contain the checklist.

            Returns:
                {
                  "project_task_number": str,
                  "business_application_ci_id": str,
                  "workflow_id": str,
                  "report": str,          # markdown report from report agent
                  "summary": dict,        # {task_id -> {status, result}}
                  "raw_state": dict,      # full workflow state for audit
                }
            """
            # Per-request stash so Python can read the exact list[dict] that
            # build_tto_task_list produced. The planner LLM is forced to invoke
            # this tool to finish its job (see PLANNER_PROMPT).
            captured: dict[str, Any] = {}

            @tool
            def build_tto_task_list_tool(fields: TTOFields) -> list[dict]:
                """Expand the static TTO templates into the workflow tasks list.

                Call this EXACTLY ONCE after extracting TTO fields from the
                ServiceNow work notes. The `fields` argument must match the
                TTOFields schema. The returned list is the planner's final
                deliverable; do not re-emit it in text.
                """
                result = build_tto_task_list(fields)
                captured["fields"] = fields
                captured["tasks"] = result
                return result

            # Fresh planner per request — clean conversation state, and the
            # stashing tool is pinned to this request's `captured` dict.
            planner = Agent(
                name="TTO Planner",
                model=settings.model,
                system_prompt=PLANNER_PROMPT,
                tools=[*servicenow_tools, build_tto_task_list_tool],
            )

            planner(
                f"Validate the TTO checklist for project task "
                f"'{project_task_number}'. Follow your steps 1-4."
            )

            tasks = captured.get("tasks")
            fields: TTOFields | None = captured.get("fields")
            if not tasks or fields is None:
                raise RuntimeError(
                    f"Planner finished without calling build_tto_task_list for "
                    f"project task {project_task_number}. No task list produced."
                )

            app_ci = fields.business_application_ci_id
            if not app_ci or app_ci.strip().upper().startswith("GEVPRJTASK"):
                raise ValueError(
                    f"Planner produced invalid business_application_ci_id={app_ci!r} "
                    f"for project task {project_task_number}. Expected a numeric "
                    f"CMDB id parsed from the work notes; got the project task "
                    f"number itself or an empty value."
                )

            workflow_id = f"tto-{app_ci}"
            log.info(
                "Project Task %s -> extracted (app CI %s); %d tasks for workflow %s",
                project_task_number, app_ci, len(tasks), workflow_id,
            )

            runner.tool.workflow(action="create", workflow_id=workflow_id, tasks=tasks)
            runner.tool.workflow(action="start", workflow_id=workflow_id)
            state = _poll_workflow(workflow_id)
            summary = _task_summary(tasks, state)

            report_prompt = (
                f"Project task: {project_task_number}\n"
                f"Business Application CI: {app_ci}\n"
                f"Workflow: {workflow_id}\n\n"
                f"Per-task results (JSON):\n"
                f"```json\n{json.dumps(summary, indent=2, default=str)}\n```"
            )
            report_text = str(report_agent(report_prompt))

            log.info(
                "Workflow %s complete; %d tasks, report=%d chars",
                workflow_id, len(summary), len(report_text),
            )

            return {
                "project_task_number": project_task_number,
                "business_application_ci_id": app_ci,
                "workflow_id": workflow_id,
                "report": report_text,
                "summary": summary,
                "raw_state": state,
            }

        a2a_agent = Agent(
            name="TTO Checklist Validator",
            description="Validates a ServiceNow project task's TTO checklist end-to-end.",
            model=settings.model,
            system_prompt=A2A_PROMPT,
            tools=[validate_tto_checklist],
        )

        log.info(
            "Agents ready: planner=[servicenow(%d tools), build_tto_task_list — per request], "
            "runner=[%d MCP tools total, workflow], report=[no tools], "
            "a2a=[validate_tto_checklist]. Source tool counts: %s",
            len(servicenow_tools),
            len(all_mcp_tools),
            ", ".join(f"{s}={len(n)}" for s, n in registry.items()),
        )
        log.info("Serving A2A on %s:%d", settings.host, settings.port)
        A2AServer(agent=a2a_agent, host=settings.host, port=settings.port).serve()


if __name__ == "__main__":
    main()
