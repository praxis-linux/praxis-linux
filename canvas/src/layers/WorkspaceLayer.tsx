import { A2uiSurface } from "@a2ui/react/v0_9";
import type { Mode } from "../agent/client";
import type { Surface } from "../session";

interface Props {
  mode: Mode;
  surface: Surface | undefined;
  /** What the agent is doing, while it works. */
  status: string | null;
  error: string | null;
}

export default function WorkspaceLayer({ mode, surface, status, error }: Props) {
  return (
    <div className="workspace-layer">
      <div className="workspace-head">
        <span className="workspace-mode">{mode}</span>
        {/* Always rendered, so screen readers announce each new status. */}
        <span className="workspace-status" role="status">
          {status}
        </span>
      </div>
      {error && (
        <div className="workspace-error" role="alert">
          {error}
        </div>
      )}
      {surface && (
        <div className="a2ui-host">
          <A2uiSurface surface={surface} />
        </div>
      )}
    </div>
  );
}
