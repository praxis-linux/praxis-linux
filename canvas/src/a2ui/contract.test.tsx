// The Canvas side of the agent contract: what the agent's builders emit must be
// accepted by the real A2UI renderer, and what the user does must reach the agent.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EditorView } from "codemirror";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
// Written by agent/tests/test_a2ui.py from the agent's own builders.
import golden from "../../../agent/tests/golden/workspace_messages.json";
import trailGolden from "../../../agent/tests/golden/trail_messages.json";
import codeGolden from "../../../agent/tests/golden/code_messages.json";
import App from "../App";

const GOLDEN: unknown[] = golden;
// The trail of a two-turn conversation, the second turn on screen.
const TRAIL: unknown[] = trailGolden;
// A Code mode screen: a change just saved, the editor, a proposed change and a run.
const CODE: unknown[] = codeGolden;

interface SentRequest {
  path: string;
  session: string | null;
  body: any;
}

// Requests the user's actions caused, and the requests to resume made on start.
let sent: SentRequest[];
let resumes: SentRequest[];

const ndjson = (events: unknown[]) =>
  new Response(events.map((event) => JSON.stringify(event) + "\n").join(""));

/**
 * Stubs the agent: every request is answered with `events` as NDJSON, except
 * resuming, which is answered with `resume`: by default, a conversation that is new.
 */
function agentAnswers(events: unknown[], { resume = [] as unknown[] } = {}) {
  sent = [];
  resumes = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const request = {
      path: new URL(url).pathname,
      session: new Headers(init.headers).get("X-Praxis-Session"),
      body: JSON.parse(init.body as string),
    };
    if (request.path === "/resume") {
      resumes.push(request);
      return ndjson(resume);
    }
    sent.push(request);
    return ndjson(events);
  });
}

const a2ui = (messages: unknown[]) => messages.map((message) => ({ type: "a2ui", message }));

const GOLDEN_TURN = [
  { type: "call", model: "local-fast", destination: "local" },
  { type: "mode", mode: "file" },
  ...a2ui(GOLDEN),
];

async function submitIntent(text: string) {
  const input = screen.getByPlaceholderText("What do you want to do?");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
}

beforeEach(() => {
  localStorage.clear();
  agentAnswers(GOLDEN_TURN);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("shows the welcome screen without the agent", async () => {
  render(<App />);

  expect(await screen.findByText("Welcome to Praxis")).toBeTruthy();
  expect(screen.getByText("Browse your files")).toBeTruthy();
  expect(sent).toEqual([]);
  await waitFor(() => expect(resumes).toHaveLength(1));
});

test("says nothing when the agent cannot be reached until the user asks for something", async () => {
  vi.stubGlobal("fetch", async () => {
    throw new TypeError("fetch failed");
  });
  render(<App />);

  expect(await screen.findByText("Welcome to Praxis")).toBeTruthy();
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("renders the agent's answer to an intent", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("plan a project");

  expect(await screen.findByText("Plan a project")).toBeTruthy();
  expect(screen.queryByText("Welcome to Praxis")).toBeNull();
  expect(screen.getByLabelText("Project name")).toBeTruthy();
  expect(screen.getByText("See an example")).toBeTruthy();
  // Markdown in body text is rendered, not shown raw.
  expect((await screen.findByText("project")).tagName).toBe("STRONG");
  expect(document.querySelector("pre code")?.textContent).toBe('print("hello")\nprint("world")\n');
  // Mode and model come from the agent's stream.
  expect(screen.getAllByText("file").length).toBeGreaterThan(0);
  expect(screen.getByText("local-fast")).toBeTruthy();

  expect(sent).toHaveLength(1);
  expect(sent[0].path).toBe("/intent");
  expect(sent[0].body).toEqual({ intent: "plan a project" });
  expect(sent[0].session).toMatch(/^[0-9a-f-]{36}$/);
});

test("clicking a card sends its intent to the agent as an action", async () => {
  render(<App />);
  fireEvent.click(await screen.findByText("Browse your files"));

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0].path).toBe("/action");
  expect(sent[0].body.version).toBe("v0.9");
  expect(sent[0].body.action).toMatchObject({
    name: "intent",
    surfaceId: "workspace",
    context: { text: "Show me my recent documents" },
  });
});

test("submitting a form sends what the user typed", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("plan a project");

  fireEvent.change(await screen.findByLabelText("Project name"), { target: { value: "Praxis" } });
  fireEvent.click(screen.getByText("Continue"));

  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1].path).toBe("/action");
  expect(sent[1].body.action).toMatchObject({
    name: "submit",
    context: {
      purpose: "Gather project details",
      values: { project_name: "Praxis", goal: "" },
    },
  });
  // Same session as the intent that produced the form.
  expect(sent[1].session).toBe(sent[0].session);
});

