"""Static TTO task templates — one entry per checklist item.

Each entry is consumed by `build_tto_task_list`:
  * `system_prompt` is `.format(**kwargs)`-substituted with the values the outer
    LLM extracted from the ServiceNow work note.
  * `tool_sources` is expanded into concrete MCP tool names via the registry
    built at server startup, then placed on the task's `tools` field so the
    Strands `workflow` tool scopes each task to only the tools it needs.
"""

from __future__ import annotations

RESULT_FORMAT = """

Respond with exactly this shape:
STATUS: PASS | FAIL | NEEDS_REVIEW
EVIDENCE:
- <fact gathered from a tool call>
- <fact gathered from a tool call>
NOTES: <optional one-line rationale>
"""


TASK_TEMPLATES: list[dict] = [
    {
        "task_id": "verify_business_app_ci",
        "description": "Verify the Business Application CI exists and is active in ServiceNow CMDB.",
        "dependencies": [],
        "tool_sources": ["servicenow"],
        "priority": 5,
        "system_prompt": (
            "You are a TTO verification agent. Verify that the Business Application "
            "CI ID '{business_application_ci_id}' exists in ServiceNow.\n"
            "Steps:\n"
            "1. Query the ServiceNow CMDB for the Business Application CI.\n"
            "2. Confirm it exists, is active, and has classification 'Business Application'.\n"
            "3. Record CI name, class, operational status, and owner as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_uai",
        "description": "Verify the UAI is tagged with the Business Application CI.",
        "dependencies": ["verify_business_app_ci"],
        "tool_sources": ["servicenow"],
        "priority": 4,
        "system_prompt": (
            "You are a TTO verification agent. Verify that UAI '{uai}' is registered "
            "in ServiceNow and tagged with Business Application CI '{business_application_ci_id}'.\n"
            "Steps:\n"
            "1. Query ServiceNow for the UAI record.\n"
            "2. Confirm the UAI is active and references the Business Application CI above.\n"
            "3. Record UAI name, owner, and the linkage as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_app_environment_ci",
        "description": "Verify the Application Environment CI exists and links to the Business Application CI.",
        "dependencies": ["verify_business_app_ci"],
        "tool_sources": ["servicenow"],
        "priority": 3,
        "system_prompt": (
            "You are a TTO verification agent. Verify that the Application Environment "
            "CI '{application_environment_ci}' exists in ServiceNow and is related to "
            "Business Application CI '{business_application_ci_id}'.\n"
            "Steps:\n"
            "1. Query ServiceNow for the Application Environment CI.\n"
            "2. Confirm it is active and linked to the Business Application CI above.\n"
            "3. Record environment name, type (prod/non-prod), and relationship as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_box_link",
        "description": "Verify the Box link is reachable and contains handover artifacts.",
        "dependencies": [],
        "tool_sources": ["box"],
        "priority": 2,
        "system_prompt": (
            "You are a TTO verification agent. Verify the Box folder at '{box_link}' "
            "for project '{business_application_ci_id}'.\n"
            "Steps:\n"
            "1. Resolve the Box link and list its contents.\n"
            "2. Confirm the folder exists and contains handover artifacts "
            "(runbooks, design docs, or operational notes).\n"
            "3. Record folder name, item count, and notable filenames as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_github_repo",
        "description": "Verify the GitHub repository exists and contains Infrastructure-as-Code.",
        "dependencies": [],
        "tool_sources": ["github"],
        "priority": 3,
        "system_prompt": (
            "You are a TTO verification agent. Verify the GitHub repository at "
            "'{github_link}' for project '{business_application_ci_id}'.\n"
            "Steps:\n"
            "1. Resolve the repo and list its top-level contents.\n"
            "2. Determine whether it contains IaC (Terraform, CloudFormation, Bicep, "
            "Pulumi) and which stack.\n"
            "3. Record repo name, default branch, and IaC file locations as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_confluence_page",
        "description": "Verify the Confluence design page exists and contains architecture content.",
        "dependencies": [],
        "tool_sources": ["confluence"],
        "priority": 3,
        "system_prompt": (
            "You are a TTO verification agent. Verify the Confluence page at "
            "'{confluence_link}' for project '{business_application_ci_id}'.\n"
            "Steps:\n"
            "1. Resolve and fetch the Confluence page.\n"
            "2. Confirm it contains architecture or design content "
            "(diagrams, prose, or attachments).\n"
            "3. Record page title, last update, and a brief summary as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "validate_cloud_services",
        "description": "Validate every cloud service listed in the checklist exists and is tagged correctly in AWS.",
        "dependencies": ["verify_business_app_ci", "verify_uai"],
        "tool_sources": ["aws"],
        "priority": 4,
        "system_prompt": (
            "You are a TTO verification agent. Validate the AWS resources backing "
            "UAI '{uai}' for project '{business_application_ci_id}'.\n"
            "The checklist declares these cloud services: {cloud_services_str}.\n"
            "Steps:\n"
            "1. For each declared service, list the deployed resources filtered by the UAI tag.\n"
            "2. Verify each resource carries required tags: uai, cost-center, owner, environment.\n"
            "3. Flag any declared service with no matching resources, and any resource missing required tags.\n"
            "4. Record counts per service and a sample of resource identifiers as evidence."
            + RESULT_FORMAT
        ),
    },
]
