import type { A2uiMessage } from "@a2ui/web_core/v0_9";
import { PRAXIS_CATALOG_ID } from "./catalog";

export const WORKSPACE_SURFACE = "workspace";
export const CONTEXT_SURFACE = "context";

const CARDS = [
  {
    title: "Start a conversation",
    description: "Ask anything — Praxis will figure out the right mode.",
    intent: "What can you help me with?",
  },
  {
    title: "Browse your files",
    description: 'Say "show me recent documents" to open File mode.',
    intent: "Show me my recent documents",
  },
  {
    title: "Write some code",
    description: "Describe what you want to build and switch to Code mode.",
    intent: "Help me write a small script",
  },
];

/** The screen shown before the agent has answered anything. Rendered locally. */
export const WELCOME_MESSAGES: A2uiMessage[] = [
  {
    version: "v0.9",
    createSurface: { surfaceId: WORKSPACE_SURFACE, catalogId: PRAXIS_CATALOG_ID },
  },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: WORKSPACE_SURFACE,
      components: [
        { id: "root", component: "Column", children: ["title", "cards"] },
        { id: "title", component: "Text", text: "Welcome to Praxis", variant: "h2" },
        { id: "cards", component: "Column", children: CARDS.map((_, i) => `card${i}`) },
        ...CARDS.flatMap((card, i) => [
          {
            id: `card${i}`,
            component: "Button",
            child: `card${i}_body`,
            action: { event: { name: "intent", context: { text: card.intent } } },
          },
          {
            id: `card${i}_body`,
            component: "Column",
            children: [`card${i}_title`, `card${i}_desc`],
          },
          { id: `card${i}_title`, component: "Text", text: card.title, variant: "h4" },
          { id: `card${i}_desc`, component: "Text", text: card.description, variant: "caption" },
        ]),
      ],
    },
  },
];
