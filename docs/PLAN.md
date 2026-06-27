# Rote — Build Plan (4 people, ~26h)

## Concept
Gemini 3.5 Computer Use agent that compiles successful browser trajectories into **executable, visually-grounded, self-healing skills** (MongoDB Atlas), and proves it learned via a **held-out success curve + a skills-off ablation** on a self-built deterministic app. Hero demo beat: **mutate the UI → naive replay fails → CU repairs the skill from the new screenshot → PASS.** Theme: Continual Learning. Target: Best Use of Gemini CU ($5000).

## Positioning (say this — never "doesn't exist")
Self-healing browser skills exist (Skyvern, Browser Use's harness); skills-over-MCP and skill marketplaces exist (Browser Use Marketplace, the Skills-Over-MCP working group, StackOverflow for Agents); cross-agent CU-skill sharing exists (SkillWeaver). **Novel only at the intersection:** visually-grounded + self-healing + success-gated CU skills that agents compile from their own runs and improve over MCP. Lead the pitch with the live self-heal; name the neighbors.

## Frozen contracts → `app/schemas.py` (do not change without telling all 4)
`Task` · `Step` (carries the per-step `intent`) · `Trajectory` (central object; `success` filled by the checker, never the model) · `Skill` (semantic `target_desc` steps, re-grounded at replay).

## Owner tasks
**A — CU engine** (`cu_runner`, `executor`, `trace`): GA Interactions loop → Trajectory; denorm 0-999 coords vs 1280×720; circuit breaker (`MAX_TURNS`/`STUCK_AFTER`); inject skills as intent-sequences. *(written — starter in repo)*
**B — Skill system** (`skill_compiler`, `skill_registry`, `repair`): distill success-trajectory → parameterized Skill (semantic targets); Atlas vector store + retrieve + dedup/merge; repair = diff intended-vs-actual intents/screenshots → patch preconditions/targets, deprecate low-success skills.
**C — Arena + rigor** (`controlled_app/`, `checker`, `eval_harness`): AcmeBilling Flask app (`/billing`, `/state`, `/reset`, mutations); deterministic checker reading `/state`; held-out task variants; `run_eval(split, use_skills)` → metrics; **build this FIRST**.
**D — Integration + demo** (`demo_ui`, `judge_console`, `replay`, `mcp_server`, pitch): the generation loop; live skill-table + success/steps chart (no Streamlit — banned); judge console (validated, locked to the 2 sites); offline replay fallback; MCP commons + MiniMax second-agent beat (P1).

## Checkpoints
- **H6 (A+C):** CU completes hero workflow on arena, checker PASS; empty-library held-out run = ablation baseline. *Non-negotiable.*
- **H12 (B):** self-heal end-to-end (mutate → fail → repair → PASS).
- **H18 (C):** climbing curve + ablation delta as stored data; **snapshot gen-N library**.
- **H22 (D):** full arc on stage laptop + replay fallback; MCP/cross-agent beat if core solid.
- **H22–24:** freeze, record VO, 2 dry runs, submission video by ~H23.

## Risk register
1. **Self-heal reliability** → pre-vet 2–3 mutations w/ known-good repairs; 1 honest live heal + rest pre-recorded.
2. **Curve must move** → tight skill families, k=2–3, low step-cap = many episodes; snapshot gen-N the night before.
3. **Preview model** → `USE_LEGACY_CU` one-line swap to gemini-2.5; pre-recorded backup. (API surface itself is GA.)
4. **"It's RPA"** → skills store semantic targets re-grounded visually; show heal on a mutated UI + unseen tasks.
5. **Eval latency / rate limits** → pre-compute eval before pitch; parallelize browsers on DigitalOcean; backoff.
6. **Wifi/API dies on stage** → offline `replay.py` (logged trajectories → checker) + recorded VO; `/reset` before each scored task.
7. **4-person integration** → freeze contracts H0; a measurable system by H6 is non-negotiable; sponsors additive past H6.

## Demo (3 min)
problem + positioning → CU completes workflow live → compile skill (Atlas row appears) → **mutate UI → naive replay fails → CU repairs from screenshot → PASS** → held-out curve + steps-collapse + skills-off ablation (eval ran on DigitalOcean) → MiniMax second agent pulls the skill over MCP → close (the commons vision).
