import { A2uiMessageSchema, type ActionPayload } from "@a2ui/web_core/v0_9";
import { z } from "zod";

const AGENT_URL = import.meta.env.VITE_PRAXIS_AGENT_URL ?? "http://127.0.0.1:8000";

export const MODES = ["conversation", "file", "code"] as const;
export type Mode = (typeof MODES)[number];

// One line of the agent's NDJSON response. See agent/src/praxis_agent/server.py.
const AgentEventSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("call"),
    model: z.string(),
    destination: z.enum(["local", "cloud"]),
  }),
  z.object({ type: z.literal("status"), text: z.string() }),
  z.object({ type: z.literal("mode"), mode: z.enum(MODES) }),
  z.object({ type: z.literal("a2ui"), message: A2uiMessageSchema }),
  z.object({ type: z.literal("error"), message: z.string() }),
]);
export type AgentEvent = z.infer<typeof AgentEventSchema>;

export interface AgentClient {
  intent(sessionId: string, intent: string, signal: AbortSignal): AsyncIterable<AgentEvent>;
  action(sessionId: string, action: ActionPayload, signal: AbortSignal): AsyncIterable<AgentEvent>;
  /** Where a conversation left off: its last screen and its trail. Nothing if it is new. */
  resume(sessionId: string, signal: AbortSignal): AsyncIterable<AgentEvent>;
}

export const httpAgent: AgentClient = {
  intent: (sessionId, intent, signal) => post("/intent", { intent }, sessionId, signal),
  resume: (sessionId, signal) => post("/resume", {}, sessionId, signal),
  action: (sessionId, action, signal) => {
    const { name, surfaceId, sourceComponentId, timestamp, context } = action;
    const message = {
      version: "v0.9",
      action: { name, surfaceId, sourceComponentId, timestamp, context },
    };
    return post("/action", message, sessionId, signal);
  },
};

async function* post(
  path: string,
  body: unknown,
  sessionId: string,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(AGENT_URL + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Praxis-Session": sessionId },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`The agent answered with HTTP ${response.status}.`);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) yield AgentEventSchema.parse(JSON.parse(line));
    }
  }
  if (buffer.trim()) yield AgentEventSchema.parse(JSON.parse(buffer));
}
