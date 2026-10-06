# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root: the glossary. Created lazily by `/domain-modeling`.
- **`docs/DECISIONS.md`**: the frozen research and engineering decisions. This is the repo's decision record; there is no `docs/adr/` folder. New decisions are appended to `DECISIONS.md`, not written as separate ADR files.
- The development contract listed in `CLAUDE.md`: `docs/CURRENT_PHASE.md`, `docs/ROADMAP.md`, `docs/EXPERIMENT_PROTOCOL.md`.

If `CONTEXT.md` doesn't exist, **proceed silently**. Don't flag its absence; don't suggest creating it upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates it lazily when terms actually get resolved.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── CLAUDE.md
├── docs/
│   ├── DECISIONS.md          ← decision record (in place of docs/adr/)
│   ├── CURRENT_PHASE.md
│   ├── ROADMAP.md
│   └── EXPERIMENT_PROTOCOL.md
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag decision conflicts

If your output contradicts a frozen decision in `docs/DECISIONS.md`, surface it explicitly rather than silently overriding:

> _Contradicts DECISIONS.md "Battery Eligibility", but worth reopening because…_