test("shows files as they are named and as they are on disk", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("plan a project");

  // A name full of Markdown syntax is shown literally.
  expect((await screen.findByText("__init__.py")).tagName).toBe("H5");
  expect(screen.getByText("praxis/")).toBeTruthy();
  expect(screen.getByText("2.0 KB · 2026-09-29 14:05")).toBeTruthy();
  // A file's text is shown raw, even when it is Markdown.
  const previews = document.querySelectorAll("pre code");
  const preview = previews[previews.length - 1];
  expect(preview?.textContent).toBe("# Notes\n\n- *ship* the demo\n\n");
});

test("clicking a file or folder asks the agent to open it", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("plan a project");

  fireEvent.click(await screen.findByText("__init__.py"));
  await waitFor(() => expect(sent).toHaveLength(2));
  fireEvent.click(await screen.findByText("↑ ada"));
  await waitFor(() => expect(sent).toHaveLength(3));

  expect(sent[1].path).toBe("/action");
  expect(sent[1].body.action).toMatchObject({
    name: "open",
    context: { target: "/home/ada/projects/__init__.py", kind: "file" },
  });
  expect(sent[2].body.action).toMatchObject({
    name: "open",
    context: { target: "/home/ada", kind: "directory" },
  });
});

test("shows a cloud call as a cloud call", async () => {
  agentAnswers([{ type: "call", model: "cloud-strong", destination: "cloud" }, ...a2ui(GOLDEN)]);
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  await waitFor(() => expect(document.querySelector(".model-badge")).not.toBeNull());
  const badge = document.querySelector(".model-badge")!;
  expect(badge.textContent).toContain("cloud-strong");
  expect(badge.className).toContain("cloud");
  // And it is announced: nothing leaves the machine unannounced.
  expect(screen.getByText(/This request is going to cloud-strong, a cloud model./)).toBeTruthy();
});

test("a local call is not announced", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("plan a project");

  await screen.findByText("Plan a project");
  expect(screen.queryByText(/a cloud model/)).toBeNull();
});

test("refuses components outside the catalog", async () => {
  const [deleteSurface, createSurface] = GOLDEN;
  agentAnswers(
    a2ui([
      deleteSurface,
      createSurface,
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "workspace",
          components: [
            { id: "root", component: "Image", url: "https://evil.example/?leak=1" },
          ],
        },
      },
    ]),
  );
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  expect((await screen.findByRole("alert")).textContent).toContain("cannot show");
  expect(document.querySelector("img")).toBeNull();
});

test("reports an agent that is not running", async () => {
  vi.stubGlobal("fetch", async () => {
    throw new TypeError("fetch failed");
  });
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  expect((await screen.findByRole("alert")).textContent).toContain("Could not reach the agent");
  // The screen the user was on is still there.
  expect(screen.getByText("Welcome to Praxis")).toBeTruthy();
});

// --- the conversation --------------------------------------------------------

const RESUMED = [{ type: "mode", mode: "conversation" }, ...a2ui(GOLDEN), ...a2ui(TRAIL)];

test("picks up where the conversation left off", async () => {
  agentAnswers(GOLDEN_TURN, { resume: RESUMED });
  render(<App />);

  expect(await screen.findByText("Plan a project")).toBeTruthy();
  expect(screen.queryByText("Welcome to Praxis")).toBeNull();
  expect(screen.getByText("This conversation")).toBeTruthy();
  expect(screen.getByText("▸ show my notes")).toBeTruthy();
  expect(sent).toEqual([]);
});

test("a reload continues the same conversation", async () => {
  render(<App />);
  await waitFor(() => expect(resumes).toHaveLength(1));
  const conversation = resumes[0].session;
  cleanup();

  render(<App />);
  await waitFor(() => expect(resumes).toHaveLength(2));
  await submitIntent("hello again");

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(conversation).toMatch(/^[0-9a-f-]{36}$/);
  expect(resumes[1].session).toBe(conversation);
  expect(sent[0].session).toBe(conversation);
});

