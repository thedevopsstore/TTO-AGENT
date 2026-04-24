# TTO UI (CopilotKit + Next.js)

Minimal Next.js frontend for chatting with an AG-UI compatible agent.

## Architecture

```
Browser (CopilotKit <CopilotChat>)
   │  POST /api/copilotkit
   ▼
Next.js route (src/app/api/copilotkit/route.ts)
   │  CopilotRuntime → HttpAgent
   ▼
AG-UI backend (AGENT_URL)
```

The Next.js API route proxies browser requests to the AG-UI backend via
`CopilotRuntime` + `HttpAgent` from `@ag-ui/client`. The browser never talks to
the AG-UI agent directly, so CORS and auth can be handled on the server.

## Prerequisites

- Node.js 20+ (or newer)
- A running AG-UI backend endpoint

## Configure

```bash
cp .env.example .env.local
```

Set:

- `AGENT_URL` — server-side URL of the AG-UI backend (for example `http://localhost:8000/`)
- `AGENT_ID` — key used in `CopilotRuntime.agents`
- `NEXT_PUBLIC_COPILOT_RUNTIME_URL` — keep as `/api/copilotkit` unless you front the route somewhere else
- `NEXT_PUBLIC_AGENT_ID` — must match `AGENT_ID` above

## Run

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

To sanity-check the proxy:

```bash
curl http://localhost:3000/api/copilotkit
```

You should see JSON with `status: "ok"` and whether the AG-UI backend is reachable.

## Files of interest

- `src/app/api/copilotkit/route.ts` — CopilotRuntime + `HttpAgent` proxy
- `src/app/page.tsx` — CopilotKit provider and `CopilotChat`
- `.env.example` — environment template
