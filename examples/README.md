# Examples

End-to-end demo flows for Praxis.

## Status

The launch demo is ready to record: [`90s-demo`](90s-demo/README.md). The others are still planned — see [Roadmap](../docs/ROADMAP.md).

## Planned demos

| Demo | Description | Requires |
|---|---|---|
| `conversation-basic` | Chat, streaming response, memory across turns | M0.9 |
| `file-search` | "Find all Python files I edited this week" | M0.10 |
| `code-edit-run` | Open, edit, and run a script from intent | M0.11 |
| `privacy-audit` | Trigger a cloud call; observe toast + audit log entry | M0.12 |
| `90s-demo` | The M0.13 launch demo — intent → mode-switch → action | M0.13 |

## Structure (M0.13)

Each demo is a self-contained directory with a `README.md` and a `demo.sh` or `demo.py` that drives the scenario.
