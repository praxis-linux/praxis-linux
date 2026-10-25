import {
  MessageProcessor,
  STRICT_VALIDATION,
  type A2uiMessage,
  type SurfaceModel,
} from "@a2ui/web_core/v0_9";
import type { ReactComponentImplementation } from "@a2ui/react/v0_9";
import { praxisCatalog } from "./a2ui/catalog";
import { CONTEXT_SURFACE, WELCOME_MESSAGES, WORKSPACE_SURFACE } from "./a2ui/welcome";
import {
  AgentError,
  httpAgent,
  type AgentClient,
  type AgentEvent,
  type Mode,
} from "./agent/client";

export type Surface = SurfaceModel<ReactComponentImplementation>;

export interface CanvasState {
  mode: Mode;
  /** The model serving the current or most recent turn. */
  call: { model: string; destination: "local" | "cloud" } | null;
  busy: boolean;
  /** What the agent is doing right now, such as a search, while it works. */
  status: string | null;
  error: string | null;
  /** A short-lived notice, such as a request going to a cloud model. */
  notice: { text: string; kind: "cloud" | "info" } | null;
  workspace: Surface | undefined;
  context: Surface | undefined;
}

// Where the current conversation's id is kept, so a reload continues it.
const CONVERSATION_KEY = "praxis.conversation";

// How long a notice stays up.
export const NOTICE_MS = 6000;

