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
  /** What the agent is doing right now, such as a search, while it works. */
  status: string | null;
  error: string | null;
  workspace: Surface | undefined;
  context: Surface | undefined;
}

// Where the current conversation's id is kept, so a reload continues it.
const CONVERSATION_KEY = "praxis.conversation";

const INITIAL_STATE: CanvasState = {
  mode: "conversation",
  call: null,
  busy: false,
  status: null,
  error: null,
  workspace: undefined,
  context: undefined,
};

/**
 * One Canvas's conversation with the agent: owns the A2UI state, sends intents
 * and UI actions to the agent, and applies what comes back.
 */
export class CanvasSession {
  private conversation: string;
  private readonly processor: MessageProcessor<ReactComponentImplementation>;
  private readonly listeners = new Set<() => void>();
  private turn: AbortController | undefined;
  private state: CanvasState = INITIAL_STATE;

  constructor(
    private readonly agent: AgentClient = httpAgent,
    private readonly storage: Pick<Storage, "getItem" | "setItem"> | null = browserStorage(),
  ) {
    this.conversation = this.storedConversation() ?? this.startConversation();
    // Strict validation rejects components outside the catalog, dangling
    // references and rootless trees instead of rendering what it can.
    this.processor = new MessageProcessor(
      [praxisCatalog],
      (action) => this.run((signal) => this.agent.action(this.conversation, action, signal)),
      { validationConfig: STRICT_VALIDATION },
    );
    this.processor.onSurfaceCreated((surface) => {
      surface.onError.subscribe((error) => this.set({ error: error.message }));
      this.syncSurfaces();
    });
    this.processor.onSurfaceDeleted(() => this.syncSurfaces());
    this.apply(WELCOME_MESSAGES);
    // Pick up where this conversation left off. Without the agent, the welcome
    // screen stays, and nothing is reported until the user asks for something.
    void this.run((signal) => this.agent.resume(this.conversation, signal), { quiet: true });
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = () => this.state;

  /** The id the agent knows this conversation by. */
  get id(): string {
    return this.conversation;
  }

  sendIntent(intent: string): Promise<void> {
    return this.run((signal) => this.agent.intent(this.conversation, intent, signal));
  }

  /** Leaves this conversation, which the agent keeps, and starts an empty one. */
  newConversation() {
    this.turn?.abort();
    this.conversation = this.startConversation();
    this.apply([
      { version: "v0.9", deleteSurface: { surfaceId: WORKSPACE_SURFACE } },
      { version: "v0.9", deleteSurface: { surfaceId: CONTEXT_SURFACE } },
      ...WELCOME_MESSAGES,
    ]);
    this.set({ ...INITIAL_STATE, workspace: this.state.workspace, context: undefined });
  }

  /** Runs one turn. A new turn supersedes the one in flight. */
  private async run(
    events: (signal: AbortSignal) => AsyncIterable<AgentEvent>,
    { quiet = false } = {},
  ): Promise<void> {
    this.turn?.abort();
    const turn = (this.turn = new AbortController());
    this.set({ busy: true, status: null, error: null });
    try {
      for await (const event of events(turn.signal)) {
        if (turn.signal.aborted) return;
        this.handle(event);
      }
    } catch (error) {
      if (!turn.signal.aborted && !quiet) this.set({ error: describe(error) });
    } finally {
      if (this.turn === turn) this.set({ busy: false, status: null });
    }
  }

  private handle(event: AgentEvent) {
    switch (event.type) {
      case "call":
        this.set({ call: { model: event.model, destination: event.destination } });
        break;
      case "status":
        this.set({ status: event.text });
        break;
      case "mode":
        this.set({ mode: event.mode });
        break;
      case "a2ui":
        // Once the reply is being drawn, what the agent was doing is over.
        if (this.state.status) this.set({ status: null });
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

  private storedConversation(): string | null {
    try {
      return this.storage?.getItem(CONVERSATION_KEY) ?? null;
    } catch {
      return null;
    }
  }

  private startConversation(): string {
    const id = crypto.randomUUID();
    try {
      this.storage?.setItem(CONVERSATION_KEY, id);
    } catch {
      // Not kept: a reload starts a new conversation, and the old one stays with the agent.
    }
    return id;
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

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function describe(error: unknown): string {
  if (error instanceof TypeError) return "Could not reach the agent. Is it running?";
  return error instanceof Error ? error.message : String(error);
}
