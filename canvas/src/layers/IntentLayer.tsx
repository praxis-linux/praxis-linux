import { useState, type KeyboardEvent } from "react";
import type { Mode } from "../agent/client";
import type { CanvasState } from "../session";

interface Props {
  mode: Mode;
  call: CanvasState["call"];
  busy: boolean;
  onIntent: (intent: string) => void;
  onStop: () => void;
  onNewConversation: () => void;
  onForgetConversation: () => void;
}

export default function IntentLayer({
  mode,
  call,
  busy,
  onIntent,
  onStop,
  onNewConversation,
  onForgetConversation,
}: Props) {
  const [value, setValue] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  function submit() {
    const trimmed = value.trim();
    if (trimmed) {
      onIntent(trimmed);
      setValue("");
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    // Enter that confirms a word in an input method (Chinese, Japanese, ...) is not a submit.
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="intent-layer">
      {confirmingDelete ? (
        <span className="intent-confirm" role="group" aria-label="Delete this conversation?">
          Delete this conversation and what was read in it?
          <button
            className="intent-new danger"
            onClick={() => {
              setConfirmingDelete(false);
              onForgetConversation();
            }}
          >
            Delete
          </button>
          <button className="intent-new" onClick={() => setConfirmingDelete(false)}>
            Keep
          </button>
        </span>
      ) : (
        <>
          <button
            className="intent-new"
            onClick={onNewConversation}
            title="Start a new conversation. This one is kept."
          >
            New
          </button>
          <button
            className="intent-new"
            onClick={() => setConfirmingDelete(true)}
            title="Delete this conversation"
          >
            Delete…
          </button>
        </>
      )}
      <span className="mode-badge">{mode}</span>
      {call && (
        <span
          className={`model-badge ${call.destination}`}
          title={
            call.destination === "cloud"
              ? "This turn is sent to a cloud model"
              : "This turn runs on a local model"
          }
        >
          {call.destination === "cloud" ? "☁ " : ""}
          {call.model}
        </span>
      )}
      <input
        className="intent-input"
        type="text"
        aria-label="What do you want to do?"
        placeholder={busy ? "Working… (Esc to stop)" : "What do you want to do?"}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        autoFocus
        autoComplete="off"
        spellCheck={false}
      />
      {busy ? (
        <button className="intent-submit stop" onClick={onStop} aria-label="Stop (Esc)">
          ■
        </button>
      ) : (
        <button className="intent-submit" onClick={submit} aria-label="Submit intent">
          →
        </button>
      )}
    </div>
  );
}
