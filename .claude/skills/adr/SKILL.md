---
name: adr
description: Add a new Architecture Decision Record to docs/DECISIONS.md, or a dated amendment to an existing one, in the project's Status/Context/Decision/Consequences format. New ADRs are always Proposed; only the repository owner flips them to Accepted.
argument-hint: new "Short title" | amend 0005 "what changed"
---

Handle `$ARGUMENTS` against `docs/DECISIONS.md`.

**`new "title"`**: take the next number (`ADR-00NN`) and append:

```
## ADR-00NN — <title>

**Status:** Proposed (<today's date>)

**Context:** <the forces; cite plan sections or tickets>

**Decision:** <one paragraph, declarative>

**Consequences:** <what becomes easier, what becomes harder, what must now be true>
```

If it replaces an earlier decision, add `**Supersedes:** ADR-00MM` and add
`**Superseded by:** ADR-00NN` under the old one. Do not delete or rewrite
old text.

**`amend NNNN "what changed"`**: under the existing ADR, append
`**Amendment (<date>):** …` with the new finding and its evidence (for
API-shape findings, the fixture path). Status is unchanged unless the owner
says otherwise.

Never change a Status to Accepted. If the work you are doing depends on an
ADR being accepted, say so in your report; the owner decides.
