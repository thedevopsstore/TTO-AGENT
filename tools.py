"""Deterministic task-list builder + the Pydantic schema for TTO field extraction.

TTOFields is used with Strands structured_output to extract checklist fields
from ServiceNow work notes. build_tto_task_list is called directly by Python
(not via LLM tool call) to expand templates into workflow tasks.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .templates import TASK_TEMPLATES


class TTOFields(BaseModel):
    """Fields extracted from the ServiceNow work note via structured_output.

    Required fields (business_application_ci_id, uai) have no default, which
    forces the LLM to extract real values from the work notes — Pydantic
    validation will reject empty or missing required fields.
    """

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


def build_tto_task_list(fields: TTOFields) -> list[dict]:
    """Expand TASK_TEMPLATES with extracted values.

    Pure Python, called directly from the server after the fetcher returns
    TTOFields. Output is the `tasks=` payload for `workflow(action="create")`.
    
    Tasks are skipped if their required fields are missing (None/empty).
    """
    values = {
        "business_application_ci_id": fields.business_application_ci_id,
        "uai": fields.uai,
        "box_link": fields.box_link,
        "github_link": fields.github_link,
        "confluence_link": fields.confluence_link,
        "application_environment_ci": fields.application_environment_ci,
        "cloud_services_str": (
            ", ".join(fields.cloud_services) if fields.cloud_services else None
        ),
    }

    # Map task_id -> required fields (if any field is None, skip the task)
    required_fields: dict[str, list[str]] = {
        "verify_app_environment_ci": ["application_environment_ci"],
        "verify_github_repo": ["github_link"],
        "verify_confluence_page": ["confluence_link"],
        # MVP1: Uncomment to enable Box and AWS validation
        # "verify_box_link": ["box_link"],
        # "validate_cloud_services": ["cloud_services_str"],
    }

    tasks: list[dict] = []
    for tpl in TASK_TEMPLATES:
        task_id = tpl["task_id"]
        
        # Check if required fields are present
        required = required_fields.get(task_id, [])
        if any(values.get(field) is None for field in required):
            continue  # Skip task — required field is missing
        
        # Build format values with fallbacks for optional fields
        format_values = {
            k: v if v is not None else "<not provided>"
            for k, v in values.items()
        }
        
        tasks.append(
            {
                "task_id": task_id,
                "description": tpl["description"].format(**format_values),
                "system_prompt": tpl["system_prompt"].format(**format_values),
                "dependencies": list(tpl.get("dependencies", [])),
                "priority": tpl.get("priority", 3),
                "tools": list(tpl.get("tools", [])),
            }
        )
    return tasks
