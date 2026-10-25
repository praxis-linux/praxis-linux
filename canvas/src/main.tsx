import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { CanvasSession } from "./session";
import "./index.css";

// One session for the window, made here rather than in a component: StrictMode
// renders components twice in development, which would start two sessions.
const session = new CanvasSession();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App session={session} />
  </React.StrictMode>,
);
