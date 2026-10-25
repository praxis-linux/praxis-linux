import { useState, useSyncExternalStore } from "react";
import { MarkdownContext } from "@a2ui/react/v0_9";
import WorkspaceLayer from "./layers/WorkspaceLayer";
import IntentLayer from "./layers/IntentLayer";
import ContextRail from "./layers/ContextRail";
import { renderAgentMarkdown } from "./a2ui/markdown";
import { CanvasSession } from "./session";

interface Props {
  session?: CanvasSession;
}

export default function App({ session: provided }: Props) {
  const [session] = useState(() => provided ?? new CanvasSession());
  const state = useSyncExternalStore(session.subscribe, session.getState);

  return (
    <MarkdownContext.Provider value={renderAgentMarkdown}>
      <div className="canvas-root">
        <div className="canvas-main">
          <WorkspaceLayer
            mode={state.mode}
            surface={state.workspace}
            status={state.busy ? state.status : null}
            error={state.error}
          />
          {state.context && <ContextRail surface={state.context} />}
        </div>
        <IntentLayer
          mode={state.mode}
          call={state.call}
          busy={state.busy}
          onIntent={(intent) => session.sendIntent(intent)}
          onNewConversation={() => session.newConversation()}
        />
      </div>
    </MarkdownContext.Provider>
  );
}
