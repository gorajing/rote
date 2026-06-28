# Rote

**A Gemini 3.5 Computer Use agent that learns reliable computer skills by doing — then replays them with no model in the loop, recalls them by plain-language intent, and repairs them when the UI changes.**

Computer-use agents are powerful but amnesiac and slow: every run is disposable, and every step is a multi-second round-trip to a vision model. Rote watches **Gemini 3.5 Flash** operate a real computer, **compiles the successful run into a reusable, keyboard-first / crop-grounded skill** (authored by a second Gemini pass — not by hand), then **replays that skill deterministically with zero model calls**. When a step drifts, it falls back to the model to self-heal one step; when you ask for something in plain words, it **recalls** the skill it already learned.

> AI Engineer World's Fair Hackathon 2026 · Theme: **Continual Learning** · Target: **Best Use of Gemini Computer Use**

## The loop

```
do it once (Gemini CU)  →  compile (2nd Gemini pass)  →  replay 0-CU, verified  →  drift? self-heal 1 step
   slow, model              the trajectory → a skill       fast, NO model calls       recall by intent next time
```

Two models, two roles:
- **The doer** — Gemini 3.5 Flash drives the computer from screenshots and leaves an annotated **intent log** (`intent → action → coords`, each `intent` is the model's own per-step reasoning).
- **The compiler** — a second Gemini 3.5 pass reads that log and writes a replayable skill: keyboard shortcuts where it can, a small **target-crop** per spatial step so replay re-localizes the pixel model-free. Replaying needs **no screenshots and no model calls** — and success is decided by a deterministic **checker reading ground truth**, never the model's self-report.

---

## Two replay engines

Rote ships two engines that share this philosophy; both replay at 0 CU and verify against ground truth.

1. **Verified-macro engine** (`app/verified_replay.py`) — desktop macOS macros with per-step pre/postconditions, retries, fallbacks, and localized repair. Drives the **real desktop** via `pyautogui`. Replay defaults to the verified per-step contract; `optimistic` blind-fast replay is **opt-in** (voice HUD / plain `--replay`).
2. **Fusion engine** (`app/fusion/`) — a **surface-agnostic** tiered dispatcher that replays a compiled `FusedSkill` by routing each step to the cheapest tier that reproduces it:
   - **keyboard** → fire the shortcut blind (0 perception),
   - **crop** → re-localize the step's visual precondition with a template match (0 model),
   - **model** → escalate exactly one step to Gemini CU on drift (the self-heal).
   The same dispatcher serves **browser and desktop**, gated by a ground-truth `Verifier`.

---

## The browser arena + evaluation

A hard, deterministic **controlled web app** to measure the engine honestly.

- **AcmeBilling arena** (`app/controlled_app/`) — a Flask app at `http://localhost:8800` with a `/state` snapshot endpoint and `/reset?variant=…` for structural UI mutations (e.g. `move_dispute_to_cases` relocates an action behind a Cases page — the self-heal stress test).
- **11-task bank** (`app/tasks.py`) — 6 train + 5 held-out across **three families**: `invoice_action` (a multi-step dispute workflow), `row_find_act` (filter → find → refund), `settings_change`.
- **Deterministic checkers** (`app/checker.py`) read `/state` — `dispute_workflow`, `row_find_act`, `settings_change` — so "success" is never self-reported.
- **Eval harness** (`app/eval_harness.py`) — skills-off ablation, UI-mutation variants, repair-eval.

**The headline metric is unfakeable: CU calls N → 0, verified by ground truth.** On the fusion engine across the bank, the **refund** and **settings** families generalize cleanly (train → held-out, **0 CU, verified**); the long modal-heavy **dispute** workflows are the honest hard frontier (crop drift → self-heal escalation). Reproduce with:

```bash
python -m app.controlled_app.server                 # arena on :8800
python -m app.fusion.test_skills --split all        # cold → compile → replay → verify, per family
```

---

## Recall — say it, and it remembers

Learning is worthless if a skill can't be found again. Macro, fusion, voice, and MCP entry points
now share an **Atlas-first** semantic recall service. A synchronized local embedding cache is used
when Atlas is unavailable; the legacy fusion recall index remains a migration fallback.

```
intent  →  recall(intent)  →  load the learned skill  →  fusion replay at 0 CU, verified
```

```bash
python -m app.fusion.recall --backfill
python -m app.fusion.recall "refund the paid Globex invoice"   # → fused_train-refund-globex (0.82)
```

Recall is still a cheap text-embedding operation, not a Computer Use call. Atlas stores common
descriptors for verified macro and fusion skills; local registries remain the executable source of truth.

---

## Learned cross-surface hybrid skills (real web → your Mac apps)

The fusion engine is surface-agnostic, so a skill can span **a real website and a native Mac app**. `app/fusion/hybrid.py` runs Gemini as the doer on *each* segment (`cu_runner` for the browser, `desktop_cu` for the desktop), compiles each real trace to a 0-CU `FusedSkill`, and chains them with a **typed payload** the orchestrator captures and bridges across surfaces — e.g. *the title Gemini selects on a real page is bridged to the pasteboard and pasted into a TextEdit note* (Playwright's browser clipboard is sandboxed from the OS, so the payload is captured explicitly, not assumed). Every seam is gated on ground truth (`world_verifiers.py`: clipboard / TextEdit), at learn **and** replay — a doer run that didn't actually achieve its segment is refused, never compiled. This is the genuinely-*learned* approach — the doer actually performs each segment, not a hand-built template.

```bash
python -m app.fusion.hybrid learn --url https://example.com --replay   # learn (real CU) then replay (0 CU)
```

---

## The macOS desktop pipeline (record → compile → replay)

This drives the **real macOS desktop** via `pyautogui` and has produced real `.docx` files and a multi-app Calculator → clipboard → Word flow.

```bash
# 1) record (the doer): Gemini drives the desktop, writes the intent log
python3 -m app.desktop_cu --max-turns 26 --trace traces/run.json \
  --intent "Create a Word document, type 'Hello', and save it to the Desktop as 'demo'."

# 2) compile (the compiler): a Gemini pass → a keyboard-first macro
python3 -m app.desktop_skill_compiler --trace traces/run.json --out database/skills/demo.macro.json

# 3) replay (fast, 0 model calls), optionally with the notch HUD
python3 -m app.desktop_cu --replay database/skills/demo.macro.json
python3 -m app.desktop_hud --skill create_word_file
```

### Step-level self-improvement
Versioned **macro v2** skills verify every state transition against macOS UI state and final file checks. A failed transition stops the replay; `--repair` asks Gemini for a bounded patch to *that step only*, replays the full candidate from a clean state, and promotes it only after the deterministic checker passes.

```bash
python -m app.self_improve replay create_word_file                         # verified, model-free replay
python -m app.self_improve demo stale_create_word_file --metrics traces/self_improvement.json
python -m app.desktop_hud --skill stale_create_word_file --repair          # localize → repair → validate → promote
python -m app.browser_self_improve replay acme_settings_email --headless   # the same engine, browser surface
```

Runtime skill versions live under `database/skills/registry/` (gitignored — these are runtime artifacts; the canonical seed skills `database/skills/*.macro.json` are tracked). `create_word_file` and `meeting_notes` share `ensure_blank_document` and `save_word_document`, so a promoted subskill repair transfers to both workflows.

### 🎙️ Talk to it
A LiveKit voice agent runs learned desktop skills by voice and narrates live (Deepgram STT → Gemini 3.5 Flash → Cartesia TTS, via LiveKit Inference — no separate Google key on the replay path):

```bash
pip install -r requirements-voice.txt
python3 -m app.voice_agent console     # then say: "calculate 52 times 68 and save it in Word."
```

### Reliability features
- **Dynamic waits** (`ensure_app` / `settle`) — polls macOS for app-readiness and watches the screen until it settles, instead of a fixed `sleep` (an already-open app continues in ~0.3s).
- **Notch HUD** (`app/notch.py`) — a Dynamic-Island-style status pill at the MacBook notch (AppKit / PyObjC), narrating each step on all Spaces without stealing focus.

---

## Module map

| File | Responsibility |
|---|---|
| `app/schemas.py` · `app/config.py` | contracts (`Task`/`Step`/`Trajectory`/`Skill`); models, viewport, flags, `.env` |
| **Fusion engine (surface-agnostic)** | |
| `app/fusion/contract.py` | `FusedSkill` + the `Executor`/`Verifier` protocols |
| `app/fusion/dispatch.py` | tiered replay (keyboard → crop → model), CU-call accounting |
| `app/fusion/compiler.py` | lower a verified Trajectory → `FusedSkill` (crop-gated) |
| `app/fusion/{browser,desktop}_executor.py` | Playwright / pyautogui executors |
| `app/fusion/verifier.py` · `world_verifiers.py` | ground-truth checks (arena `/state`, docx, clipboard, TextEdit) |
| `app/fusion/skill_store.py` | versioned, success-gated fused-skill store |
| `app/fusion/recall.py` | embed-on-promote + recall a skill by intent |
| `app/fusion/hybrid.py` | learned cross-surface (web → Mac app) skills |
| `app/fusion/test_skills.py` | multi-skill generalization harness (rebuilds the library) |
| **Browser arena + eval** | |
| `app/controlled_app/` | AcmeBilling Flask arena (`:8800`, `/state`, `/reset?variant=`) |
| `app/tasks.py` · `app/checker.py` | 11-task train/held-out bank · deterministic checkers |
| `app/eval_harness.py` | ablation / mutation / repair eval |
| `app/cu_runner.py` · `app/executor.py` · `app/runner.py` · `app/trace.py` | browser CU loop → Trajectory |
| **Desktop / self-improvement** | |
| `app/desktop_cu.py` · `app/desktop_skill_compiler.py` · `app/universal_skill_compiler.py` | desktop doer + compilers |
| `app/verified_replay.py` · `app/verification.py` | condition-checked macro replay + DSL |
| `app/local_skill_registry.py` · `app/skill_repair.py` | versioned promotion + localized repair |
| `app/notch.py` · `app/desktop_hud.py` · `app/voice_agent.py` | notch HUD · HUD runner · voice agent |
| `services/database_gateway.py` + `mcp/rote_mcp/recall.py` | HTTP-isolated Atlas recall + local semantic fallback |
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
| `mcp/rote_mcp/` | ✅ | FastMCP server, descriptor projection, recall, promotion, HTTP storage client |
| `services/database_gateway.py` | ✅ | Network boundary over unchanged `database.api.push/retrieve` |

---

## Quickstart

> macOS only for the desktop track. Use **`python3`** to create the venv; after `activate`, plain `python` works.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium                       # browser + arena + fusion track

cp .env.example .env                              # add GEMINI_API_KEY=...  (.env is gitignored)
```

**macOS permissions (one-time, desktop track).** The app running the process (Terminal / iTerm / VS Code) needs both, or screenshots come back blank and clicks do nothing:
- System Settings → Privacy & Security → **Screen Recording** and **Accessibility**

```bash
python3 -m app.desktop_cu --probe                 # verify permissions
python -m app.runner --url https://www.google.com --intent "Search for 'Gemini API'."   # browser smoke test
```

### FastMCP server

The MCP server has no direct MongoDB access. It calls a local database gateway, which wraps the
unchanged `database.api.push()` and `retrieve()` functions. Atlas provides semantic discovery and
the local versioned registry remains the
source of executable macro and fusion skills. All verified skill descriptors, including desktop and browser skills,
and CU traces live in `automated_tasks.tasks`. Desktop keyboard shortcuts follow
`database/automation_shortcut.schema.json`. When no
verified skill matches, `execute_new_task` falls back to
Gemini Computer Use and records a trace. A completed trace is automatically compiled into a
candidate. A clean deterministic replay and checker pass produces a deterministic verified skill.
If deterministic compilation or replay fails, the trace is registered as an `adaptive` verified
skill instead. Adaptive skills are searchable and executable, with
`verification_mode=adaptive_cu` and `checker.type=adaptive_cu`; replay uses the recorded semantic
action path as a Computer Use hint and validates the live screen during execution.
Index active
desktop skills explicitly, then configure an MCP client to launch the stdio server:

```bash
pip install -e ./mcp
python -m services.database_gateway       # terminal 1: DB API on 127.0.0.1:8810
python -m rote_mcp.descriptors --dry-run  # inspect verified descriptors
python -m rote_mcp.descriptors            # optional backfill through the gateway
python -m rote_mcp.server                 # terminal 2, normally launched by the MCP client
```

Example client configuration (use absolute paths on your machine):

```json
{
  "mcpServers": {
    "rote": {
      "command": "/absolute/path/to/rote/.venv/bin/python",
      "args": ["-m", "rote_mcp.server"],
      "cwd": "/absolute/path/to/rote",
      "env": {"ROTE_DATABASE_API_URL": "http://127.0.0.1:8810"}
    }
  }
}
```

The gateway must be running before the MCP client if Atlas search, trace persistence, and remote
skill persistence are required. If it is unavailable, verified skills already present in the local
semantic cache remain recallable and failed writes stay in `pending_skill_sync.json`.

The Atlas Vector Search index `description` indexes `embedding` in `tasks`; `doc_type` excludes CU
traces before ranking. Prefer the MCP `execute_task` tool: it recalls, binds variables, and routes
to macro replay, fusion replay, trace-assisted CU, or cold CU. Desktop replay controls the real keyboard and mouse and therefore
requires `confirm_execution=true` on every call.
Unverified Computer Use runs are embedded in `automated_tasks.tasks`; similar future tasks may use
their redacted, coordinate-free action sequence as a non-binding hint, but never as verified replay.
User-provided literals are projected as `{{variable_name}}` placeholders in Atlas documents. MCP
callers provide a `variables` object for new CU tasks and runtime `params` for verified replay;
literal values are used during execution but are not persisted in skill or trace documents.

⚠️ The desktop track moves your real mouse and keyboard. Keep hands off while it runs; slam the mouse into a screen corner to abort (pyautogui failsafe).

## License
MIT
