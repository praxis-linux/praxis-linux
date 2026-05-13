import type { A2UINode } from "../a2ui/types";
import { Renderer } from "../a2ui/Renderer";

interface Props {
  node: A2UINode;
}

export default function ContextRail({ node }: Props) {
  return (
    <aside className="context-rail">
      <Renderer node={node} />
    </aside>
  );
}
