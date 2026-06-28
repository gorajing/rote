# Rote

**A Gemini 3.5 Computer Use agent that learns reliable computer skills by doing — then replays them with no model in the loop, and repairs them when the UI changes.**

Computer-use agents are powerful but amnesiac and slow: every run is disposable, and every step is a multi-second round-trip to a vision model. Rote watches **Gemini 3.5 Flash** operate a real computer, **compiles the successful run into a reusable keyboard-first skill** (authored by a second Gemini pass — not by hand), and then **replays that skill deterministically with zero model calls** — ~8× faster and free. When a step drifts, it falls back to the model to self-heal.

> AI Engineer World's Fair Hackathon 2026 · Theme: **Continual Learning** · Target: **Best Use of Gemini Computer Use**

> 🎙️ **Talk to it.** A LiveKit voice agent runs these skills by voice and narrates live:
> `python3 -m app.voice_agent console` then say *"calculate 52 times 68 and save it in Word."*
> What's new on the `feat/v0.1` branch (voice + optimistic replay + fixes) is summarized in
> **[docs/feat-v0.1-notes.md](docs/feat-v0.1-notes.md)** — start there.

> 🌊 **DigitalOcean partner stunt.** The voice agent also understands *"ship it on
> DigitalOcean"* / *"run the DigitalOcean demo"*. By default it rehearses the exact `doctl`
> steps without touching cloud state. To run it live, install `doctl`, set `DO_APP_ID`,
> optional `DO_APP_URL`, and `ROTE_DO_DEMO_LIVE=1` in `.env`, then say the command.

## The idea in one loop
```
computer-use run  →  intent log  →  Gemini compiles a macro  →  deterministic replay  →  (drift → self-heal)
   slow, model            the trajectory      a 2nd Gemini pass         fast, NO model, NO tokens
```

