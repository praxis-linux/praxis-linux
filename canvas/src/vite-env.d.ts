/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the agent listens. */
  readonly VITE_PRAXIS_AGENT_URL?: string;
  /** In development, the token the agent was started with (scripts/dev.py sets both). */
  readonly VITE_PRAXIS_AGENT_TOKEN?: string;
}
