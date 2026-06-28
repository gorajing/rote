# Rote — Shipped State (supersedes the v2 browser-crop plan)

> **What this is now:** this file used to be the full build plan, centered on a **browser-only target-crop demo**. The project pivoted twice past it. It's now a record of what actually shipped. **For the full, current detail see [`README.md`](../README.md).** The original plan survives only as a labeled *History* note at the bottom.

## Concept — "CU is the teacher and the healer; Rote makes its intelligence amortizable and durable."
Gemini 3.5 CU does the hard zero-shot grounding once. Rote **compiles that into a verified skill** (success-gated by a deterministic checker reading ground truth) and **replays it with ZERO CU calls** — and when the UI drifts, escalates a single step back to CU to re-ground and patch the skill. The system gets **cheaper AND more robust** with experience. Theme: Continual Learning. Target: Best Use of Gemini CU.

## The unfakeable centerpiece — the CU-call collapse
Headline metric = **CU model calls (+ $ + latency)**, verified by ground truth: **"CU calls: N → 0 (verified replay)."** The one number raw gemini-3.5-CU can never reproduce and a judge cannot fake. NOT a success curve (it saturates on a capable model and reads as hand-tuned).

## Integrity spine — success-gated memory
`Trajectory.success` is filled ONLY by the deterministic checker reading `/state`, NEVER the model. Pitch against the literature: ~52–59% of competitors' "success"-labeled memory came from FAILED self-reported runs → memory that rots. Rote's checker-gated induction = memory that doesn't.

## Shipped today
Three tracks. All replay **model-free on the happy path (0 CU)** and verify against ground truth, falling back to a single self-heal CU call on drift. Reliability is uneven across tracks — the browser/fusion engine is the demo-safe spine; live cold-launch of heavy desktop apps (Word) and the web→native hybrid paste are the model-bound frontier (see the honest per-track notes below and in [`README.md`](../README.md)).

### 1) Desktop macro pipeline (macOS, condition-checked)
`app/desktop_cu.py` (the doer) → `app/desktop_skill_compiler.py` + `app/universal_skill_compiler.py` (a 2nd Gemini pass compiles the trajectory into a macro) → `app/verified_replay.py` (per-step pre/postconditions, retries, fallbacks; drives the **real desktop** via `pyautogui`). Localized repair via `app/skill_repair.py`; versioned local promotion via `app/local_skill_registry.py`. Notch HUD (`app/notch.py` + `app/desktop_hud.py`) and voice runner (`app/voice_agent.py`).
- **Optimistic replay is opt-in.** `replay_verified(..., optimistic=False)` is the default verified per-step contract; `optimistic=True` is the blind happy-path speedup, opted in only at the call sites (`desktop_hud.py`: `optimistic=not a.repair`; `desktop_cu.replay`: `optimistic=True`). PR #9 ("make optimistic replay opt-in") is **merged to main**.

### 2) Surface-agnostic fusion engine (`app/fusion/*`)
One tiered dispatcher replays a compiled `FusedSkill` across **browser and desktop**, routing each step to the cheapest tier that reproduces it: **keyboard** (fire the shortcut blind, 0 perception) → **crop** (re-localize the step's visual precondition with a template match, 0 model) → **model** (escalate exactly ONE step to CU on drift = the self-heal). Files: `contract.py` (the `FusedSkill` + `Verifier` spine), `dispatch.py` (the tiered dispatcher), `compiler.py` (trajectory → `FusedSkill`), `skill_store.py` (persist + promote skills across runs), `recall.py` (plain-language intent → recall → 0-CU replay, **local-first**), `hybrid.py` (learned cross-surface web→Mac-app skills). Every step is gated by a ground-truth `Verifier`.

### 3) Browser arena + 11-task eval
`app/controlled_app/` — **AcmeBilling**, a deterministic Flask app at `http://localhost:8800` with a `/state` snapshot endpoint and `/reset?variant=…` for structural UI mutations. `app/tasks.py` — **6 train + 5 held-out** across **three families** (`invoice_action`, `row_find_act`, `settings_change`). `app/checker.py` — deterministic checkers (`dispute_workflow`, `row_find_act`, `settings_change`) read `/state`, so "success" is never self-reported. `app/eval_harness.py` — skills-off ablation / UI-mutation / repair eval.
- **Metric, honest:** CU **N → 0**, verified by ground truth. Generalization is uneven by family: `settings_change` replays reliably at 0 CU verified; `row_find_act` (refund) generalizes but a held-out row's crop drift can cost **CU=1** before re-grounding; the modal-heavy `invoice_action` (dispute) workflow is the **hard frontier** (drift escalates, sometimes needs a full recompile). Cold-learn is a live, stochastic Gemini run — a representative run lands ~3/5 of the sampled tasks at 0 CU verified — so the durable claim is the **mechanism** (drift paid once → amortizes to 0, always ground-truth-gated), proven deterministically by the hermetic test bank, not a fixed per-task score.

Reproduce (canonical commands in [`README.md`](../README.md)):
```bash
python -m app.controlled_app.server          # arena on :8800
```

## Frozen contracts (two — do not change a field without telling everyone)
- `app/schemas.py` — the browser/desktop **Trajectory spine**: `Task` (`checker` = key into `checker.CHECKERS`) · `Step` · `Trajectory` (`success` = checker only, NEVER the model) · `Skill`.
- `app/fusion/contract.py` — the fusion **`FusedSkill`**: typed `Step`s each with a `Precondition`, plus a `Verifier` spec decided against GROUND TRUTH.

## Arena facts (corrected)
- Reset / mutate routes: **`POST /reset?variant=<name>`** and **`GET /mutate/<name>`** — *not* `/reset?mutation=…`.
- Variants: `baseline`, `move_dispute_to_cases`, `relabel_export`.
- The structural mutation is **`move_dispute_to_cases`**: the "Mark Disputed" action is relocated to a separate **Cases** page (`/cases/dispute/<id>`) — the self-heal stress test. The **reason dropdown is baseline** (present in every variant) — it is *not* a mutation.
- Split: **6 train / 5 held-out**, three families.

## Not built / partial integrations (do NOT over-claim)
- **Atlas registry** (`database/api.py`) — two lanes. MCP uses Atlas as a descriptor index (`doc_type=executable_skill`) and resolves executable macros from the local versioned registry, but the old tracked local seed catalog was intentionally deleted after the DB pivot, so this lane is empty until runtime/demo skills are promoted locally. Voice/chat use flattened, checker-backed replayable skill docs (`doc_type=skill`) through `app.skill_store`. Newly learned fresh/hybrid artifacts do **not** auto-seed Atlas yet. Fusion recall is **local-first** and does not depend on Atlas.
- **MCP / MiniMax closing beat** — FastMCP skill search/inspection/replay is built in `app/mcp_server.py`; the MiniMax closing-vision beat is not built.
- **`eval_harness.py` compile / store / repair paths** are **stubs** (`_compile_successes`, the `repair_library` branch) — guarded imports that no-op when the module isn't present.

## History — the original v2 plan (superseded)
The pre-pivot centerpiece was a **browser-only** demo: `compile_skill` caching a per-step **target-crop** as a visual precondition, a three-bar "a stale skill is WORSE than no skill" hero on a single hard AcmeBilling task, an Atlas row appearing live, and a MiniMax-over-MCP closing beat. The **crop-grounding idea survived and generalized** into the fusion engine's crop tier (now surface-agnostic — browser **and** desktop); the single-task browser hero and the live-Atlas / MCP beats did not ship as the centerpiece. The metric that endured is the **CU-call collapse (N → 0, ground-truth-verified)**.
