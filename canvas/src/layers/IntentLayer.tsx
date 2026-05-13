import { useState, type KeyboardEvent } from "react";
import type { A2UIMode } from "../a2ui/types";

interface Props {
  mode: A2UIMode;
  onIntent: (intent: string) => void;
}

export default function IntentLayer({ mode, onIntent }: Props) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (trimmed) {
      onIntent(trimmed);
      setValue("");
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="intent-layer">
      <span className="mode-badge">{mode}</span>
      <input
        className="intent-input"
        type="text"
        placeholder="What do you want to do?"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        autoFocus
        autoComplete="off"
        spellCheck={false}
      />
      <button className="intent-submit" onClick={submit} aria-label="Submit intent">
        →
      </button>
    </div>
  );
}