const INITIAL_STATE: CanvasState = {
  mode: "conversation",
  call: null,
  busy: false,
  status: null,
  error: null,
  notice: null,
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
  // Per surface: the messages that drew what it shows now, and the last complete
  // screen before that, to bring back if a new screen is rejected.
  private readonly drawing = new Map<string, A2uiMessage[]>();
  private readonly lastGood = new Map<string, A2uiMessage[]>();

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
    this.applyAll(WELCOME_MESSAGES);
    // Pick up where this conversation left off, once the agent is up. Without the
    // agent, the welcome screen stays, and nothing is reported until the user asks
    // for something.
    void this.agent.ready().then(() => {
      // Unless the user has asked for something meanwhile.
      if (!this.turn) {
        void this.run((signal) => this.agent.resume(this.conversation, signal), { quiet: true });
      }
    });
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
    this.applyAll([
      { version: "v0.9", deleteSurface: { surfaceId: WORKSPACE_SURFACE } },
      { version: "v0.9", deleteSurface: { surfaceId: CONTEXT_SURFACE } },
      ...WELCOME_MESSAGES,
    ]);
    this.set({ ...INITIAL_STATE, workspace: this.state.workspace, context: undefined });
  }

  /**
   * The kill switch: stop what the agent is doing, now. The Canvas drops the turn
   * at once; the agent is told to end every turn it is working on, in case this
   * Canvas is not the only one that started one.
   */
  async stop(): Promise<void> {
    this.turn?.abort();
    this.set({ busy: false, status: null });
    this.notify("Stopped.", "info");
    try {
      await this.agent.stop(this.conversation);
    } catch (error) {
      this.set({ error: describe(error) });
    }
  }

  /** Deletes this conversation, with what was read in it, and starts an empty one. */
  async forgetConversation(): Promise<void> {
    this.turn?.abort();
    try {
      await this.agent.forget(this.conversation);
    } catch (error) {
      this.set({ error: `The conversation was not deleted. ${describe(error)}` });
      return;
    }
    this.newConversation();
    this.notify("Conversation deleted.", "info");
  }

  private noticeTimer: ReturnType<typeof setTimeout> | undefined;

  private notify(text: string, kind: "cloud" | "info") {
    clearTimeout(this.noticeTimer);
    this.set({ notice: { text, kind } });
    this.noticeTimer = setTimeout(() => this.set({ notice: null }), NOTICE_MS);
  }

  /**
   * Runs one turn. A new turn supersedes the one in flight.
   *
   * A `quiet` turn reports nothing that goes wrong: used to resume on start, when
   * the user has not asked for anything yet.
   */
  private async run(
    events: (signal: AbortSignal) => AsyncIterable<AgentEvent>,
    { quiet = false } = {},
  ): Promise<void> {
    this.turn?.abort();
    const turn = (this.turn = new AbortController());
    // The model badge describes this turn only; a turn that calls no model shows none.
    this.set({ busy: true, status: null, error: null, call: null });
    const progress: Turn = { quiet, rejected: false };
    try {
      for await (const event of events(turn.signal)) {
        if (turn.signal.aborted) return;
        this.handle(event, progress);
      }
    } catch (error) {
      if (!turn.signal.aborted && !quiet) this.set({ error: describe(error) });
      // Whatever ended the turn early, the agent should stop working on it.
      turn.abort();
    } finally {
      if (this.turn === turn) this.set({ busy: false, status: null });
    }
  }

  private handle(event: AgentEvent, turn: Turn) {
    switch (event.type) {
      case "call":
        this.set({ call: { model: event.model, destination: event.destination } });
        // Nothing leaves the machine unannounced (ADR-001 §12). The audit log has it too.
        if (event.destination === "cloud") {
          this.notify(`This request is going to ${event.model}, a cloud model.`, "cloud");
        }
        break;
      case "status":
        this.set({ status: event.text });
        break;
      case "mode":
        this.set({ mode: event.mode });
        break;
      case "a2ui":
        // After the first rejected message, the rest of this turn's screen is not drawn.
        if (turn.rejected) break;
        // Once the reply is being drawn, what the agent was doing is over.
        if (this.state.status) this.set({ status: null });
        if (!this.apply(event.message)) {
          turn.rejected = true;
          if (!turn.quiet) this.set({ error: "The agent sent a screen the Canvas cannot show." });
        }
        break;
      case "error":
        if (!turn.quiet) this.set({ error: event.message });
        break;
    }
  }

  /**
   * Applies one A2UI message. Returns false if the Canvas rejected it.
   *
   * A screen is drawn by deleting its surface, creating it again and filling it in.
   * If the filling-in is rejected, the surface would be left empty; the screen it
   * showed before is drawn again instead.
   */
  private apply(message: A2uiMessage): boolean {
    const surfaceId = surfaceOf(message);
    if ("deleteSurface" in message) {
      const shown = this.drawing.get(surfaceId) ?? [];
      if (shown.some((m) => "updateComponents" in m)) this.lastGood.set(surfaceId, shown);
      this.drawing.set(surfaceId, []);
    }
    try {
      this.processor.processMessages([message]);
    } catch {
      const drawing = this.drawing.get(surfaceId) ?? [];
      const previous = this.lastGood.get(surfaceId);
      if (previous && !drawing.some((m) => "updateComponents" in m)) {
        this.processor.processMessages([
          { version: "v0.9", deleteSurface: { surfaceId } },
          ...previous,
        ]);
        this.drawing.set(surfaceId, [...previous]);
      }
      return false;
    }
    if (!("deleteSurface" in message)) {
      this.drawing.set(surfaceId, [...(this.drawing.get(surfaceId) ?? []), message]);
    }
    return true;
  }

  private applyAll(messages: A2uiMessage[]) {
    for (const message of messages) this.apply(message);
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

interface Turn {
  quiet: boolean;
  /** Set once the Canvas has rejected a message of this turn's screen. */
  rejected: boolean;
}

function surfaceOf(message: A2uiMessage): string {
  if ("deleteSurface" in message) return message.deleteSurface.surfaceId;
  if ("createSurface" in message) return message.createSurface.surfaceId;
  if ("updateComponents" in message) return message.updateComponents.surfaceId;
  return message.updateDataModel.surfaceId;
}

function describe(error: unknown): string {
  if (error instanceof AgentError) return error.message;
  return "Something went wrong while talking to the agent.";
}
