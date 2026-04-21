"""A2A server entrypoint: wires a two-agent sequential workflow behind a single skill.

Architecture (https://strandsagents.com/docs/user-guide/concepts/multi-agent/workflow/):

    A2A Agent (one skill)  ->  validate_tto_checklist (Python)
                                   |
                                   +-- Planner Agent
                                   |       tools=[servicenow_mcp, build_tto_task_list]
                                   |       returns PlanOutput(workflow_id, tasks)
                                   |
                                   +-- Runner Agent
                                           tools=[*all_mcp_clients, workflow]
                                           Python drives workflow create/start/status

Python, not the LLM, chains the two agents and drives the workflow lifecycle.
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

WORKFLOW_DIR = Path(
    os.getenv("STRANDS_WORKFLOW_DIR", Path.home() / ".strands" / "workflows")
)
TERMINAL_STATES = {"completed", "error"}

from . import tools as tools_module
from .mcp_sessions import open_sessions
from .settings import Settings
from .tools import PlanOutput, build_tto_task_list

log = logging.getLogger(__name__)


PLANNER_PROMPT = """
You are the TTO Planner. You produce a workflow plan for validating a
ServiceNow project's Transition-To-Operations checklist.

For the project CI id you are given:

1. Use the ServiceNow MCP tools to locate the project record and read its
   work notes.
2. Extract the TTO fields from the work-note text:
     - business_application_ci_id
     - uai
     - box_link
     - github_link
     - confluence_link
     - application_environment_ci
     - cloud_services (parse a comma-separated list like
       "ALB, ECR, ECS, Postgres, KMS, S3, IAM" into an array of strings)
   Missing optional fields MUST be reported as null, never invented.
3. Call `build_tto_task_list(fields=<your extracted TTOFields>)`. Take its
   return value verbatim. Do NOT paraphrase, re-order, or modify any task.
4. Emit the final plan:
     - workflow_id = "tto-" + business_application_ci_id
     - tasks       = the EXACT list returned by build_tto_task_list
""".strip()


A2A_PROMPT = """
You are the TTO Checklist Validator. Extract the project CI id from the
caller's request and call `validate_tto_checklist(project_ci_id=<that id>)`.
Return its output verbatim.
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


def _compile_report(planned: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    """Build a per-task status+result summary; include raw state for audit."""
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
    return {"summary": summary, "raw_state": state}


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

        all_mcp_tools = [t for tools in tools_by_source.values() for t in tools]

        planner = Agent(
            name="TTO Planner",
            model=settings.model,
            system_prompt=PLANNER_PROMPT,
            tools=[*tools_by_source["servicenow"], build_tto_task_list],
        )

        runner = Agent(
            name="TTO Runner",
            model=settings.model,
            system_prompt="You hold the workflow tool and the MCP tools needed by the tasks.",
            tools=[*all_mcp_tools, workflow],
        )

        @tool
        def validate_tto_checklist(project_ci_id: str) -> dict[str, Any]:
            """Validate the TTO checklist for a ServiceNow project end-to-end.

            Chains two specialist agents via Python:
              1. Planner: fetches work notes, extracts fields, calls
                 build_tto_task_list. Returns a typed PlanOutput.
              2. Runner:  Python drives workflow create/start/status via its
                 `workflow` tool; each workflow task runs a sub-agent scoped
                 to the MCP tool names listed in its `tools` field.

            Returns a summary (per-task STATUS + result) plus the raw workflow
            status payload for audit.
            """
            plan = planner.structured_output(
                PlanOutput,
                f"Plan the TTO validation for project CI {project_ci_id}.",
            )
            tasks = [t.model_dump() for t in plan.tasks]
            log.info("Planner produced %d tasks for workflow %s", len(tasks), plan.workflow_id)

            runner.tool.workflow(action="create", workflow_id=plan.workflow_id, tasks=tasks)
            runner.tool.workflow(action="start", workflow_id=plan.workflow_id)
            state = _poll_workflow(plan.workflow_id)
            return _compile_report(tasks, state)

        a2a_agent = Agent(
            name="TTO Checklist Validator",
            description="Validates a ServiceNow project's TTO checklist end-to-end.",
            model=settings.model,
            system_prompt=A2A_PROMPT,
            tools=[validate_tto_checklist],
        )

        log.info(
            "Agents built: planner=[servicenow(%d tools), build_tto_task_list], "
            "runner=[%d MCP tools total, workflow], a2a=[validate_tto_checklist]. "
            "Source tool counts: %s",
            len(tools_by_source["servicenow"]),
            len(all_mcp_tools),
            ", ".join(f"{s}={len(n)}" for s, n in registry.items()),
        )
        log.info("Serving A2A on %s:%d", settings.host, settings.port)
        A2AServer(agent=a2a_agent, host=settings.host, port=settings.port).serve()


if __name__ == "__main__":
    main()
