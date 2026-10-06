// The Canvas side of the agent contract: what the agent's builders emit must be
// accepted by the real A2UI renderer, and what the user does must reach the agent.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
// Written by agent/tests/test_a2ui.py from the agent's own builders.
import golden from "../../../agent/tests/golden/workspace_messages.json";
import App from "../App";

const GOLDEN: unknown[] = golden;

interface SentRequest {
  path: string;
  session: string | null;
  body: any;
}

let sent: SentRequest[];

/** Stubs the agent: every request is answered with `events` as NDJSON. */
function agentAnswers(events: unknown[]) {
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({
      path: new URL(url).pathname,
      session: new Headers(init.headers).get("X-Praxis-Session"),
      body: JSON.parse(init.body as string),
    });
    return new Response(events.map((event) => JSON.stringify(event) + "\n").join(""));
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

beforeEach(() => agentAnswers(GOLDEN_TURN));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("shows the welcome screen without the agent", async () => {
  render(<App />);

  expect(await screen.findByText("Welcome to Praxis")).toBeTruthy();
  expect(screen.getByText("Browse your files")).toBeTruthy();
  expect(sent).toEqual([]);
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

  const badge = await screen.findByText(/cloud-strong/);
  expect(badge.className).toContain("cloud");
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