test("clicking a turn in the trail asks the agent to show it again", async () => {
  agentAnswers(GOLDEN_TURN, { resume: RESUMED });
  render(<App />);

  fireEvent.click(await screen.findByText("plan a project"));

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0].path).toBe("/action");
  expect(sent[0].body.action).toMatchObject({ name: "revisit", context: { turn: 1 } });
});

test("a new conversation starts from the welcome screen with a new id", async () => {
  agentAnswers(GOLDEN_TURN, { resume: RESUMED });
  render(<App />);
  await screen.findByText("This conversation");
  const old = resumes[0].session;

  fireEvent.click(screen.getByText("New"));

  expect(await screen.findByText("Welcome to Praxis")).toBeTruthy();
  expect(screen.queryByText("This conversation")).toBeNull();
  await submitIntent("fresh start");
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0].session).not.toBe(old);
  expect(localStorage.getItem("praxis.conversation")).toBe(sent[0].session);
});

test("says what the agent is doing while it looks things up", async () => {
  let push!: (event: unknown) => void;
  let end!: () => void;
  agentAnswers([]);
  vi.stubGlobal("fetch", async (url: string) => {
    if (new URL(url).pathname === "/resume") return ndjson([]);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        push = (event) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
        end = () => controller.close();
      },
    });
    return new Response(body);
  });
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("find my python files");
  await waitFor(() => expect(push).toBeDefined());
  push({ type: "call", model: "local-fast", destination: "local" });
  push({ type: "status", text: "Searching for *.py…" });

  expect(await screen.findByText("Searching for *.py…")).toBeTruthy();
  GOLDEN_TURN.slice(1).forEach(push);
  end();

  expect(await screen.findByText("Plan a project")).toBeTruthy();
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe(""));
});

// --- what the agent sends cannot reach outside the Canvas --------------------

/** A turn whose screen is one body Text: the kind the model writes. */
function textScreen(text: string) {
  const [deleteSurface, createSurface] = GOLDEN;
  return a2ui([
    deleteSurface,
    createSurface,
    {
      version: "v0.9",
      updateComponents: {
        surfaceId: "workspace",
        components: [
          { id: "root", component: "Column", children: ["title", "b0"] },
          { id: "title", component: "Text", text: "Links", variant: "h2" },
          { id: "b0", component: "Text", text, variant: "body" },
        ],
      },
    },
  ]);
}

test("links and images in the agent's text are shown, never followed or loaded", async () => {
  agentAnswers(
    textScreen(
      "See [the docs](https://evil.example/login), <https://evil.example/x> " +
        "and ![a chart](https://evil.example/t.png?d=secret).",
    ),
  );
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  await screen.findByText("Links");
  await waitFor(() => expect(document.querySelector(".agent-link")).not.toBeNull());
  expect(document.querySelector(".a2ui-host a")).toBeNull();
  expect(document.querySelector(".a2ui-host img")).toBeNull();
  const text = document.querySelector(".a2ui-host")?.textContent ?? "";
  expect(text).toContain("the docs (https://evil.example/login)");
  expect(text).toContain("[image: a chart]");
});

test("an image file is shown from the data the agent read, not fetched", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("plan a project");

  const image = (await screen.findByAltText("pixel.png")) as HTMLImageElement;
  expect(image.getAttribute("src")).toMatch(/^data:image\/png;base64,iVBORw0KGgo/);
});

test("an image component pointing anywhere but inline data is refused", async () => {
  const [deleteSurface, createSurface] = GOLDEN;
  agentAnswers(
    a2ui([
      deleteSurface,
      createSurface,
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "workspace",
          components: [
            { id: "root", component: "Column", children: ["img"] },
            { id: "img", component: "FileImage", src: "https://evil.example/t.png?d=secret" },
          ],
        },
      },
    ]),
  );
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  expect((await screen.findByRole("alert")).textContent).toContain("cannot show");
  expect(document.querySelector("img")).toBeNull();
});

// --- what goes wrong, and what the user is told -------------------------------

test("an event type this Canvas does not know is skipped", async () => {
  agentAnswers([{ type: "progress", percent: 50 }, ...GOLDEN_TURN]);
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("plan a project");

  expect(await screen.findByText("Plan a project")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("an unreadable line ends the turn with a plain message and stops the request", async () => {
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    if (new URL(url).pathname === "/resume") return ndjson([]);
    signal = init.signal ?? undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode("Internal Server Error\n"));
        // The stream stays open: only the Canvas can end this request.
      },
    });
    return new Response(body);
  });
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent sent something the Canvas could not read.",
  );
  expect(signal?.aborted).toBe(true);
  expect(screen.getByPlaceholderText("What do you want to do?")).toBeTruthy();
});

