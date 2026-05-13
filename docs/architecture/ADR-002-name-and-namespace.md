# ADR-002: Project name and namespace

**Status:** Accepted
**Date:** 2026-04-25
**Decision-makers:** Founding contributor

## Context

The project name is the highest-leverage one-shot decision in the project's life. It appears in every URL, every commit, every social post, every podcast mention. We applied a deliberate rubric and considered three candidates before landing on the final choice.

## Decision

**Brand name: Praxis** (used in casual flow and conversation)
**Formal name: Praxis Linux** (used in headers, SEO, and the namespace)
**Namespace handle: praxis-linux** (used for GitHub org, X handle, package fallbacks)

This mirrors the convention used by *Arch Linux* (brand: Arch; GitHub: archlinux; X: @archlinux), *Kali Linux* (brand: Kali; GitHub: kalilinux), and *Linux Mint* (brand: Mint; GitHub: linuxmint).

### Identifiers

| Surface | Value |
|---|---|
| Display brand | Praxis |
| Long form (SEO/disambiguation) | Praxis Linux |
| GitHub organisation | `github.com/praxis-linux` ✅ verified available |
| Main repository | `github.com/praxis-linux/praxis-linux` |
| Org profile repo | `github.com/praxis-linux/.github` |
| X / Twitter handle | `@PraxisLinux` ✅ verified available |
| Domain (target) | `praxislinux.dev` (preferred), else `praxislinux.org` |
| System hostname default | `praxis` |
| Package name (apt/pacman) | `praxis` |
| Audit log path | `~/.praxis/audit.db` |
| Doctor CLI | `praxis-doctor` |
| Hashtag (social) | `#PraxisLinux` |

## Rationale

### Why "Praxis"

*Praxis* (πρᾶξις) is Greek for *action* — specifically, the moment thought becomes deed. Aristotle ranked it as the highest form of activity. For Marx and Freire, it meant action that changes the world; for Aquinas and the medieval scholastics, it was applied knowledge.

The name aligns with the manifesto's central claim: *apps are answers, not destinations*. The OS is for *doing*, not for navigating menus. The brand carries philosophical and intellectual weight without being pretentious. It is short (six letters), pronounceable in one try by an English speaker, and spellable from hearing it once.

### Rubric scores (out of 8)

| Criterion | Praxis | Aether | Lumpaar |
|---|---|---|---|
| Pronounceable first try | ✅ | ✅ | ❌ |
| Spellable from hearing | ✅ | ⚠️ | ❌ |
| Namespace available | ✅ (with `-os` suffix) | ❌ (heavily contested) | ✅ |
| First-page SEO ownable | ⚠️ (vs Praxis test, PraxisLIVE) | ❌ (commodity) | ❌ (Kuala Lumpur) |
| Carries meaning | ✅ (action) | ✅ (medium/substrate) | ❌ |
| No vulgar/negative connotations across major languages | ✅ | ✅ | ❌ (Urban Dictionary) |
| Short (5–8 letters) | ✅ | ✅ | ✅ |
| Sounds technical-but-warm | ✅ | ✅ | ⚠️ |
| **Total** | **7.5 / 8** | **5 / 8** | **2 / 8** |

### Alternatives considered

- **Aether** — original recommendation. Rejected because `aether-os` and `@aetheros` were unavailable on GitHub and X; the namespace was too crowded to dominate.
- **Lumpaar** — proposed by founder. Rejected for severe SEO collision with Kuala Lumpur misspellings, an Urban Dictionary entry, and unclear pronunciation.
- **Loom** — strong canvas metaphor. Rejected because *Loom* (the screen-recording app) is a well-known existing product and would create immediate brand confusion.
- **Sigil** — considered as a backup; available, distinctive, hacker-friendly. Held in reserve if Praxis encounters trademark issues.
- **Prism** — rejected for residual association with the NSA Prism surveillance program (twenty years old but still surfaces).

### Trademark and SEO risk acceptance

We acknowledge:

- **Praxis** (capitalised, US registration) is a trademark of Educational Testing Service for their teacher certification exam. We are not in their goods/services class (we are operating-system software; they are educational testing). A trademark lawyer should still be consulted before any commercial launch in Phase 3.
- **PraxisLIVE** is an existing live-coding tool. We use *Praxis Linux* (not *Praxis*) wherever ambiguity could arise.
- **createuniverses/praxis** on GitHub is an inactive live-coding project, low collision risk.
- **Praxis** as a philosophical concept will appear in search results. We accept this as a feature, not a bug — it brings thoughtful traffic and supports the brand thesis.

**SEO mitigations:**

1. Lead with "Praxis Linux" in `<title>` tags, README headers, meta descriptions
2. Hashtag `#PraxisLinux` on all social posts
3. Pinned post / first launch post explicitly disambiguates
4. Domain: prefer `praxis.dev` (developer audience) over `.com`

## Consequences

**Positive:**
- Strong, defensible brand with intellectual weight
- Available namespace at `praxis-linux` keeps everything consistent
- Apache 2.0 + open trademark posture lets community use the name freely

**Negative:**
- Trademark review needed before commercial launch (Phase 3 concern, not Phase 0)
- "Praxis OS" string collides with educational testing in some search contexts — mitigated by leading with "Praxis Linux"
- Some users will pronounce it "PRAY-ksis" vs. "PRAK-sis" — both are accepted in English; we don't fight this

## References

- [Manifesto](../../MANIFESTO.md)
- [ADR-001 — Strategic positioning](./ADR-001-strategic-positioning.md)
- Wiktionary, *praxis* — https://en.wiktionary.org/wiki/praxis
- USPTO trademark search — https://tmsearch.uspto.gov (verify "Praxis" in class 9, computer software)
