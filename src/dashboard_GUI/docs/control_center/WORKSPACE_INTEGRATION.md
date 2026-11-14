# GUI and research workspaces — Git audit and integration story

Audit date: 2026-10-28. This describes observed source/Git state, not a scientific
phase approval. The user requested publication of the GUI, inspection of both
workspaces and a complete integration explanation.

## One repository, two worktrees

Both folders use origin `git@github.com:ibrahim-1811/rnd_mrta_ilp_ws.git`:

| Folder | Branch | Role |
|---|---|---|
| /home/ibrahim/rnd_mrta_ilp_ws | traffic/rmf-integration | Current verified dashboard plus original scientific foundation |
| /home/ibrahim/rnd_mrta_ilp_research | research/outcome-aware-ilp | Newer scientific M0 integrity work and local M1 diagnostic work |

They share Git history/objects, but have separate checked-out files, ignored
runtime output and environments. Committing/pushing the GUI does not copy it into
the research worktree. The research branch was already at remote commit 163c6a4,
`fix(research): complete M0 integrity and reproducibility`. Its documentation's
older "not committed" phrasing is stale relative to the actual Git commit.

At audit, research has six untracked M1 files: two probe/analysis scripts,
congestion/outcome-signal/rollout modules, and their diagnostic test. They are
work in progress and were neither modified, committed nor pushed by this task.
GUI-root `.codex/` and empty `26.2.2` are unrelated existing untracked files and
remain outside the GUI commit. The already-deleted duplicate plan is included in
the GUI checkpoint; its canonical plan has identical original SHA256 contents.

## What the dashboard currently does

Stages 1–6 wrap the original headless simulator: local API, process supervision,
actual observed single-run state and recordings, React Operations/History/System,
matrix setup, guarded paired comparisons, decision/outcome inspection, immutable
single-run CSV inputs, one-command startup and verification. Readable reports
create separate offline HTML/PNGs, without rewriting scientific outputs.

The verified 50-robot pilot used eight strategies, 200 orders, three seeds, q=0,
and the GUI branch's pre-M0 source. It completed 24/24. Keep it as an exploratory
integration pilot, with its original source hash/schema/provenance. It is not an
M0/q=3 primary-research result and must not be mixed with that condition.

Git publication includes source, tests, scoped dependency manifests/lock and docs.
`var/` runs/reports/SQLite/logs, environments, node_modules and dist remain local
and ignored. A second checkout does not automatically see these artifacts. Use
explicit read-only dataset roots or an exported dataset archive in a future
integration; copying Git source alone is not dataset transfer.

## Why merging alone is insufficient

The research M0 commit changes scientific interfaces the GUI consumes:

1. `build_manifest` requires strategy_identity. The GUI observed wrapper and
   final artifact validator currently call the older interface.
2. The matrix runner now supports `--quantum`, `--window`, `--purpose` and records
   those fields. The old GUI request/preview/resume ownership spec lacks them.
3. `check_strategies` guards per-strategy identity hashes. The GUI reader must
   preserve/validate these identities and invoke the research comparison gates.
4. Research seed roles separate training 101–110, validation 111–115, evaluation
   1–30, and reserved final 201–230. GUI forms must expose/enforce actual purpose
   semantics and never present ILP training on evaluation seeds.
5. The observed adapter pins the original runner's source bytes. On the research
   runner it correctly fails closed. Updating the pin without reproducing the
   new setup and rerunning exact tiny parity would remove that protection.
6. The research checkout currently has no `.pixi` environment. Its runtime paths
   must be deliberately provisioned/configured without changing solver versions;
   the launcher's current relative interpreter check cannot simply be assumed.

M0 also fixes decision/outcome linkage and classifies failures. Keep those
scientific improvements; adapt the dashboard boundary rather than reverting
research behavior to fit an older screen. Work-in-progress M1 modules must not
be silently included in an integration checkpoint.

## Safe integration sequence

1. Publish this verified GUI branch; retain the scientific research branch.
2. Keep a recoverable checkpoint of M1 work before an explicit integration.
3. Bring only the reviewed GUI delivery commit onto a dedicated integration
   branch based on research M0; resolve documentation against research authority.
4. Adapt command/manifest/identity/purpose interfaces and environment selection.
5. Keep observation unavailable until the updated wrapper proves parity with
   the research runner; original CLI mode is the fallback.
6. Run GUI tests/build, M0 integrity tests, tiny original-vs-observed parity,
   cancellation/restart/proxy tests and a small q=3 experiment.
7. Only then run primary research campaigns with frozen seed roles and settings.
   ILP data/teacher/rule-learning milestones follow their scientific plan.

No merge/cherry-pick or research-worktree mutation was performed in this audit.
This preserves ongoing M1 work and avoids claiming untested compatibility.

## Verification for publication

- GUI backend: 152 passed (one existing Starlette/AnyIO warning).
- Frontend: 22 passed; TypeScript and production build passed.
- Research tracked M0 gate: 38 passed in 15.12 seconds, using the existing GUI
  scientific interpreter with research source packages on PYTHONPATH. No package
  install, configuration edit or environment symlink was made in research.
- This is a focused M0 check, not a claim that the entire research/M1 suite passed.
- Prior launcher/parity/HTTP evidence is documented in Stage 6 and pilot reports.
