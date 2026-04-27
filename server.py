"""Strands Steered Workflow Agent for TTO Checklist Validation.

Architecture:

Pipeline (all steps driven by the agent, guarded by steering):

  ┌─────────────────────────────────────────────────────────────────┐
  │ 1. MCP tool          → fetch raw project data                   │
  │ 2. Structured output → TTOFields (Pydantic)                     │
  │ 3. build_task_list   → List[WorkflowTask] (workflow-tool schema)│
  │ 4. workflow(create)  → register tasks with Strands workflow tool│
  │ 5. workflow(start)   → execute (parallel where deps allow)      │
  │ 6. workflow(status)  → report final state                       │
  └─────────────────────────────────────────────────────────────────┘

The `build_task_list` tool is the ONLY custom tool. Steps 4–6 use the
official `strands_tools.workflow` tool directly — no custom trigger needed.

Steering enforces logical ordering without hard-wiring a graph:
  - Guard: don't call build_task_list before extraction has happened
  - Guard: don't call workflow(create) with zero tasks
  - Guard: don't finish the session without calling workflow(start)

Transport:

The steered Strands Agent is wrapped by `ag_ui_strands.StrandsAgent` and
mounted on a FastAPI app at `POST /invocations` (SSE stream of AG-UI events)
with a `GET /ping` health check. This is the layout expected by Bedrock
AgentCore Runtime for AG-UI containers and by any CopilotKit / AG-UI client.

Key design:
- Single agent with LLMSteeringHandler + LedgerProvider for pipeline enforcement
- Agent uses structured_output for extraction (one LLM call)
- build_task_list is exposed as a @tool for the agent to call
- workflow tool actions (create, start, status) are driven by the agent
- Steering ensures steps happen in order without manual orchestration
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import uvicorn
from ag_ui_strands import StrandsAgent, create_strands_app

from strands import Agent, tool
from strands.vended_plugins.steering import LLMSteeringHandler, LedgerProvider
from strands_tools import workflow

from .mcp_sessions import open_sessions
from .settings import Settings
from .tools import TTOFields, build_tto_task_list as _build_tto_task_list
import litellm
from strands.models.litellm import LiteLLMModel

log = logging.getLogger(__name__)

# Workflow state directory (used by strands_tools.workflow internally)
WORKFLOW_DIR = Path(
    os.getenv("STRANDS_WORKFLOW_DIR", Path.home() / ".strands" / "workflows")
)


STEERING_PROMPT = """
You are a pipeline quality gate for a TTO checklist validation agent.

The agent must complete ALL of these steps before finishing:
  Step 1: Call servicenow_project_task_detail to fetch raw project task data
  Step 2: Use structured_output to extract TTOFields from the work notes
  Step 3: Call build_task_list with the extracted TTOFields JSON
  Step 4: Call workflow(action="create", workflow_id=..., tasks=[...])
          using the exact workflow_id and tasks returned by build_task_list
  Step 5: Call workflow(action="start", workflow_id=...)
  Step 6: Call workflow(action="status", workflow_id=...) to report results

Enforce these rules:

  A. If the agent tries to call build_task_list but servicenow_project_task_detail
     has not run yet, GUIDE it: "Fetch the project task data first using 
     servicenow_project_task_detail."

  B. If the agent tries to call build_task_list but structured_output extraction
     has not been performed yet (no TTOFields in the conversation), GUIDE it:
     "Extract the TTOFields first using structured_output."

  C. If the agent tries to call workflow(action="create") before build_task_list
     has returned a successful result (with a non-empty tasks list), GUIDE it:
     "Run build_task_list first — pass it the TTOFields from your structured extraction."

  D. If the agent tries to call workflow(action="create") but the tasks argument
     is empty or missing, GUIDE it:
     "The tasks list is empty. Check the build_task_list output and pass the tasks array."

  E. If the agent tries to call workflow(action="start") before
     workflow(action="create") has been called, GUIDE it:
     "Create the workflow first: workflow(action='create', workflow_id=..., tasks=...)."

  F. If the agent is about to stop responding and workflow(action="start") has
     not been called yet, GUIDE it:
     "Pipeline incomplete — you still need to call workflow(action='start') to execute the work."

