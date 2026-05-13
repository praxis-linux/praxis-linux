import { useState } from "react";
import WorkspaceLayer from "./layers/WorkspaceLayer";
import IntentLayer from "./layers/IntentLayer";
import ContextRail from "./layers/ContextRail";
import type { A2UIPayload } from "./a2ui/types";

const INITIAL_PAYLOAD: A2UIPayload = {
  mode: "conversation",
  title: "Welcome to Praxis",
  body: {
    component: "Cards",
    props: {
      items: [
        {
          title: "Start a conversation",
          description: "Ask anything — Praxis will figure out the right mode.",
          icon: "💬",
        },
        {
          title: "Browse your files",
          description: 'Say "show me recent documents" to open File mode.',
          icon: "📁",
        },
        {
          title: "Write some code",
          description: "Describe what you want to build and switch to Code mode.",
          icon: "⚡",
        },
      ],
    },
  },
};

export default function App() {
  const [payload, setPayload] = useState<A2UIPayload>(INITIAL_PAYLOAD);

  function handleIntent(intent: string) {
    // M0.6: will send to agent service; for now reflect intent in title
    setPayload((p) => ({ ...p, title: `"${intent}"` }));
  }

  return (
    <div className="canvas-root">
      <div className="canvas-main">
        <WorkspaceLayer payload={payload} />
        {payload.context && <ContextRail node={payload.context} />}
      </div>
      <IntentLayer mode={payload.mode} onIntent={handleIntent} />
    </div>
  );
}
