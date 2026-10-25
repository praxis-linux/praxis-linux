import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { rust } from "@codemirror/lang-rust";
import { StreamLanguage } from "@codemirror/language";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { EditorState, type Extension } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { DynamicStringSchema } from "@a2ui/web_core/v0_9";
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { basicSetup, EditorView } from "codemirror";
import { useEffect, useRef } from "react";
import { z } from "zod";

// Languages the editor highlights, by the name the agent sends. Anything else is
// edited as plain text.
const LANGUAGES: Record<string, () => Extension> = {
  python: () => python(),
  javascript: () => javascript({ jsx: true }),
  typescript: () => javascript({ jsx: true, typescript: true }),
  json: () => json(),
  markdown: () => markdown(),
  html: () => html(),
  css: () => css(),
  rust: () => rust(),
  shell: () => StreamLanguage.define(shell),
};

// One Dark's highlighting on the Canvas's own surfaces.
const praxisTheme = EditorView.theme(
  {
    "&": { backgroundColor: "var(--bg-surface)", fontSize: "13px" },
    ".cm-gutters": { backgroundColor: "var(--bg-surface)", borderRight: "1px solid var(--border)" },
    ".cm-content": { fontFamily: "var(--a2ui-font-family-monospace)" },
  },
  { dark: true },
);

/**
 * A file's text, editable. `value` is bound to the data model, so what the user
 * types is what an action that reads the same path (Save) sends.
 */
export const CodeEditorApi = {
  name: "CodeEditor",
  schema: z.object({
    value: DynamicStringSchema,
    language: z.string().max(20).optional(),
    // What is being edited, for screen readers: usually the file's name.
    label: z.string().max(200).optional(),
  }),
};

export const CodeEditor = createComponentImplementation(CodeEditorApi, ({ props }) => {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // The latest setter, for the editor's listener, which is made once.
  const setValue = useRef(props.setValue);
  setValue.current = props.setValue;
  const value = typeof props.value === "string" ? props.value : "";

  useEffect(() => {
    const language = LANGUAGES[props.language ?? ""];
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          oneDark,
          praxisTheme,
          language ? language() : [],
          EditorView.contentAttributes.of({
            "aria-label": props.label ? `Editing ${props.label}` : "Code editor",
          }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) setValue.current?.(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
    // Made once per file shown; new text from the agent arrives through the effect below.
  }, [props.language, props.label]);

  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div className="code-editor" ref={host} />;
});
