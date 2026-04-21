"""A2A server entrypoint: specialist agents chained via Python.

Architecture:

    A2A Agent (one skill)  ->  validate_tto_checklist(project_task_number)
                                   |
                                   +-- Fetcher Agent (LLM, two calls)
                                   |       tools = [servicenow_mcp_tools]
                                   |       Call 1: fetch project task (tool runs)
                                   |       Call 2: structured_output=TTOFields
                                   |       → returns validated TTOFields
                                   |
                                   +-- Python (no LLM)
                                   |       tasks = build_tto_task_list(fields)
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

Key design:
- Fetcher uses two LLM calls: first with tools to fetch, second with
  structured_output to extract. The conversation context carries the
  work_notes between calls.
- structured_output ensures Pydantic validation of TTOFields.
- build_tto_task_list is called directly by Python — no tool wrapper needed.
- Every handoff between agents is typed Python objects.
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
import litellm
from strands.models.litellm import LiteLLMModel

log = logging.getLogger(__name__)

WORKFLOW_DIR = Path(
    os.getenv("STRANDS_WORKFLOW_DIR", Path.home() / ".strands" / "workflows")
)
TERMINAL_STATES = {"completed", "error"}


FETCHER_PROMPT = """
You are the TTO Fetcher. You retrieve ServiceNow project task details and
extract TTO checklist fields from the work notes.

When asked to fetch a project task:
1. Call `servicenow_project_task_detail` with the project task number
   (format 'GEVPRJTASK...........'). Do not answer from memory.
2. Return the complete work_notes content so it can be parsed.

The work_notes typically contain lines like:
    App CI ID: 1101672345
    UAI: uai3071168
    Box link: https://...
    Github Link: https://...
    Confluence link: https://...
    Application Environment CI: 1101672999
    Used cloud services: ALB, ECR, ECS, Postgres, KMS, S3, IAM

Important distinctions:
- business_application_ci_id is the "App CI ID" — a numeric CMDB id found
  INSIDE the work notes. It is NEVER the project task number (GEVPRJTASK...).
- uai is the Unique Application Identifier, e.g. "uai3071168".
- cloud_services should be parsed as a list: ["ALB", "ECR", "ECS", ...].
- Missing optional fields must be null, never invented.
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

    litellm.ssl_verify = False
    llm_model = LiteLLMModel(
      client_args={
        "api_key": settings.lite_llm_api_key,
        "api_base": settings.litellm_host,
        "use_litellm_proxy": True,
      },
      model_id=settings.model
    )

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
            model=llm_model,
            system_prompt=RUNNER_PROMPT,
            tools=[*all_mcp_tools, workflow],
        )

        # Stateless narrative writer. Reused across requests.
        report_agent = Agent(
            name="TTO Report",
            model=llm_model,
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
            # Fresh fetcher per request — clean conversation state.
            fetcher = Agent(
                name="TTO Fetcher",
                model=llm_model,
                system_prompt=FETCHER_PROMPT,
                tools=servicenow_tools,
            )

            # Call 1: Fetch the project task (tools run, work_notes land in context)
            log.info("Fetching project task %s from ServiceNow...", project_task_number)
            fetch_response = fetcher(
                f"Fetch the project task '{project_task_number}' using "
                f"servicenow_project_task_detail and show me the complete work_notes."
            )

            # Sanity check: ensure we got something back
            fetch_text = str(fetch_response)
            if not fetch_text or len(fetch_text) < 50:
                raise RuntimeError(
                    f"Fetcher returned insufficient data for project task "
                    f"{project_task_number}. Response: {fetch_text[:200]}"
                )

            # Call 2: Extract structured fields (no tool call, just parsing from context)
            log.info("Extracting TTO fields via structured_output...")
            extract_result = fetcher(
                "Now extract the TTO fields from the work_notes you just retrieved. "
                "Remember: business_application_ci_id is the numeric 'App CI ID' from "
                "the work notes, NOT the GEVPRJTASK number.",
                structured_output_model=TTOFields,
            )
            fields: TTOFields = extract_result.structured_output

            # Validate extraction
            if not fields:
                raise RuntimeError(
                    f"Fetcher failed to extract TTOFields for project task "
                    f"{project_task_number}."
                )

            app_ci = fields.business_application_ci_id
            if not app_ci or app_ci.strip().upper().startswith("GEVPRJTASK"):
                raise ValueError(
                    f"Fetcher produced invalid business_application_ci_id={app_ci!r} "
                    f"for project task {project_task_number}. Expected a numeric "
                    f"CMDB id parsed from the work notes; got the project task "
                    f"number itself or an empty value."
                )

            # Build task list in pure Python (no LLM involved)
            log.info("Building task list from templates...")
            tasks = build_tto_task_list(fields)

            workflow_id = f"tto-{app_ci}"
            log.info(
                "Project Task %s -> extracted (app CI %s, uai %s); %d tasks for workflow %s",
                project_task_number, app_ci, fields.uai, len(tasks), workflow_id,
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
            model=llm_model,
            system_prompt=A2A_PROMPT,
            tools=[validate_tto_checklist],
        )

        log.info(
            "Agents ready: fetcher=[servicenow(%d tools) + structured_output — per request], "
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
