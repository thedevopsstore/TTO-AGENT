"use client";

import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-core/v2";

const runtimeUrl =
  process.env.NEXT_PUBLIC_COPILOT_RUNTIME_URL ?? "/api/copilotkit";
const agentId = process.env.NEXT_PUBLIC_AGENT_ID ?? "tto-validator";

export default function Home() {
  return (
    <CopilotKit runtimeUrl={runtimeUrl} agent={agentId} showDevConsole={false}>
      <main
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "24px",
        }}
      >
        <div
          style={{
            width: "100%",
            maxWidth: "1100px",
            height: "90vh",
            borderRadius: "16px",
            border: "1px solid var(--surface-border, rgba(0, 180, 216, 0.25))",
            boxShadow:
              "0 0 0 1px rgba(0, 0, 0, 0.35), 0 24px 48px rgba(0, 0, 0, 0.45)",
            overflow: "hidden",
          }}
        >
          <CopilotChat
            agentId={agentId}
            className="h-full rounded-2xl max-w-6xl mx-auto"
            labels={{
              welcomeMessageText:
                "Hey — I’m here to help you with TTO checklist validation.",
            }}
          />
        </div>
      </main>
    </CopilotKit>
  );
}
