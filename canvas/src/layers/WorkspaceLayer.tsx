import { A2uiSurface } from "@a2ui/react/v0_9";
import type { Mode } from "../agent/client";
import type { Surface } from "../session";

interface Props {
  mode: Mode;
  surface: Surface | undefined;
  error: string | null;
}

export default function WorkspaceLayer({ mode, surface, error }: Props) {
  return (
    <div className="workspace-layer">
      <div className="workspace-mode">{mode}</div>
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
