"""A2A server entrypoint: wires a two-agent sequential workflow behind a single skill.

Architecture (https://strandsagents.com/docs/user-guide/concepts/multi-agent/workflow/):

    A2A Agent (one skill)  ->  validate_tto_checklist (Python)
                                   |
                                   +-- Planner Agent (LLM)
                                   |       tools=[servicenow_mcp_tools...]
                                   |       structured_output -> TTOFields
                                   |       (required fields force ServiceNow tool calls)
                                   |
                                   +-- build_tto_task_list(fields)   (pure Python)
                                   |
                                   +-- Runner Agent
                                           tools=[*all_mcp_tools, workflow]
                                           Python drives workflow create/start/status

The planner LLM only does what LLMs are good at: fetch + extract. Task-list
construction is deterministic Python so it can't be skipped or mangled.
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
from .tools import TTOFields, build_tto_task_list

log = logging.getLogger(__name__)


PLANNER_PROMPT = """
You are the TTO Planner. You will be given a ServiceNow Project Task number
(format: 'GEVPRJTASK...........'). The TTO checklist lives in that task's
**work notes**.

STEP 1 — Fetch the record. You MUST call the tool
`servicenow_project_task_detail` with the given project task number. Do not
use any other ServiceNow tool for this step, and do not answer from memory.

STEP 2 — Parse the work notes. Locate the `work_notes` (or equivalent) field
in the tool's response. It contains lines like:

    App CI ID: 1101672345
    UAI: uai3071168
    Box link: https://...
    Github Link: https://...
    Confluence link: https://...
    Application Environment CI: 1101672999
    Used cloud services: ALB, ECR, ECS, Postgres, KMS, S3, IAM

STEP 3 — Emit TTOFields with the values you parsed:

  - business_application_ci_id  (required) - the "App CI ID" line's value
    (e.g. "1101672345"). This is a numeric CMDB id found INSIDE the work
    notes. It is NEVER equal to the project task number you were given
    (GEVPRJTASK...). If the value you are about to emit starts with
    "GEVPRJTASK", you have the wrong field — re-parse the work notes.
  - uai                         (required) - e.g. "uai3071168"
  - box_link
  - github_link
  - confluence_link
  - application_environment_ci
  - cloud_services  (parse the comma-separated list into an array of
    strings, e.g. ["ALB", "ECR", "ECS", "Postgres", "KMS", "S3", "IAM"])

Optional fields MUST be null when the work notes do not contain them —
never invented, never copied from the project task number.

Do not plan tasks, do not summarize, do not transform. Return TTOFields only.
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
            tools=list(tools_by_source["servicenow"]),
        )

        runner = Agent(
            name="TTO Runner",
            model=settings.model,
            system_prompt="You hold the workflow tool and the MCP tools needed by the tasks.",
            tools=[*all_mcp_tools, workflow],
        )

        @tool
        def validate_tto_checklist(project_task_number: str) -> dict[str, Any]:
            """Validate the TTO checklist on a ServiceNow Project Task.

            Args:
                project_task_number: ServiceNow `pm_project_task` number, e.g.
                    'GEVPRJTASK0481346'. Its work notes contain the checklist.

            Flow:
              1. Planner LLM looks up the Project Task in ServiceNow and reads
                 its work notes (required TTOFields force the tool calls).
              2. Python calls build_tto_task_list(fields) deterministically.
              3. Runner drives the workflow tool (create -> start -> poll on disk).
              4. Compile per-task status + raw state for audit.
            """
            fields = planner.structured_output(
                TTOFields,
                (
                    f"Call servicenow_project_task_detail with "
                    f"project_task_number='{project_task_number}', parse the "
                    f"TTO checklist from the returned work notes, and emit "
                    f"TTOFields. The business_application_ci_id in your output "
                    f"MUST come from the 'App CI ID' line in the work notes, "
                    f"NOT from the project task number."
                ),
            )

            app_ci = fields.business_application_ci_id
            if not app_ci or app_ci.strip().upper().startswith("GEVPRJTASK"):
                raise ValueError(
                    f"Planner returned invalid business_application_ci_id={app_ci!r} "
                    f"for project task {project_task_number}. Expected a numeric "
                    f"CMDB id parsed from the work notes; got the project task "
                    f"number itself or an empty value."
                )

            tasks = build_tto_task_list(fields)
            workflow_id = f"tto-{app_ci}"
            log.info(
                "Project Task %s -> fields extracted (app CI %s); built %d tasks for workflow %s",
                project_task_number,
                app_ci,
                len(tasks),
                workflow_id,
            )

            runner.tool.workflow(action="create", workflow_id=workflow_id, tasks=tasks)
            runner.tool.workflow(action="start", workflow_id=workflow_id)
            state = _poll_workflow(workflow_id)
            return _compile_report(tasks, state)

        a2a_agent = Agent(
            name="TTO Checklist Validator",
            description="Validates a ServiceNow project's TTO checklist end-to-end.",
            model=settings.model,
            system_prompt=A2A_PROMPT,
            tools=[validate_tto_checklist],
        )

        log.info(
            "Agents built: planner=[servicenow(%d tools)], "
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