test("a connection lost mid-turn is not reported as an agent that is not running", async () => {
  vi.stubGlobal("fetch", async (url: string) => {
    if (new URL(url).pathname === "/resume") return ndjson([]);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            JSON.stringify({ type: "call", model: "local-fast", destination: "local" }) + "\n",
          ),
        );
        setTimeout(() => controller.error(new TypeError("network error")), 10);
      },
    });
    return new Response(body);
  });
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hi");

  expect((await screen.findByRole("alert")).textContent).toBe(
    "The connection to the agent was lost.",
  );
});

test("a rejected screen leaves the previous one in place", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("plan a project");
  await screen.findByText("Plan a project");
  const [deleteSurface, createSurface] = GOLDEN;
  agentAnswers(
    a2ui([
      deleteSurface,
      createSurface,
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "workspace",
          components: [{ id: "root", component: "Image", url: "https://evil.example/x" }],
        },
      },
      // Messages after the rejected one are not drawn either.
      ...textScreen("drawn after the rejection").map((event) => event.message),
    ]),
  );

  await submitIntent("again");

  expect((await screen.findByRole("alert")).textContent).toContain("cannot show");
  expect(screen.getByText("Plan a project")).toBeTruthy();
  expect(screen.queryByText(/Loading/)).toBeNull();
  expect(screen.queryByText("drawn after the rejection")).toBeNull();
});

test("the model badge is cleared by a turn that calls no model", async () => {
  agentAnswers([{ type: "call", model: "cloud-strong", destination: "cloud" }, ...a2ui(GOLDEN)]);
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("hi");
  await waitFor(() => expect(document.querySelector(".model-badge")).not.toBeNull());
  // Opening a file is answered without a call event: no model was involved.
  agentAnswers([{ type: "mode", mode: "file" }, ...a2ui(GOLDEN)]);

  fireEvent.click(await screen.findByText("__init__.py"));

  await waitFor(() => expect(document.querySelector(".model-badge")).toBeNull());
});

// --- the kill switch and deleting -------------------------------------------------

/** Stubs the agent so that intents hang until stopped; records every request. */
function agentThatHangs() {
  const paths: string[] = [];
  let intentSignal: AbortSignal | undefined;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname;
    paths.push(path);
    if (path === "/resume") return ndjson([]);
    if (path === "/stop") return new Response('{"stopped": 1}');
    intentSignal = init.signal ?? undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            JSON.stringify({ type: "status", text: "Searching for *.py…" }) + "\n",
          ),
        );
      },
    });
    return new Response(body);
  });
  return { paths, signal: () => intentSignal };
}

test("Esc stops the agent while it works", async () => {
  const agent = agentThatHangs();
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("find my python files");
  await screen.findByText("Searching for *.py…");

  fireEvent.keyDown(window, { key: "Escape" });

  await screen.findByText("Stopped.");
  expect(agent.signal()?.aborted).toBe(true);
  await waitFor(() => expect(agent.paths).toContain("/stop"));
  expect(screen.getByPlaceholderText("What do you want to do?")).toBeTruthy();
});

test("the send button becomes a Stop button while the agent works", async () => {
  const agent = agentThatHangs();
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("find my python files");

  fireEvent.click(await screen.findByRole("button", { name: "Stop (Esc)" }));

  await waitFor(() => expect(agent.paths).toContain("/stop"));
  expect(screen.getByRole("button", { name: "Submit intent" })).toBeTruthy();
});

test("Esc does nothing when the agent is idle", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  fireEvent.keyDown(window, { key: "Escape" });

  expect(screen.queryByText("Stopped.")).toBeNull();
  expect(sent).toEqual([]);
});

test("deleting a conversation asks first, then starts a new one", async () => {
  agentAnswers(GOLDEN_TURN, { resume: RESUMED });
  render(<App />);
  await screen.findByText("This conversation");
  const old = resumes[0].session;

  fireEvent.click(screen.getByText("Delete…"));
  fireEvent.click(screen.getByText("Keep"));
  expect(sent).toEqual([]);
  fireEvent.click(screen.getByText("Delete…"));
  fireEvent.click(screen.getByText("Delete"));

  await screen.findByText("Conversation deleted.");
  expect(sent.map((r) => [r.path, r.session])).toEqual([["/forget", old]]);
  expect(screen.getByText("Welcome to Praxis")).toBeTruthy();
  expect(screen.queryByText("This conversation")).toBeNull();
  expect(localStorage.getItem("praxis.conversation")).not.toBe(old);
});

