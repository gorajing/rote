# Rote — team protocol (converge, don't expand)

We have more than enough capability. The gap to "best tool" is **coherence, robustness, and
integration** — the human-owned work. Until further notice we **converge**, not expand.

## Rule 0 — main is protected (this is why it went red)
- **No direct pushes to `main`.** Ever. (A direct push is what broke it.)
- Merge only via **PR + green CI**. Red CI = nobody merges on top.
- A red `main` is everyone's problem and blocks everyone. Greening it is priority #1, today.

## The order of work (do these in sequence, not in parallel)
1. **Green the foundation (now).** Reconcile optimistic-replay with ikjun's tests, fix the
   `NoneType` crash, get `main` green and trustworthy. Nothing else merges until this lands.
2. **Make the core engine robust.** Stop guessing on the two generalization bugs — instrument
   (verbose + real end-state), find root cause, fix, then run the **full 11-task bank** for an
   honest generalization number. "Works on any task" is the bar, not "works on the hero demo."
3. **Converge the architecture (the big lever).** One skill schema → one store → vector DB as the
   retrieval index (recall → replay → learn). Two engines + three stores is debt. Collapse it.
4. **Bulletproof the demo + the unfakeable metric:** CU N→0 verified + self-heal + recall +
   cross-device, reproducible with no live-fragility.
5. **Converge the team.** Everyone on the shared architecture + green main. Voice / MCP are
   **optional flourishes on a coherent core** — not destabilizers competing for the demo.

## Ownership (no uncoordinated fan-out)
- One owner per area above. Owners listed in the PR description.
- Before starting new work, check it's not someone else's in-flight PR.
- Disagreement on architecture → 10-min sync, decide, write it here. Don't fork silently.

## Definition of done for a merge
- [ ] CI green (the full test bank, not a subset)
- [ ] Touches the shared schema/store, not a new parallel one
- [ ] A teammate reviewed the PR
- [ ] No new direct-to-main commits

> One sentence: the team has built enough power; the work left is making it cohere, work robustly,
> and integrate — convergence and depth.