Two models, two roles:
- **The doer** — Gemini 3.5 Flash drives the computer from screenshots and leaves an annotated **intent log** (`intent → action → coords`, each `intent` is the model's own per-step reasoning).
- **The compiler** — a second Gemini 3.5 pass reads that log and **writes a keyboard-first replay macro**: it drops fumbles, swaps visual clicks for shortcuts (`Cmd+N`, `Cmd+S`, `Cmd+B`, `Cmd+D`), and launches apps reliably. Replaying that macro needs **no screenshots and no model calls**.

---

## ✅ What works right now — the macOS desktop pipeline

This is the part that runs end-to-end today. It drives the **real macOS desktop** (not a browser) via `pyautogui`, and has produced real `.docx` files, formatted documents, and a multi-app Calculator→clipboard→Word flow.

### Stage 1 — record (the doer)
```bash
python3 -m app.desktop_cu \
  --max-turns 26 \
  --trace traces/run.json \
  --intent "Create a new Microsoft Word document, type 'Hello', and save it to the Desktop as 'demo'."
```
Drives the desktop with Gemini 3.5, prints each step + a **latency breakdown** (the model inference is ~85% of wall-clock), and writes the intent log to `--trace`.

### Stage 2 — compile (the compiler)
```bash
python3 -m app.desktop_skill_compiler --trace traces/run.json --out database/skills/demo.macro.json
```
A Gemini 3.5 pass turns the intent log into a keyboard-first **macro** in `database/skills/`. The macro JSON is authored entirely by the model.

### Stage 3 — replay (fast, no model)
```bash
python3 -m app.desktop_cu --replay database/skills/demo.macro.json          # plain
python3 -m app.desktop_hud --skill create_word_file                         # with the notch HUD
```
Runs the macro with `pyautogui` only — **0 tokens, 0 model calls**. Self-heal: if the post-replay success check fails, it can hand back to the model (see `app/desktop_speed.py`).

### Step-level self-improvement

Versioned macro v2 skills verify every state transition locally with macOS UI state and final
file checks. A failed transition stops the replay; `--repair` asks Gemini for a bounded patch to
that step only, replays the full candidate from a clean state, and promotes it only after the
deterministic checker passes.

```bash
# verified, model-free replay
python -m app.self_improve replay create_word_file

# deterministic stale fixture: localize, repair once, validate, and promote the shared subskill
python -m app.self_improve demo stale_create_word_file \
  --metrics traces/self_improvement.json

# inspect active/candidate/rejected versions
python -m app.self_improve history stale_ensure_blank_document

# show verification/repair/promotion states in the notch HUD
python -m app.desktop_hud --skill stale_create_word_file --repair
```

Runtime versions are stored atomically under `database/skills/registry/` and are intentionally
gitignored. `create_word_file` and `meeting_notes` share `ensure_blank_document` and
`save_word_document`, so a promoted subskill repair transfers to both workflows.

The same replay and repair engine supports both surfaces:

```bash
# Multi-app desktop workflow with clipboard and DOCX verification
python -m app.self_improve replay calculator_to_word_save

# Semantic Playwright workflow with HTTP application-state verification
python -m app.browser_self_improve replay acme_settings_email --headless
python -m app.browser_self_improve repair acme_settings_email --headless

# Compile a new successful trace for either surface
python -m app.universal_skill_compiler --surface browser --trace traces/run.json --out database/skills/new.macro.json
```

Browser skills use semantic targets (`role`, `label`, `text`, `testid`, or `css`) rather than
stored coordinates. Final checkers can validate browser state, files, clipboard state, or a
deterministic HTTP JSON endpoint. New applications only need a macro and an externally verifiable
checker; the versioning and localized repair lifecycle is shared.

### Two reliability features worth knowing
- **Dynamic waits** (`ensure_app` / `settle` in `app/desktop_cu.py`) — instead of a fixed `sleep`, it polls macOS for app-readiness and watches the screen locally until it stops changing, then proceeds. An already-open app continues in ~0.3s instead of 6s.
- **Notch HUD** (`app/notch.py`) — a Dynamic-Island-style status pill at the MacBook notch (AppKit via PyObjC), narrating each step with a live spinner + progress bar so a 2s app-load never looks frozen. Shows on **all Spaces** and never steals keyboard focus.

### Macro format (`database/skills/*.macro.json`)
A flat list of steps replayed top to bottom. Ops:
```json
{"op": "open_app", "app": "Microsoft Word", "launch_wait": 6, "why": "..."}  // self-checks: focus if open, launch+wait if not
{"op": "hotkey",   "keys": ["command", "s"], "why": "..."}                    // a shortcut chord
{"op": "key",      "key": "return", "why": "..."}                            // a single key
{"op": "type",     "text": "Hello", "why": "..."}                           // type literal text
{"op": "wait",     "seconds": 2, "why": "..."}                              // dynamic: proceeds when the screen settles, capped at `seconds`
```

### Measured results (live, this branch)
| Task | Doer (model) | Replay (compiled) | Speedup |
|---|---|---|---|
| Create a Word file | 130s · 124,859 tok | 16s · **0 tok** | ~8× |
| Formatted meeting notes (bold title + lines) | 175s · 173,351 tok | 23s · **0 tok** | ~7.6× |
| Multi-app: Calculator → clipboard → Word | 132s · 256,063 tok | 17–35s · **0 tok** | ~4–8× |

---

## Module map
| File | Status | Responsibility |
|---|---|---|
| `app/schemas.py` | ✅ | frozen contracts: `Task`, `Step`, `Trajectory`, `Skill` |
| `app/config.py` | ✅ | models, viewport, flags; auto-loads `.env` |
| **Desktop track (works today)** | | |
| `app/desktop_cu.py` | ✅ | desktop doer (Gemini CU + pyautogui), `replay()`, dynamic waits (`ensure_app`/`settle`) |
| `app/desktop_skill_compiler.py` | ✅ | **the desktop compiler** — Gemini reads an intent log → writes a macro |
| `app/verified_replay.py` | ✅ | step pre/postconditions, parameter binding, subskill expansion, deterministic checker |
| `app/verification.py` | ✅ | shared desktop/browser condition DSL and file/HTTP/state checkers |
| `app/browser_backend.py` | ✅ | semantic Playwright execution backend for the shared replay engine |
| `app/local_skill_registry.py` | ✅ | local candidate/version history and success-gated promotion |
| `app/skill_repair.py` | ✅ | localized Gemini patch generation and clean end-to-end validation |
| `app/notch.py` | ✅ | Dynamic-Island notch HUD (AppKit / PyObjC) |
| `app/desktop_hud.py` | ✅ | run a replay with the notch HUD |
| `app/desktop_speed.py` | ✅ | cold-CU vs compiled-replay speed proof (+ self-heal fallback) |
| `app/desktop_eval.py` | ✅ | skills-off ablation (cold vs skill-injected) |
| `app/hud.py` | ⚠️ | early Tkinter HUD — superseded by `notch.py` (Tk can't render at the notch) |
| `database/skills/*.macro.json` | ✅ | Gemini-authored, replayable skills |
| `database/api.py` + `data/` | ✅ | local Skill lookup plus MongoDB Atlas semantic vector search |
| **Browser track (original concept)** | | |
| `app/cu_runner.py` | ✅ | Gemini CU loop on a Playwright browser → `Trajectory` |
| `app/executor.py` | ✅ | Playwright action executor (full 3.5 browser action space) |
| `app/runner.py` | ✅ | browser entry point / smoke test |
| `app/trace.py` | ✅ | trajectory recorder |
| **Not built yet** | | |
| Atlas registry sync, desktop eval fleet, `mcp_server` | ⛔ todo | local versioned registry and repair are implemented; remote sharing remains |

---

## Quickstart (for new teammates)
> macOS only for the desktop track. Use **`python3`** to create the venv; after `activate`, plain `python` works.
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # only needed for the browser track

cp .env.example .env                 # then add GEMINI_API_KEY=...   (.env is gitignored)
```

**macOS permissions (one-time, required for the desktop track).** The app running the process (Terminal / iTerm / VS Code) needs both, or screenshots come back blank and clicks do nothing:
- System Settings → Privacy & Security → **Screen Recording**
- System Settings → Privacy & Security → **Accessibility**

Verify with:
```bash
python3 -m app.desktop_cu --probe
```

**Browser smoke test (no controlled app needed):**
```bash
python -m app.runner --url https://www.google.com --intent "Search for 'Gemini API'."
```

**DigitalOcean dry run / live partner demo:**
```bash
python -m app.digitalocean_demo --action restart          # dry-run, no cloud mutation

# live mode: requires doctl auth plus DO_APP_ID in .env
python -m app.digitalocean_demo --action restart --live
```

⚠️ The desktop track moves your real mouse and keyboard. Keep hands off while it runs; slam the mouse into a screen corner to abort (pyautogui failsafe).

## Next up
- Wire the real `learn-cu` orchestrator to emit the hybrid artifact contract in `docs/LEARN_CU_ARTIFACT_CONTRACT.md`.
- Run and record the stale Word fixture on the stage machine with its exact Word version.
- Sync the local versioned registry to MongoDB Atlas so promoted skills can be shared across agents.
- Generalize beyond Word/Calculator (Excel, browser, Mail) to prove the loop holds across surfaces.

See `docs/PLAN.md` for the full plan, contracts, and risk register.

## Sponsors
Gemini 3.5 Computer Use (core) · MongoDB Atlas (skill registry) · DigitalOcean (eval fleet + host) · MiniMax (second agent over MCP).

## License
MIT
