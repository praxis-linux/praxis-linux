import type { CardsNode } from "../types";

type Props = CardsNode["props"];

export default function Cards({ items }: Props) {
  return (
    <div className="a2ui-cards">
      {items.map((item, i) => (
        <div key={i} className="a2ui-card">
          {item.icon && <span className="card-icon">{item.icon}</span>}
          <div className="card-body">
            <div className="card-title">{item.title}</div>
            {item.description && (
              <div className="card-description">{item.description}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
