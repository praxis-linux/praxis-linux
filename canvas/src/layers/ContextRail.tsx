import { A2uiSurface } from "@a2ui/react/v0_9";
import type { Surface } from "../session";

interface Props {
  surface: Surface;
}

export default function ContextRail({ surface }: Props) {
  return (
    <aside className="context-rail a2ui-host">
      <A2uiSurface surface={surface} />
    </aside>
  );
}
