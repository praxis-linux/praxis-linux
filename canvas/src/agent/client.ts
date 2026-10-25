import { A2uiMessageSchema, type ActionPayload } from "@a2ui/web_core/v0_9";
import { z } from "zod";

const AGENT_URL = import.meta.env.VITE_PRAXIS_AGENT_URL ?? "http://127.0.0.1:7729";

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

/** Why a request to the agent failed, in words fit to show the user. */
export class AgentError extends Error {}

const EVENT_TYPES = new Set<string>(
  AgentEventSchema.options.map((option) => option.shape.type.value),
);

async function* post(
  path: string,
  body: unknown,
  sessionId: string,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  let response: Response;
  try {
    response = await fetch(AGENT_URL + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Praxis-Session": sessionId },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new AgentError("Could not reach the agent. Is it running?");
  }
  if (!response.ok || !response.body) {
    throw new AgentError(`The agent answered with HTTP ${response.status}.`);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  try {
    let buffer = "";
    for (;;) {
      let chunk: ReadableStreamReadResult<string>;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (signal.aborted) throw error;
        throw new AgentError("The connection to the agent was lost.");
      }
      if (chunk.done) break;
      buffer += chunk.value;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const event = parseEvent(line);
        if (event) yield event;
      }
    }
    const event = parseEvent(buffer);
    if (event) yield event;
  } finally {
    // Stops the agent's work too when the turn ends early: superseded, or unreadable.
    void reader.cancel().catch(() => {});
  }
}

/** One line of the stream. Blank lines and event types this Canvas does not know are skipped. */
function parseEvent(line: string): AgentEvent | null {
  if (!line.trim()) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    throw new AgentError("The agent sent something the Canvas could not read.");
  }
  const type = (raw as { type?: unknown } | null)?.type;
  if (typeof type === "string" && !EVENT_TYPES.has(type)) return null;
  const event = AgentEventSchema.safeParse(raw);
  if (!event.success) {
    throw new AgentError("The agent sent something the Canvas could not read.");
  }
  return event.data;
}
