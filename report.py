"""Formatting and consolidation utilities for TTO workflow results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any


CHECKLIST_ITEMS: dict[int, str] = {
    1: "Business Application CI ID",
    2: "UAI",
    3: "Box Link",
    4: "Confluence Link",
    5: "GitHub Link",
    6: "Architecture Diagram",
    13: "Build Alignment to Baseline Configurations",
    14: "Approved IaC Templates in Use",
}

TASK_TO_ITEMS: dict[str, list[int]] = {
    "snow_validation": [1, 2],
    "confluence_validation": [3, 4, 6],
    "github_validation": [5, 13, 14],
}


@dataclass
class ItemResult:
    item_no: int
    name: str
    status: str = "NA"
    evidence: str = "No evidence returned."
    action: str = ""


def _normalize_status(raw: str | None) -> str:
    status = (raw or "").strip().upper()
    if status in {"PASS", "FAIL", "NA", "ERROR"}:
        return status
    return "NA"


def _extract_task_objects(payload: Any) -> dict[str, dict[str, Any]]:
    """Best-effort extraction of task objects from workflow status payload."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}

    if not isinstance(payload, dict):
        return {}

    # Common shape: {"tasks": [{"task_id": "...", ...}, ...]}
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        out: dict[str, dict[str, Any]] = {}
        for task in tasks:
            if isinstance(task, dict) and isinstance(task.get("task_id"), str):
                out[task["task_id"]] = task
        if out:
            return out

    # Alternate shape: {"tasks": {"task_id": {...}}}
    if isinstance(tasks, dict):
        return {k: v for k, v in tasks.items() if isinstance(v, dict)}

    # Fallback: maybe payload itself is keyed by task ids.
    maybe_tasks = {
        key: value
        for key, value in payload.items()
        if key in TASK_TO_ITEMS and isinstance(value, dict)
    }
    return maybe_tasks


def _task_output_text(task_obj: dict[str, Any]) -> str:
    """Extract textual task output from flexible status structures."""
    for key in ("result", "output", "response", "content", "message"):
        value = task_obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            for nested_key in ("content", "text", "message"):
                nested_val = value.get(nested_key)
                if isinstance(nested_val, str) and nested_val.strip():
                    return nested_val
        if isinstance(value, list):
            joined = "\n".join(str(part) for part in value if isinstance(part, (str, int, float)))
            if joined.strip():
                return joined
    return ""


def _parse_item_blocks(text: str) -> dict[int, ItemResult]:
    """Parse item-level PASS/FAIL/NA blocks from LLM task outputs."""
    matches = list(
        re.finditer(
            r"(?ims)\bitem\s*(\d+)\b.*?\bstatus\s*[:\-]\s*(PASS|FAIL|NA|ERROR)\b(.*?)(?=\n\s*item\s*\d+\b|\Z)",
            text,
        )
    )
    parsed: dict[int, ItemResult] = {}
    for m in matches:
        item_no = int(m.group(1))
        status = _normalize_status(m.group(2))
        tail = m.group(3)

        ev_match = re.search(r"(?im)^\s*evidence\s*[:\-]\s*(.+)$", tail)
        action_match = re.search(r"(?im)^\s*action\s*[:\-]\s*(.+)$", tail)

        evidence = ev_match.group(1).strip() if ev_match else "Evidence not explicitly provided."
        action = action_match.group(1).strip() if action_match else ""
        parsed[item_no] = ItemResult(
            item_no=item_no,
            name=CHECKLIST_ITEMS.get(item_no, f"Item {item_no}"),
            status=status,
            evidence=evidence,
            action=action,
        )
    return parsed


def consolidate_item_results(
    workflow_status: Any,
    task_failures: dict[str, str] | None = None,
) -> dict[int, ItemResult]:
    """Build normalized item results from workflow status and task failures."""
    task_failures = task_failures or {}
    item_results = {
        item_no: ItemResult(item_no=item_no, name=name)
        for item_no, name in CHECKLIST_ITEMS.items()
    }

    task_objects = _extract_task_objects(workflow_status)
    for task_id, item_numbers in TASK_TO_ITEMS.items():
        task_obj = task_objects.get(task_id, {})
        task_status = _normalize_status(str(task_obj.get("status", "")))
        output_text = _task_output_text(task_obj)
        parsed_items = _parse_item_blocks(output_text) if output_text else {}

        for item_no in item_numbers:
            if item_no in parsed_items:
                item_results[item_no] = parsed_items[item_no]
                continue

            if task_id in task_failures:
                item_results[item_no] = ItemResult(
                    item_no=item_no,
                    name=CHECKLIST_ITEMS[item_no],
                    status="ERROR",
                    evidence=task_failures[task_id],
                    action=f"Investigate and rerun {task_id}.",
                )
                continue

            if task_status in {"FAIL", "ERROR"}:
                item_results[item_no] = ItemResult(
                    item_no=item_no,
                    name=CHECKLIST_ITEMS[item_no],
                    status="ERROR",
                    evidence=output_text or f"{task_id} failed without detailed output.",
                    action=f"Investigate and rerun {task_id}.",
                )
                continue

            if task_status == "PASS":
                item_results[item_no].status = "PASS"
                item_results[item_no].evidence = output_text or "Task reported PASS."
                item_results[item_no].action = ""
            else:
                item_results[item_no].status = "NA"
                item_results[item_no].evidence = output_text or "No item-specific evidence returned."

    return item_results


def _overall_status(item_results: dict[int, ItemResult]) -> str:
    statuses = [r.status for r in item_results.values()]
    if all(s == "PASS" for s in statuses):
        return "PASS"
    if any(s in {"FAIL", "ERROR"} for s in statuses):
        return "FAIL"
    return "PARTIAL"


def format_report(project_id: str, item_results: dict[int, ItemResult], run_date: datetime | None = None) -> str:
    """Format the final report in the requested structure."""
    run_date = run_date or datetime.now()
    overall = _overall_status(item_results)
    ordered_items = [1, 2, 3, 4, 5, 6, 13, 14]
    lines: list[str] = [
        "TTO VALIDATION REPORT",
        f"Project: {project_id}",
        f"Run Date: {run_date:%Y-%m-%d}",
        f"Overall Status: {overall}",
        "",
        "─────────────────────────────────────────",
        "",
    ]

    for item_no in ordered_items:
        result = item_results[item_no]
        action = result.action.strip() if result.action.strip() else "—"
        lines.extend(
            [
                f"ITEM {item_no:<2} | {result.name:<31} | {result.status}",
                f"Evidence: {result.evidence}",
                f"Action:   {action}",
                "",
            ]
        )

    passed = [n for n, r in item_results.items() if r.status == "PASS"]
    failed = [n for n, r in item_results.items() if r.status in {"FAIL", "ERROR"}]
    na_items = [n for n, r in item_results.items() if r.status == "NA"]

    lines.extend(
        [
            "─────────────────────────────────────────",
            f"Summary: {len(passed)} of {len(item_results)} items passed",
            f"Failed items: {failed if failed else '[]'}",
            f"NA items: {na_items if na_items else '[]'}",
        ]
    )
    return "\n".join(lines)
