import type { TextNode } from "../types";

type Props = TextNode["props"];

export default function Text({ content, variant = "body" }: Props) {
  if (variant === "heading") return <h2 className="a2ui-heading">{content}</h2>;
  if (variant === "code") return <pre className="a2ui-code"><code>{content}</code></pre>;
  return <p className="a2ui-text">{content}</p>;
}
