import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { NextRequest, NextResponse } from "next/server";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8080/invocations";
const AGENT_ID = process.env.AGENT_ID || "tto-validator";
const AGENT_PING_URL =
  process.env.AGENT_PING_URL || new URL("/ping", AGENT_URL).toString();
const ALLOWED_ORIGINS =
  process.env.COPILOTKIT_ALLOWED_ORIGINS?.split(",")
    .map((origin) => origin.trim())
    .filter(Boolean) ?? [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
  ];
const ALLOWED_ORIGIN_SET = new Set(ALLOWED_ORIGINS);

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

function resolveAllowedOrigin(requestOrigin: string): string | null {
  try {
    const parsed = new URL(requestOrigin);
    const normalized = `${parsed.protocol}//${parsed.host}`;
    if (ALLOWED_ORIGIN_SET.has(normalized)) {
      return requestOrigin;
    }
    if (isLoopbackHost(parsed.hostname)) {
      // Allow localhost/loopback across ports for local dev.
      return requestOrigin;
    }
  } catch {
    // Ignore malformed origin and treat as blocked.
  }
  return null;
}

const serviceAdapter = new ExperimentalEmptyAdapter();

const runtime = new CopilotRuntime({
  agents: {
    [AGENT_ID]: new HttpAgent({ url: AGENT_URL }),
  },
});

export const POST = async (req: NextRequest) => {
  const startedAt = Date.now();
  const requestId = crypto.randomUUID();
  try {
    console.info(
      `[copilotkit/route] request_start id=${requestId} method=${req.method} path=${req.nextUrl.pathname} origin=${req.headers.get("origin") ?? "none"}`,
    );

    const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
      runtime,
      serviceAdapter,
      endpoint: "/api/copilotkit",
      cors: {
        origin: (requestOrigin: string) => {
          const allowed = resolveAllowedOrigin(requestOrigin);
          if (!allowed) {
            console.warn(
              `[copilotkit/route] blocked_origin origin=${requestOrigin} allowlist=${ALLOWED_ORIGINS.join(",")}`,
            );
          }
          return allowed;
        },
      },
    });

    const res = await handleRequest(req);
    const durationMs = Date.now() - startedAt;
    const response = new NextResponse(res.body, {
      status: res.status,
      statusText: res.statusText,
      headers: res.headers,
    });
    response.headers.set("x-request-id", requestId);

    console.info(
      `[copilotkit/route] request_end id=${requestId} status=${res.status} duration_ms=${durationMs}`,
    );
    return response;
  } catch (error: unknown) {
    const err = error as Error;
    const durationMs = Date.now() - startedAt;
    console.error(
      `[copilotkit/route] request_error id=${requestId} duration_ms=${durationMs} error=${err.message}`,
    );
    return NextResponse.json(
      { error: err.message, stack: err.stack },
      { status: 500 },
    );
  }
};

export const GET = async () => {
  let agentStatus = "unknown";
  try {
    const res = await fetch(AGENT_PING_URL, {
      signal: AbortSignal.timeout(3000),
    });
    agentStatus = res.ok ? "reachable" : `error (${res.status})`;
  } catch (e: unknown) {
    agentStatus = `unreachable (${(e as Error).message})`;
  }

  return NextResponse.json({
    status: "ok",
    agentUrl: AGENT_URL,
    pingUrl: AGENT_PING_URL,
    agentId: AGENT_ID,
    agentStatus,
  });
};