test("Enter that confirms an input-method composition does not send the intent", async () => {
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  const input = screen.getByPlaceholderText("What do you want to do?");
  fireEvent.change(input, { target: { value: "你好" } });

  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });

  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(sent).toEqual([]);
});

test("an error while resuming on start is not reported", async () => {
  agentAnswers(GOLDEN_TURN, {
    resume: [{ type: "error", message: "That part of the conversation is not stored." }],
  });
  render(<App />);

  await waitFor(() => expect(resumes).toHaveLength(1));
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByRole("alert")).toBeNull();
});

// --- Code mode ---

const CODE_TURN = [{ type: "mode", mode: "code" }, ...a2ui(CODE)];

async function showCode() {
  agentAnswers(CODE_TURN);
  render(<App />);
  await screen.findByText("Welcome to Praxis");
  await submitIntent("open app.py");
  return (await screen.findByLabelText("Editing app.py")) as HTMLElement;
}

test("a file opens in an editor holding its text", async () => {
  const editor = await showCode();

  expect(editor.closest(".cm-editor")).toBeTruthy();
  expect(EditorView.findFromDOM(editor.closest(".cm-editor") as HTMLElement)?.state.doc.toString()).toBe(
    "print('hi')\n",
  );
  // The proposed change, and what the run printed, are on the same screen.
  expect(screen.getByText("Proposed change to /home/ada/projects/app.py")).toBeTruthy();
  expect(screen.getByText("$ python3 app.py · exit code 1 · 0.2 s")).toBeTruthy();
});

test("a proposed change's lines are marked as added or removed, as text", async () => {
  await showCode();

  const diff = document.querySelector("code.language-praxis-diff")!;
  expect(diff.querySelector(".diff-del")?.textContent).toBe("-print('hi')\n");
  expect(diff.querySelector(".diff-add")?.textContent).toBe("+print('bye')\n");
  expect(diff.querySelectorAll(".diff-file")).toHaveLength(2);
  expect(diff.textContent).toContain("@@ -1 +1 @@");
});

test("Save sends what was typed, with the file and the hash it was opened at", async () => {
  const editor = await showCode();
  const view = EditorView.findFromDOM(editor.closest(".cm-editor") as HTMLElement)!;

  act(() => {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "print('bye')\n" } });
  });
  fireEvent.click(screen.getByText("Save"));

  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1].body.action).toMatchObject({
    name: "save",
    context: {
      target: "/home/ada/projects/app.py",
      base: "ab".repeat(32),
      content: "print('bye')\n",
    },
  });
});

test("Apply, Discard, Undo and Run send what they act on", async () => {
  await showCode();

  for (const label of ["Apply", "Discard", "Undo", "Run", "Run again"]) {
    fireEvent.click(screen.getByText(label));
  }

  await waitFor(() => expect(sent).toHaveLength(6));
  expect(sent.slice(1).map((request) => request.body.action)).toMatchObject([
    { name: "apply", context: { proposal: "c0ffee" } },
    { name: "discard", context: { proposal: "c0ffee" } },
    { name: "undo", context: { backup: "20261025T120000-0a1b2c3d" } },
    { name: "run", context: { target: "/home/ada/projects/app.py" } },
    { name: "run", context: { target: "/home/ada/projects/app.py" } },
  ]);
});

test("an editor whose text comes from a function the catalog leaves out is refused", async () => {
  const [, create, components] = CODE as any[];
  const unbound = structuredClone(components);
  for (const component of unbound.updateComponents.components) {
    if (component.component === "CodeEditor") component.value = { call: "openUrl", args: {} };
  }
  agentAnswers([...a2ui([CODE[0], create, unbound])]);
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("open app.py");

  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/not|could/i));
  expect(screen.queryByLabelText("Editing app.py")).toBeNull();
});

test("an agent that does not recognise this Canvas says so in plain words", async () => {
  vi.stubGlobal("fetch", async (url: string) =>
    new URL(url).pathname === "/resume" ? ndjson([]) : new Response("no", { status: 401 }),
  );
  render(<App />);
  await screen.findByText("Welcome to Praxis");

  await submitIntent("hello");

  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent did not recognise this Canvas. Restart Praxis.",
  );
});
