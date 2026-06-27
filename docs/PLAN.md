# Rote — Build Plan v2 (post-reassessment pivot)

> **Why this changed:** Gemini 3.5 CU is so capable + re-grounds visually every step that the original demo (a climbing success curve + a cosmetic self-heal) is **flat on a clean app and fakeable on a hard one**, and the one unfakeable win didn't exist in code. The architecture is sound — the **demo centerpiece pivots** to the single thing raw CU can't reproduce or fake. (See "What's kept" at the bottom.)

## Concept — "CU is the teacher and the healer; Rote makes its intelligence amortizable and durable."
Gemini 3.5 CU does the hard zero-shot grounding once. Rote **compiles that into a verified skill** (success-gated by a deterministic checker) and **replays it with ZERO CU calls** — and when the UI drifts, escalates a single step back to CU to re-ground and patch the skill. The system gets **cheaper AND more robust** with experience. Theme: Continual Learning. Target: Best Use of Gemini CU ($5000).

## The unfakeable centerpiece — the CU-call collapse
Headline metric = **CU model calls (+ $ + latency)**, live HUD: **"CU calls: 8 → 0 (verified replay)."** The only number raw gemini-3.5-CU can never reproduce and a judge cannot fake. NOT the success curve (saturates on a capable model + reads as hand-tuned).

**Enabling compiler change:** `compile_skill` KEEPS each step's executed **action + a small target-crop** of the pre-action screenshot (both already in `Step`) as a **visual precondition**, alongside the semantic `target_desc`. On replay: cheap non-CU re-localize → hit ⇒ execute the cached action (0 CU calls); miss (UI drift) ⇒ escalate that ONE step to CU + patch the skill. Self-heal is the SAME engine's escalation path — provably not blind coordinate replay. Floor if the per-step gate is flaky: one verify CU call per skill (N→1) — still unfakeable; report the REAL measured number, never a hoped-for 0.

## Integrity spine — success-gated memory
`Trajectory.success` is filled ONLY by the deterministic checker reading `/state`, NEVER the model. Pitch against the literature: ~52–59% of competitors' "success"-labeled memory came from FAILED self-reported runs → memory that rots. Rote's checker-gated induction = memory that doesn't.

## Frozen contracts → `app/schemas.py` (do not change without telling all of us)
`Task` · `Step` (per-step `intent` + `coords` + `screenshot_path`) · `Trajectory` (`success` = checker only) · `Skill` (now also caches per-step action + target-crop as a visual precondition).

## The arena MUST be hard for zero-shot (not clean)
Clean app → zero-shot ~100% → flat curve → no demo. Build deterministic-checkable AND hard: hidden/overflow nav (Dispute behind "⋯ More"), a required pre-filter before the row exists, a required sub-step (reason dropdown in a modal), a decoy control, long conditional row-selection. **GO/NO-GO: zero-shot base success measurably < ~70%, steps ~2–3× the skilled path** (measured by `skill_inject_check.py` at H6).

## The hero — structural-mutation, three bars: "a stale skill is WORSE than no skill"
1. zero-shot (no skill) **PASS** on the mutated UI → control proving the model + task are fine.
2. inject the **STALE** skill → confident-but-wrong recipe anchors past a now-required step → real circuit-breaker **FAIL** (red X).
3. **repair** (precondition/landmark miss → re-compile the shortcut) → **PASS** in fewer steps.
Mutation must be STRUCTURAL (move Dispute behind the overflow + add a required reason dropdown), never cosmetic. NEVER wire "naive replay" to offline coord-firing — that's literally RPA.

## Owner tasks (post-pivot)
- **A + B — Jin (+ Claude): the engine + skill core + replay.** CU loop (done), `run_episode` (done); **`compile_skill`** (Trajectory → Skill, caches action+crop, success-gated); **the verified deterministic-replay engine** (0-CU-call replay + single-step CU escalation = unified self-heal); the `skill_inject_check` go/no-go.
- **Atlas registry — Riccardo:** store/retrieve/dedup `Skill`s in MongoDB Atlas (vector retrieval by task embedding), conformed to the `Skill` schema. (His `query` db work re-points here.)
- **C — ikjun: the HARD arena + checker + eval.** AcmeBilling (hard, per above), deterministic `check_task`, structural mutations via `/reset?mutation=`, 15–20 held-out variants, `eval_harness`.
- **D — demo + pitch.** The live "CU calls" HUD, the three-bar hero, a secondary same-family success panel, the pitch ("teacher and healer" + success-gated memory), the offline replay fallback, the MiniMax/MCP closing vision.

## Checkpoints
- **H6 — GO/NO-GO:** run `skill_inject_check.py` on the hard arena. Require zero-shot base < ~70% AND large positive Δsteps/Δsuccess from a compiled skill. Inject a mismatched skill → confirm no DIP (gate retrieval by similarity if it does). If Δ≈0, the curve is dead → lead on the CU-call collapse + hero only.
- **H10:** verified deterministic replay working → live "CU calls: N → 0" on a held-out task.
- **H14:** structural-mutation hero (zero-shot PASS / stale FAIL / repaired PASS) pre-vetted on 2–3 mutations.
- **H18:** secondary success panel (same-family held-out) + Atlas registry live.
- **H22:** full arc on the stage laptop + offline replay fallback; MiniMax/MCP beat if core solid.
- **H22–24:** freeze, record VO, 2 dry runs, submission video.

## Risk register (re-ordered post-pivot)
1. **Flat curve / decorative skill (the #1 threat)** → arena MUST be hard (zero-shot < 70%); gate on `skill_inject_check` at H6; if flat, lead on the CU-call collapse + hero (both survive saturation).
2. **Contrived self-heal** → structural mutation only; zero-shot control proves the task; route the FAIL through the real circuit breaker; pre-vet 2–3, run the most reliable live.
3. **"It's RPA"** → replay is gated on a visual precondition + escalates to CU on drift; never fire stored coords blindly; show CU re-grounding live on the heal.
4. **Non-monotonic ablation** → untested retrieval can DIP; gate retrieval by a similarity threshold; test a mismatched skill.
5. **Preview model** → `USE_LEGACY_CU` swap; pre-recorded backup. (Interactions API is GA.)
6. **Wifi/API dies on stage** → offline `replay.py` + recorded VO; `/reset` before each scored task.

## Demo (3 min) — CU stars live, twice
problem + "teacher and healer" → CU grounds a genuinely-hard task **zero-shot, live** (CU brilliant) → compile a verified skill (Atlas row appears) → **verified replay: "CU calls 8 → 0"** (the unfakeable money shot) → structural UI mutation → **stale skill FAILS** (red X) while **zero-shot PASSES** (control) → CU **re-grounds + repairs** the skill live → **PASS in fewer steps** → [secondary] same-family held-out success panel + the success-gated-memory integrity line → MiniMax pulls the skill over MCP (closing vision).

## What's KEPT (most of the build)
GA CU loop, `run_episode` + persistence, the success-gated `Trajectory` contract, semantic re-grounded skills, Atlas storage, calibration, the fixture, `skill_inject_check` (now the go/no-go gate). The pivot is the **centerpiece metric + arena difficulty + the cache-action-crop compiler change** — not a rewrite.
