"""Custom tool: expand static templates + extracted values into workflow tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field
from strands import tool

from .templates import TASK_TEMPLATES

REGISTRY: dict[str, list[str]] = {}


class WorkflowTask(BaseModel):
    """Shape of one task inside a Strands workflow-tool `create` payload."""

    task_id: str = Field(..., description="Unique task identifier within the workflow.")
    description: str = Field(..., description="Human-readable task description.")
    system_prompt: str = Field(..., description="System prompt executed by the task's sub-agent.")
    dependencies: list[str] = Field(default_factory=list, description="task_ids this task depends on.")
    priority: int = Field(3, description="Scheduling priority 1-5 (higher = sooner).")
    tools: list[str] = Field(default_factory=list, description="Tool names (from parent registry) scoped to this task.")


class PlanOutput(BaseModel):
    """Structured output returned by the planner agent.

    The planner fetches the ServiceNow work note, extracts the TTO fields, and
    calls `build_tto_task_list`; it must return that tool's output verbatim
    under `tasks`, plus a deterministic `workflow_id`.
    """

    workflow_id: str = Field(
        ...,
        description="Deterministic workflow id, MUST equal 'tto-' + business_application_ci_id.",
    )
    tasks: list[WorkflowTask] = Field(
        ...,
        description="The exact list returned by build_tto_task_list, unchanged.",
    )


class TTOFields(BaseModel):
    """Fields the outer LLM must extract from the ServiceNow work note."""

    business_application_ci_id: str = Field(
        ...,
        description=(
            "ServiceNow CMDB sys_id or number identifying the Business Application CI. "
            "Example: '1101672345'."
        ),
    )
    uai: str = Field(
        ...,
        description="Unique Application Identifier tagged on the application, e.g. 'uai3071168'.",
    )
    box_link: str | None = Field(
        None,
        description="URL to the Box folder holding handover artifacts. Null if not present.",
    )
    github_link: str | None = Field(
        None,
        description="URL to the GitHub repository (typically the IaC repo). Null if not present.",
    )
    confluence_link: str | None = Field(
        None,
        description="URL to the Confluence page with architecture/design documentation.",
    )
    application_environment_ci: str | None = Field(
        None,
        description="ServiceNow CI identifier for the Application Environment record.",
    )
    cloud_services: list[str] | None = Field(
        None,
        description=(
            "List of cloud services declared in the checklist, e.g. "
            "['ALB', 'ECR', 'ECS', 'Postgres', 'KMS', 'S3', 'IAM']. "
            "Parse from a comma-separated line in the work note."
        ),
    )


@tool
def build_tto_task_list(fields: TTOFields) -> list[dict]:
    """Build the Strands `workflow` task list for a TTO checklist validation.

    Expands every template in TASK_TEMPLATES by substituting the values extracted
    from the ServiceNow work note, and resolves each template's `tool_sources`
    (e.g. ["servicenow", "aws"]) into the concrete MCP tool names registered on
    the parent agent. The returned list is passed directly to
    `workflow(action="create", tasks=<this>)`.
    """
    values = {
        "business_application_ci_id": fields.business_application_ci_id,
        "uai": fields.uai,
        "box_link": fields.box_link or "<missing>",
        "github_link": fields.github_link or "<missing>",
        "confluence_link": fields.confluence_link or "<missing>",
        "application_environment_ci": fields.application_environment_ci or "<missing>",
        "cloud_services_str": (
            ", ".join(fields.cloud_services) if fields.cloud_services else "<none>"
        ),
    }

    tasks: list[dict] = []
    for tpl in TASK_TEMPLATES:
        tool_names: list[str] = []
        for source in tpl["tool_sources"]:
            tool_names.extend(REGISTRY.get(source, []))

        tasks.append(
            {
                "task_id": tpl["task_id"],
                "description": tpl["description"].format(**values),
                "system_prompt": tpl["system_prompt"].format(**values),
                "dependencies": list(tpl.get("dependencies", [])),
                "priority": tpl.get("priority", 3),
                "tools": tool_names,
            }
        )
    return tasks
