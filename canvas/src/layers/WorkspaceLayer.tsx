import type { A2UIPayload } from "../a2ui/types";
import { Renderer } from "../a2ui/Renderer";

interface Props {
  payload: A2UIPayload;
}

export default function WorkspaceLayer({ payload }: Props) {
  return (
    <div className="workspace-layer">
      <div className="workspace-mode">{payload.mode}</div>
      {payload.title && (
        <div className="workspace-title">{payload.title}</div>
      )}
      <Renderer node={payload.body} />
    </div>
  );
}
