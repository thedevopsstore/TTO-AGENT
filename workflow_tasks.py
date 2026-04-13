"""Workflow task definitions for TTO checklist validation."""

from __future__ import annotations

from typing import Any

from strands.tools.mcp import MCPClient


def build_tto_tasks(project_id: str, mcp_clients: dict[str, MCPClient]) -> list[dict[str, Any]]:
    """Create workflow tasks using direct MCP client mapping."""
    return [
        {
            "task_id": "snow_validation",
            "description": f"""
Validate TTO checklist items 1 and 2 for project {project_id}.

Item 1 - Business Application CI ID:
  Locate the ServiceNow project record for {project_id}.
  Verify that a Business Application CI ID exists and is valid in ServiceNow.
  Confirm the CI record exists under the Business Application CI class.

Item 2 - UAI:
  Verify the UAI is tagged against the Business Application CI ID found in Item 1.
  Confirm the UAI record exists and is linked correctly.

For each item return status (PASS/FAIL/NA), the exact value found as evidence,
and a recommended action if FAIL.
""".strip(),
            "system_prompt": "Focus on ServiceNow validation and provide concise evidence per item.",
            "priority": 5,
            "tools": [mcp_clients["servicenow"]],
        },
        {
            "task_id": "confluence_validation",
            "description": f"""
Validate TTO checklist items 3, 4, and 6 for project {project_id}.
The ServiceNow project record was already retrieved in snow_validation -
use the URLs and references found there.

Item 3 - Box Link:
  Retrieve the Box folder link from the ServiceNow project record.
  Verify the Box link exists and is accessible.
  Confirm it contains design documentation.

Item 4 - Confluence Link:
  Retrieve the Confluence page link from the ServiceNow project record.
  Verify the Confluence page exists and is accessible.
  Read the page content and confirm it contains component design documentation.

Item 6 - Architecture Diagram:
  On the same Confluence page found in Item 4, check for attachments.
  Look specifically for a draw.io file (.drawio or .xml) attached to the page.
  Verify the architecture diagram exists and is attached.

For each item return status (PASS/FAIL/NA), the URL or attachment name found
as evidence, and a recommended action if FAIL.
""".strip(),
            "system_prompt": "Validate links and architecture evidence via ServiceNow + Confluence records.",
            "priority": 4,
            "dependencies": ["snow_validation"],
            "tools": [mcp_clients["confluence"]],
        },
        {
            "task_id": "github_validation",
            "description": f"""
Validate TTO checklist items 5, 13, and 14 for project {project_id}.
The GitHub repository URL was retrieved in snow_validation - use that directly.

Item 5 - GitHub Link:
  Verify the GitHub repository exists and is accessible.
  Confirm the repository is not archived or empty.

Item 13 - Build Alignment to Baseline Configurations:
  Read the IaC files in the repository.
  Look in /terraform, /cloudformation, or /iac directories.
  Check whether the configuration follows standard baseline patterns.
  Flag any deviations from standard structure or naming conventions.

Item 14 - Approved IaC Templates in Use:
  In the same IaC files, check that only approved resource types and
  modules are referenced.
  Flag any custom, unapproved, or unknown resource types found.

For each item return status (PASS/FAIL/NA), specific file paths and
findings as evidence, and a recommended action if FAIL.
""".strip(),
            "system_prompt": "Validate GitHub repository and IaC compliance evidence with actionable findings.",
            "priority": 4,
            "dependencies": ["snow_validation"],
            "tools": [mcp_clients["github"]],
        },
    ]
