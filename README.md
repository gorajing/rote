# Rote

**A Gemini 3.5 Computer Use agent that learns reliable browser skills by doing — and repairs them when the UI changes.**

Computer-use agents are powerful but amnesiac: every run is disposable. Rote watches Gemini 3.5 Flash operate a browser, **compiles the successful trajectory into a reusable, visually-grounded skill**, and when the page changes it **repairs the skill from the new screenshot** instead of breaking — proving it learned with a held-out success curve + a skills-off ablation on a deterministic app.

> AI Engineer World's Fair Hackathon 2026 · Theme: **Continual Learning** · Target: **Best Use of Gemini Computer Use**

## The idea in one loop
```
computer-use run → annotated trace → compile skill → replay → UI drift → self-repair
```

The **`Trajectory`** — an annotated pixel trace (`intent → action → coords → screenshot`) — is the central object. Everything is a function of it:

- **observe** = the trace itself (each step's `intent` is Gemini 3.5's own per-step reasoning)
- **eval** = a deterministic checker on app state — *never* the model's self-report
- **judge / localize** = the step whose post-screenshot stops matching its `intent` is the failure point
- **improve** = a skill is the compressed *intent-sequence*, re-grounded visually at replay (not coordinate replay → **not RPA**)

## Module map
| File | Owner | Responsibility |
|---|---|---|
| `app/schemas.py` | all | frozen contracts: `Task`, `Step`, `Trajectory`, `Skill` |
| `app/config.py` | all | models, viewport, flags |
| `app/cu_runner.py` | A | Gemini CU loop → `Trajectory` (intent capture, circuit breaker, skill injection) |
| `app/executor.py` | A | Playwright action executor (full 3.5 browser action space) |
| `app/trace.py` | A | trajectory recorder |
| `app/skill_compiler.py` | B | trajectory → skill *(todo)* |
| `app/skill_registry.py` | B | MongoDB Atlas store + vector retrieval *(todo)* |
| `app/repair.py` | B | failure → repaired skill *(todo)* |
| `app/checker.py` | C | deterministic success via app state *(todo)* |
| `app/eval_harness.py` | C | held-out eval + skills-off ablation *(todo)* |
| `app/controlled_app/` | C | AcmeBilling deterministic arena + UI mutations *(todo)* |
| `app/mcp_server.py` | B/D | skill commons over MCP *(P1)* |
| `app/demo_ui.py`, `judge_console.py`, `replay.py` | D | proof surface + offline fallback *(todo)* |

See `docs/PLAN.md` for the full 26-hour plan, contracts, checkpoints, and risk register.

## Quickstart
> macOS has no bare `python` — use **`python3`** to create the venv. After `activate`, plain `python` works.
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # add GEMINI_API_KEY, ROTE_MONGO_URI

# smoke-test the CU loop against a public page (no controlled app needed yet):
export GEMINI_API_KEY=...
python -m app.runner --url https://www.google.com --intent "Search for 'Gemini API'."
```
`app/cu_runner.run_task(task, page)` drives Gemini CU through one task and returns a `Trajectory`. It needs the controlled app (Owner C) running to drive against.

## Build checkpoints (26h)
- **H6** — CU completes the hero workflow on the controlled app, checker PASS; empty-library run = the ablation baseline. *Non-negotiable.*
- **H12** — self-heal end-to-end: mutate UI → naive replay fails → repair → PASS.
- **H18** — climbing held-out curve + ablation delta; snapshot the gen-N library.
- **H22** — full demo arc on the stage laptop + offline replay fallback.

## Sponsors
Gemini 3.5 Computer Use (core) · MongoDB Atlas (skill registry = self-improvement memory) · DigitalOcean (parallel eval fleet + host) · MiniMax (the second agent that pulls a skill over MCP).

## License
MIT
