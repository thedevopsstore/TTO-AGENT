## TTO Checklist A2A Agent

An A2A-exposed Strands agent that validates a ServiceNow project's
Transition-To-Operations (TTO) checklist end-to-end.

### How it works

```
Primary agent ──A2A──▶ Outer Strands Agent
                         │
                         │ 1. Fetch work notes via ServiceNow MCP
                         │ 2. Extract fields (CI, UAI, box/github/confluence, cloud services)
                         │ 3. build_tto_task_list(...)   — template + values → task list
                         │ 4. workflow(create) → workflow(start) → workflow(status)
                         │      └─ each task runs in parallel with ONLY its scoped MCP tools
                         ▼ 5. Report
```

The outer agent has exactly three kinds of tools:
- ServiceNow MCP tools (used directly by the outer LLM).
- `build_tto_task_list` — pure-Python template expander.
- `workflow` — Strands multi-agent tool that runs tasks in parallel with
  per-task tool scoping.

Other MCP servers (Box, Confluence, GitHub, AWS, Azure) are registered on the
parent agent so the `workflow` tool can route their tools into the tasks that
need them, but the outer LLM never invokes them directly.

### Install & run

```bash
cd agent
pip install -e .

cp config/mcp.env.example .env   # edit URLs + TTO_MODEL
export $(grep -v '^#' .env | xargs)

checklist-agent          # or: python -m checklist_agent
```

The agent serves A2A on `TTO_HOST:TTO_PORT` (default `127.0.0.1:9000`).

### Package layout

```
src/checklist_agent/
  settings.py        pydantic-settings (MCP URLs, host/port, model)
  mcp_sessions.py    open all MCP clients once; source -> tool names registry
  templates.py       TASK_TEMPLATES + RESULT_FORMAT
  tools.py           @tool build_tto_task_list
  server.py          outer Agent + A2AServer wiring
  __main__.py        python -m checklist_agent entry
```

### Adding a new checklist item

Append an entry to `TASK_TEMPLATES` in `templates.py`:

```python
{
    "task_id": "verify_new_thing",
    "description": "...",
    "dependencies": ["verify_business_app_ci"],
    "tool_sources": ["servicenow"],   # names from settings.sources
    "priority": 3,
    "system_prompt": "You are a TTO verification agent ... {business_application_ci_id} ..." + RESULT_FORMAT,
}
```

If the item needs a new extracted field, add it to `build_tto_task_list`'s
signature and to step 2 of `OUTER_PROMPT` in `server.py`.
