# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report it privately through GitHub: **[Report a vulnerability](https://github.com/praxis-linux/praxis-linux/security/advisories/new)** (the repository's Security tab → "Report a vulnerability"). Only the maintainers see the report. Include:

1. A description of the vulnerability and its potential impact
2. Steps to reproduce (or a proof-of-concept)
3. Affected versions / components
4. Whether you have a proposed fix

We will acknowledge receipt within **72 hours** and aim to provide an initial assessment within **7 days**. We follow responsible disclosure: we will coordinate a fix and a release before any public disclosure, and we will credit reporters in the release notes unless you prefer to remain anonymous.

## Scope

| In scope | Out of scope |
|---|---|
| Agent sandbox escapes | Issues in upstream packages (report to them directly) |
| Cloud audit bypass (cloud calls without toast/log) | Social engineering |
| Privilege escalation via MCP servers | Physical access attacks |
| Data exfiltration through the Canvas | Theoretical attacks with no practical path |
| Kill-switch bypass | |
| A file changed, or code run, without the user's Save, Apply or Run | |

## Supported versions

During Phase 0 (pre-alpha), only the latest commit on `main` is supported. A stable support policy will be published when Phase 1 ships.