When guiding, be specific: name the exact tool, action, and argument values
the agent should use (taken from prior tool outputs in the ledger).

Use the ledger in your context to determine exactly which steps are done.

CRITICAL: The agent must ONLY report on tasks that were executed in the workflow.
If the ledger shows build_task_list returned N tasks and workflow(create) registered N tasks,
the final report must have exactly N task results, no more, no less.
""".strip()


AGENT_PROMPT = """
You are a TTO (Technical Turnover) Checklist Validator for ServiceNow Project Tasks.

For every project task you receive, execute this pipeline end-to-end:

Step 1 — FETCH
  Call servicenow_project_task_detail with the project_task_number (format: 'GEVPRJTASK...........') 
  to retrieve the raw project task data including work_notes.

Step 2 — EXTRACT (structured output)
  Read the work_notes from the fetched data. Extract every TTO checklist field you find:
    - business_application_ci_id: The "App CI ID" (numeric CMDB id) from work notes
    - uai: Unique Application Identifier (e.g., "uai3071168")
    - box_link: URL to Box folder (optional)
    - github_link: URL to GitHub repository (optional)
    - confluence_link: URL to Confluence page (optional)
    - application_environment_ci: ServiceNow CI identifier for Application Environment (optional)
    - cloud_services: List of cloud services (e.g., ["ALB", "ECR", "ECS"]) (optional)
  
  CRITICAL: business_application_ci_id is the numeric "App CI ID" from the work notes,
  NOT the GEVPRJTASK number. Never confuse these two.
  
  Use structured_output=TTOFields for this extraction. This ensures Pydantic validation.

Step 3 — BUILD TASK LIST
  Call build_task_list(fields_json=<JSON string of your TTOFields extraction>).
  The tool returns a workflow_id and a tasks array in workflow-tool format.

Step 4 — CREATE WORKFLOW
  Call: workflow(action="create", workflow_id=<id from step 3>, tasks=<tasks from step 3>)

Step 5 — START WORKFLOW
  Call: workflow(action="start", workflow_id=<same id>)

