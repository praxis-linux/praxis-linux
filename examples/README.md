# Examples

End-to-end demo flows for Praxis.

## Status

[`90s-demo`](90s-demo/README.md), the launch demo, is ready: a setup script and a shot list. The others are not written yet, though everything they show works.

## Demos

| Demo | Description | Status |
|---|---|---|
| [`90s-demo`](90s-demo/README.md) | The launch demo: intent → mode switch → useful action | Ready |
| `conversation-basic` | Chat, streaming response, memory across turns | Not written yet |
| `file-search` | "Find all Python files I edited this week" | Not written yet |
| `code-edit-run` | Open, edit, and run a script from intent | Not written yet (the 90-second demo covers it) |
| `privacy-audit` | Trigger a cloud call; observe the notice and the audit log entry | Not written yet |

## Structure

Each demo is a self-contained directory with a `README.md` and a `demo.sh` or `demo.py` that drives the scenario.
