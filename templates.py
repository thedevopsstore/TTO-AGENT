"""Static TTO task templates — one entry per checklist item.

Each entry is consumed by `build_tto_task_list`:
  * `system_prompt` is `.format(**kwargs)`-substituted with the values the outer
    LLM extracted from the ServiceNow work note.
  * `tools` lists the exact MCP tool names available to the task. The Strands
    `workflow` tool filters the parent agent's tools to only those listed.
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
        "tools": ["servicenow_ci_details"],
        "priority": 5,
        "system_prompt": (
            "You are a TTO verification agent. Verify that the Business Application "
            "CI ID '{business_application_ci_id}' exists in ServiceNow.\n\n"
            "Tool: servicenow_ci_details\n\n"
            "Steps:\n"
            "1. Call servicenow_ci_details with ci_id='{business_application_ci_id}'.\n"
            "2. Confirm the CI exists, is active, and has classification 'Business Application'.\n"
            "3. Record CI name, class, operational status, owner, AND the UAI from the "
            "response as evidence (downstream tasks depend on this UAI)."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_uai",
        "description": "Verify the UAI from the checklist matches the UAI on the Business Application CI.",
        "dependencies": ["verify_business_app_ci"],
        "tools": [],  # No tools needed — uses parent task's CI details output
        "priority": 4,
        "system_prompt": (
            "You are a TTO verification agent. Verify that the UAI from the checklist "
            "matches the UAI on the Business Application CI.\n\n"
            "Context:\n"
            "- Checklist UAI: '{uai}'\n"
            "- Business Application CI: '{business_application_ci_id}'\n"
            "- The parent task (verify_business_app_ci) already called servicenow_ci_details "
            "which returned the UAI registered on the CI.\n\n"
            "Steps:\n"
            "1. Compare the checklist UAI '{uai}' with the UAI from the parent task's CI details.\n"
            "2. PASS if they match; FAIL if they differ or the CI has no UAI.\n"
            "3. Record both values and whether they match as evidence."
            + RESULT_FORMAT
        ),
    },
    {
        "task_id": "verify_app_environment_ci",
        "description": "Verify the Application Environment CI exists and has the Business Application CI in its relationships.",
        "dependencies": ["verify_business_app_ci"],
        "tools": ["servicenow_ci_details"],
        "priority": 3,
        "system_prompt": (
            "You are a TTO verification agent. Verify that the Application Environment "
            "CI '{application_environment_ci}' exists and has the Business Application CI "
            "in its relationships.\n\n"
            "Context:\n"
            "- Application Environment CI from checklist: '{application_environment_ci}'\n"
            "- Business Application CI ID: '{business_application_ci_id}'\n"
            "- The parent task (verify_business_app_ci) called servicenow_ci_details and its "
            "output contains the Business Application CI name — use this name to verify the relationship.\n\n"
            "Tool: servicenow_ci_details\n\n"
            "Steps:\n"
            "1. Get the Business Application CI name from the parent task's servicenow_ci_details output.\n"
            "2. Call servicenow_ci_details with ci_id='{application_environment_ci}'.\n"
            "3. Look at the relationships returned for the Application Environment CI.\n"
            "4. Check if the Business Application CI name appears in those relationships.\n"
            "5. PASS if the Business Application CI name is found in the Application Environment CI's relationships.\n"
            "6. FAIL if it is not found or the relationship does not exist.\n"
            "7. Record the Application Environment CI name, the relationship type, and the "
            "Business Application CI name as evidence."
            + RESULT_FORMAT
        ),
    },
    # MVP1: Box verification disabled — uncomment to enable
    # {
    #     "task_id": "verify_box_link",
    #     "description": "Verify the Box link is reachable and contains handover artifacts.",
    #     "dependencies": [],
    #     "tools": ["box_list_folder_items"],
    #     "priority": 2,
    #     "system_prompt": (
    #         "You are a TTO verification agent. Verify the Box folder at '{box_link}' "
    #         "for project '{business_application_ci_id}'.\n\n"
    #         "Tool: box_list_folder_items\n\n"
    #         "Steps:\n"
    #         "1. Call box_list_folder_items with the folder URL '{box_link}'.\n"
    #         "2. Confirm the folder exists and contains handover artifacts "
    #         "(runbooks, design docs, or operational notes).\n"
    #         "3. Record folder name, item count, and notable filenames as evidence."
    #         + RESULT_FORMAT
    #     ),
    # },
    {
        "task_id": "verify_github_repo",
        "description": "Verify the GitHub repository exists and contains Infrastructure-as-Code.",
        "dependencies": [],
        "tools": ["github_get_repo_contents"],
        "priority": 3,
        "system_prompt": (
            "You are a TTO verification agent. Verify the GitHub repository at "
            "'{github_link}' for project '{business_application_ci_id}'.\n\n"
            "Tool: github_get_repo_contents\n\n"
            "Steps:\n"
            "1. Call github_get_repo_contents with the repo URL '{github_link}'.\n"
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
        "tools": ["confluence_get_page"],
        "priority": 3,
        "system_prompt": (
            "You are a TTO verification agent. Verify the Confluence page at "
            "'{confluence_link}' for project '{business_application_ci_id}'.\n\n"
            "Tool: confluence_get_page\n\n"
            "Steps:\n"
            "1. Call confluence_get_page with the page URL '{confluence_link}'.\n"
            "2. Confirm it contains architecture or design content "
            "(diagrams, prose, or attachments).\n"
            "3. Record page title, last update, and a brief summary as evidence."
            + RESULT_FORMAT
        ),
    },
    # MVP1: AWS cloud services validation disabled — uncomment to enable
    # {
    #     "task_id": "validate_cloud_services",
    #     "description": "Validate every cloud service listed in the checklist exists and is tagged correctly in AWS.",
    #     "dependencies": ["verify_business_app_ci", "verify_uai"],
    #     "tools": ["aws_resource_explorer_search", "aws_get_resource_tags"],
    #     "priority": 4,
    #     "system_prompt": (
    #         "You are a TTO verification agent. Validate the AWS resources backing "
    #         "UAI '{uai}' for project '{business_application_ci_id}'.\n"
    #         "The checklist declares these cloud services: {cloud_services_str}.\n\n"
    #         "Tools: aws_resource_explorer_search, aws_get_resource_tags\n\n"
    #         "Steps:\n"
    #         "1. Call aws_resource_explorer_search with uai='{uai}' for each declared service.\n"
    #         "2. For resources found, call aws_get_resource_tags to verify required tags: "
    #         "uai, cost-center, owner, environment.\n"
    #         "3. Flag any declared service with no matching resources, and any resource missing required tags.\n"
    #         "4. Record counts per service and a sample of resource identifiers as evidence."
    #         + RESULT_FORMAT
    #     ),
    # },
]
