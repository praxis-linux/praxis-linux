export type A2UIMode =
  | "conversation"
  | "file"
  | "code"
  | "document"
  | "spreadsheet"
  | "browser"
  | "settings"
  | "terminal"
  | "media"
  | "diagram"
  | "dashboard";

export interface A2UIPayload {
  mode: A2UIMode;
  title?: string;
  body: A2UINode;
  context?: A2UINode;
}

export type A2UINode = CardsNode | FormNode | TextNode;

export interface CardsNode {
  component: "Cards";
  props: {
    items: Array<{
      title: string;
      description?: string;
      icon?: string;
      action?: string;
    }>;
  };
}

export interface FormNode {
  component: "Form";
  props: {
    fields: Array<{
      name: string;
      label: string;
      type: "text" | "textarea" | "select";
      placeholder?: string;
    }>;
    submitLabel?: string;
  };
}

export interface TextNode {
  component: "Text";
  props: {
    content: string;
    variant?: "body" | "heading" | "code";
  };
}
