import type { A2UINode } from "./types";
import Cards from "./components/Cards";
import Form from "./components/Form";
import Text from "./components/Text";

interface Props {
  node: A2UINode;
}

export function Renderer({ node }: Props) {
  switch (node.component) {
    case "Cards":
      return <Cards {...node.props} />;
    case "Form":
      return <Form {...node.props} />;
    case "Text":
      return <Text {...node.props} />;
  }
}
