import {
  MessageProcessor,
  STRICT_VALIDATION,
  type A2uiMessage,
  type SurfaceModel,
} from "@a2ui/web_core/v0_9";
import type { ReactComponentImplementation } from "@a2ui/react/v0_9";
import { praxisCatalog } from "./a2ui/catalog";
import { CONTEXT_SURFACE, WELCOME_MESSAGES, WORKSPACE_SURFACE } from "./a2ui/welcome";
import { httpAgent, type AgentClient, type AgentEvent, type Mode } from "./agent/client";

export type Surface = SurfaceModel<ReactComponentImplementation>;

export interface CanvasState {
  mode: Mode;
  /** The model serving the current or most recent turn. */
  call: { model: string; destination: "local" | "cloud" } | null;
  busy: boolean;
  error: string | null;
  workspace: Surface | undefined;
  context: Surface | undefined;
}

/**
 * One Canvas's conversation with the agent: owns the A2UI state, sends intents
 * and UI actions to the agent, and applies what comes back.
 */
export class CanvasSession {
  readonly id = crypto.randomUUID();
  private readonly processor: MessageProcessor<ReactComponentImplementation>;
  private readonly listeners = new Set<() => void>();
  private turn: AbortController | undefined;
  private state: CanvasState = {
    mode: "conversation",
    call: null,
    busy: false,
    error: null,
    workspace: undefined,
    context: undefined,
  };

  constructor(private readonly agent: AgentClient = httpAgent) {
    // Strict validation rejects components outside the catalog, dangling
    // references and rootless trees instead of rendering what it can.
    this.processor = new MessageProcessor(
      [praxisCatalog],
      (action) => this.run((signal) => this.agent.action(this.id, action, signal)),
      { validationConfig: STRICT_VALIDATION },
    );
    this.processor.onSurfaceCreated((surface) => {
      surface.onError.subscribe((error) => this.set({ error: error.message }));
      this.syncSurfaces();
    });
    this.processor.onSurfaceDeleted(() => this.syncSurfaces());
    this.apply(WELCOME_MESSAGES);
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = () => this.state;

  sendIntent(intent: string): Promise<void> {
    return this.run((signal) => this.agent.intent(this.id, intent, signal));
  }

  /** Runs one turn. A new turn supersedes the one in flight. */
  private async run(events: (signal: AbortSignal) => AsyncIterable<AgentEvent>): Promise<void> {
    this.turn?.abort();
    const turn = (this.turn = new AbortController());
    this.set({ busy: true, error: null });
    try {
      for await (const event of events(turn.signal)) {
        if (turn.signal.aborted) return;
        this.handle(event);
      }
    } catch (error) {
      if (!turn.signal.aborted) this.set({ error: describe(error) });
    } finally {
      if (this.turn === turn) this.set({ busy: false });
    }
  }

  private handle(event: AgentEvent) {
    switch (event.type) {
      case "call":
        this.set({ call: { model: event.model, destination: event.destination } });
        break;
      case "mode":
        this.set({ mode: event.mode });
        break;
      case "a2ui":
        this.apply([event.message]);
        break;
      case "error":
        this.set({ error: event.message });
        break;
    }
  }

  private apply(messages: A2uiMessage[]) {
    try {
      this.processor.processMessages(messages);
    } catch (error) {
      // Later messages of a rejected screen fail too; the first failure is the cause.
      if (this.state.error) return;
      this.set({ error: `The agent sent UI the Canvas cannot show. ${describe(error)}` });
    }
  }

  private syncSurfaces() {
    this.set({
      workspace: this.processor.getSurface(WORKSPACE_SURFACE),
      context: this.processor.getSurface(CONTEXT_SURFACE),
    });
  }

  private set(patch: Partial<CanvasState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
}

function describe(error: unknown): string {
  if (error instanceof TypeError) return "Could not reach the agent. Is it running?";
  return error instanceof Error ? error.message : String(error);
}