Step 6 — CHECK STATUS AND REPORT
  Call: workflow(action="status", workflow_id=<same id>)
  
  CRITICAL REPORTING RULES:
  • ONLY report on tasks that were ACTUALLY EXECUTED in the workflow
  • DO NOT invent, assume, or hallucinate other TTO checklist items
  • Use ONLY the task results from workflow(action="status") output
  • The posture count MUST match the exact number of tasks that ran
  • If a task was skipped (e.g., missing box_link), DO NOT include it in the report
  
  Then generate a professional markdown report with:
  1. Header: project task number, business application CI, workflow id, and posture summary
     Example: "Posture: 3 PASS, 1 NEEDS_REVIEW, 0 FAIL, 0 MISSING — 4 checks total"
     The count (4 in this example) MUST equal the number of tasks that actually ran.
  
  2. Checklist section: ONLY include tasks that were executed in the workflow.
     For each executed task, show:
     - Task ID (from workflow results)
     - Status (PASS / FAIL / NEEDS_REVIEW / MISSING)
     - Evidence (bullet list from the task's result)
     - Notes (one-line rationale from the task's result)
     
     DO NOT add tasks that were not in the workflow execution.
  
  3. Actions required: bullet list of FAIL or NEEDS_REVIEW items with remediation steps.
     Base this ONLY on the tasks that were executed.
  
  Present this report to the user as your final answer.

Always complete all six steps. If a step fails, explain why and retry or recover.
The steering system will guide you if you try to skip steps or execute them out of order.

IMPORTANT: When generating the final report, use ONLY the actual workflow results.
Do not invent or include checklist items that were not executed. Some tasks may be
skipped if optional fields are missing (e.g., box_link, cloud_services). This is
expected behavior. Report only what actually ran.
""".strip()


class TTOWorkflowSteering(LLMSteeringHandler):
    """
    Steers the agent through the TTO validation pipeline without hard-coding a graph.
    
    The LedgerProvider populates steering_context["ledger"] before each
    steer_before_tool() call, so the steering LLM can inspect the full call
    history and reason about what is still missing.
    """
    
    def __init__(self, model: Any):
        super().__init__(
            system_prompt=STEERING_PROMPT,
            model=model,
            context_providers=[LedgerProvider()],
        )


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
        tools_by_source, _ = open_sessions(settings, stack)

        if not tools_by_source.get("servicenow"):
            raise RuntimeError("ServiceNow MCP is required but has no tools available.")

        servicenow_tools = list(tools_by_source["servicenow"])
        all_mcp_tools = [t for tools in tools_by_source.values() for t in tools]

        @tool
        def build_task_list(fields_json: str) -> dict[str, Any]:
            """Build workflow tasks from extracted TTO fields.
            
            This is the ONLY custom tool in the pipeline. It transforms the
            extracted TTOFields into the task list format required by the
            workflow tool.
            
            IMPORTANT: Some tasks may be skipped if required fields are missing
            (e.g., if box_link is null, verify_box_link is not created).
            Only tasks with all required fields will be included.
            
            Args:
                fields_json: JSON string of TTOFields (from structured_output extraction)
                
            Returns:
                {
                  "workflow_id": str,        # e.g., "tto-1101672345"
                  "tasks": list,             # workflow-tool format tasks (only tasks with required fields)
                  "task_count": int,         # number of tasks created
                  "task_ids": list[str],     # list of task_id strings
                  "message": str,            # reminder to report ONLY on these tasks
                }
            """
            try:
                fields_dict = json.loads(fields_json)
                fields = TTOFields(**fields_dict)
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(
                    f"Invalid fields_json: {e}. Expected JSON string of TTOFields."
                ) from e
            
            app_ci = fields.business_application_ci_id
            if not app_ci or app_ci.strip().upper().startswith("GEVPRJTASK"):
                raise ValueError(
                    f"Invalid business_application_ci_id={app_ci!r}. "
                    f"Expected a numeric CMDB id, not a GEVPRJTASK number."
                )
            
            tasks = _build_tto_task_list(fields)
            workflow_id = f"tto-{app_ci}"
            
            task_ids = [t["task_id"] for t in tasks]
            
            log.info(
                "Built %d tasks for workflow %s: %s",
                len(tasks),
                workflow_id,
                ", ".join(task_ids),
            )
            
            return {
                "workflow_id": workflow_id,
                "tasks": tasks,
                "task_count": len(tasks),
                "task_ids": task_ids,
                "message": (
                    f"Created {len(tasks)} workflow tasks: {', '.join(task_ids)}. "
                    f"IMPORTANT: Only these {len(tasks)} tasks will be executed. "
                    f"Report ONLY on these tasks in your final report, no others."
                ),
            }

        # Create the steered TTO workflow agent.
        # This single agent handles the entire pipeline with steering guards.
        tto_agent = Agent(
            name="TTO Checklist Validator",
            description="Validates a ServiceNow project task's TTO checklist end-to-end using a steered workflow.",
            model=llm_model,
            system_prompt=AGENT_PROMPT,
            tools=[
                *all_mcp_tools,  # Step 1: ServiceNow, GitHub, Confluence, etc.
                build_task_list,  # Step 3: Custom task builder
                workflow,         # Steps 4-6: Strands workflow tool
            ],
            plugins=[
                TTOWorkflowSteering(model=llm_model),  # Pipeline enforcement
            ],
        )

        # Wrap the Strands agent with the AG-UI adapter. The adapter keeps one
        # agent instance per AG-UI thread_id so multi-turn conversations work
        # without leaking state between users.
        agui_agent = StrandsAgent(
            agent=tto_agent,
            name=settings.agent_id,
            description="Validates a ServiceNow project task's TTO checklist end-to-end.",
        )

        app = create_strands_app(agui_agent, settings.agent_path, "/ping")

        log.info(
            "TTO Steered Workflow Agent ready: tools=[%d MCP tools from %s, "
            "build_task_list, workflow], plugins=[TTOWorkflowSteering with LedgerProvider]",
            len(all_mcp_tools),
            ", ".join(f"{s}({len(tools)})" for s, tools in tools_by_source.items()),
        )
        log.info(
            "Serving AG-UI on %s:%d (POST %s, GET /ping), agent_id=%s",
            settings.host, settings.port, settings.agent_path, settings.agent_id,
        )
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
