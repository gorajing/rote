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

Rote ships two engines that share this philosophy: both replay **model-free on the happy path (0 CU)** and decide success against ground truth — and when a step drifts, both fall back to the model for *exactly that step* (self-heal) rather than failing or faking it.

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

**The headline metric is unfakeable: CU calls N -> 0, verified by ground truth** — a skill that didn't actually achieve its goal is refused, never cached. Generalization is honestly **uneven by family**: `settings_change` replays reliably at **0 CU, verified**; `row_find_act` (refund) generalizes, but crop drift on a held-out row can cost **one self-heal call (CU=1)** before it re-grounds; the modal-heavy `invoice_action` (dispute) workflows are the **hard frontier** — drift escalates and sometimes needs a full recompile. Cold-learning is a live Gemini run, so the exact per-task 0-CU count **varies between runs** (a representative run lands ~3/5 of the sampled tasks at 0 CU verified); the durable claim is not a fixed score but the **mechanism** — drift is paid once, then amortizes to 0, always behind a ground-truth gate. That mechanism is locked down by the hermetic test bank in `tests/` (no key or network). Reproduce the live eval with:

```bash
python -m app.controlled_app.server                 # arena on :8800
python -m app.fusion.test_skills --split all        # cold → compile → replay → verify, per family (live, stochastic)
python -m unittest discover -s tests                # the deterministic mechanism proof (no key needed)
```

**What's proven vs. the frontier.** The demo-safe spine is solid and reproducible: **browser fusion replay at 0 CU**, **settings generalization**, **self-heal that persists** (drift paid once, then free — `validate_persist` drives the live arena to **CU 0 → 1 → 0** across a save/reload-from-disk boundary), **recall** of a warm skill by plain-language intent, and a **non-Acme real-web → TextEdit self-improvement demo** (`stale_web_to_textedit_note`) that fails, repairs, promotes, then replays with 0 model calls. The honest frontier, still model-bound today: arbitrary generalization across *all* 11 tasks (dispute modal drift), the fully learned web→native-app **hybrid** (Gemini action-safety blocks the cross-context paste — see below), and **live cold-launch of heavy desktop apps** like Word (UI-readiness timing). We ship those as architecture and say plainly where the model stops us.

---

## Recall — say it, and it remembers

Learning is worthless if a skill can't be found again. `app/fusion/recall.py` embeds each promoted skill's intent into a **local-first** index and matches a plain-language goal to the right learned skill — closing the continual-learning loop:

```
intent  →  recall(intent)  →  load the learned skill  →  fusion replay at 0 CU, verified
```

```bash
python -m app.fusion.recall --backfill
python -m app.fusion.recall "refund the paid Globex invoice"   # → fused_train-refund-globex (0.82)
```

Cosine match runs in-process (no network at recall time beyond embedding the query — a cheap *text* call, not a computer-use call, so replay stays 0 CU). MongoDB Atlas (`database/api.py`) is optional for this fusion recall path; the voice/chat surfaces below can also use Atlas as a shared replayable skill catalog.

---

## Learned cross-surface hybrid skills (real web → your Mac apps)

The fusion engine is surface-agnostic, so a skill can span **a real website and a native Mac app**. `app/fusion/hybrid.py` runs Gemini as the doer on *each* segment (`cu_runner` for the browser, `desktop_cu` for the desktop), compiles each real trace to a 0-CU `FusedSkill`, and chains them with a **typed payload** the orchestrator captures and bridges across surfaces — e.g. *the title Gemini selects on a real page is bridged to the pasteboard and pasted into a TextEdit note* (Playwright's browser clipboard is sandboxed from the OS, so the payload is captured explicitly, not assumed). Every seam is gated on ground truth (`world_verifiers.py`: clipboard / TextEdit), at learn **and** replay — a doer run that didn't actually achieve its segment is refused, never compiled. This is the genuinely-*learned* approach — the doer actually performs each segment, not a hand-built template.

**Honest status (current).** The **browser segment works and is ground-truth-verified** — Gemini really selects the target text and the captured payload is confirmed on the OS pasteboard. The **desktop paste seam is currently blocked by Gemini's built-in action-safety**, which refuses the cross-context (web→desktop) paste (`BadRequestError: Input blocked`) — observed even on `example.com`. That is a model-side limitation; we do **not** disable safety to force it. The typed-payload architecture and the browser half are proven; the open path is to complete the desktop seam *deterministically* (paste/save as known keystrokes, bypassing the model for that one fixed step) rather than asking the model to perform an action it refuses.

```bash
python -m app.fusion.hybrid learn --url https://example.com --replay   # browser segment verified; desktop paste seam blocked by model safety
```

---

## The macOS desktop pipeline (record → compile → replay)

This drives the **real macOS desktop** via `pyautogui` and has produced real `.docx` files and a multi-app Calculator → clipboard → Word flow. **Honest status:** desktop replay is real but **launch-timing-sensitive** — a heavy app like Word can report a window before its UI is actually interactive, so a *cold-launch* replay can stall at the launch step (`_app_ready` checks window-count, not interactivity). The reliably-demonstrable self-improvement path for a live demo is the browser/fusion engine (`validate_selfheal`) plus recall; the desktop track is best shown against an already-warm app.

```bash
# 1) record (the doer): Gemini drives the desktop, writes the intent log
python3 -m app.desktop_cu --max-turns 26 --trace traces/run.json \
  --intent "Create a Word document, type 'Hello', and save it to the Desktop as 'demo'."

# 2) compile (the compiler): a Gemini pass -> a keyboard-first macro
python3 -m app.desktop_skill_compiler --trace traces/run.json --out traces/demo.macro.json

# 3) replay (fast, 0 model calls), optionally with the notch HUD
python3 -m app.desktop_cu --replay traces/demo.macro.json
python3 -m app.desktop_hud --replay traces/demo.macro.json
```

### Step-level self-improvement
Versioned **macro v2** skills verify every state transition against macOS UI state and final file checks. A failed transition stops the replay; `--repair` asks Gemini for a bounded patch to *that step only*, replays the full candidate from a clean state, and promotes it only after the deterministic checker passes.

```bash
python -m app.self_improve demo stale_web_to_textedit_note --metrics traces/web_textedit_self_improvement.json
```

The old tracked `database/skills/*.macro.json` seed catalog was intentionally removed after the DB pivot because those examples were stale. Runtime repair versions still live under `database/skills/registry/` (gitignored), while repeatable demo fixtures live under `examples/demo_skills/`. Voice/chat use MongoDB replayable skill documents instead of the old local seed catalog.

### Voice and chat surfaces
A LiveKit voice agent runs learned desktop skills by voice and narrates live (Deepgram STT → Gemini 3.5 Flash → Cartesia TTS, via LiveKit Inference — no separate Google key on the replay path):

```bash
pip install -r requirements-voice.txt
python3 -m app.voice_agent console     # then say: "calculate 52 times 68 and save it in Word."
python -m chat                         # Gemini function-calling TUI over the same replayable DB store
```

There are two Atlas modes today:
- **MCP descriptor index**: `app.skill_search_index` writes small `doc_type=executable_skill` descriptors, then `app.mcp_service` resolves the executable macro from the local versioned registry before replay. After the DB pivot there is no tracked local seed catalog, so this lane is empty until runtime/demo skills are promoted into the local registry.
- **Voice/chat skill store**: `app.skill_store` writes flattened `doc_type=skill` macro documents so the voice agent and chat TUI can replay without local subskill lookups. Search filters out stale or unchecker-backed documents before execution.

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
| `app/fusion/validate_selfheal.py` · `validate_persist.py` | live proofs: drift → self-heal, and the persistence round-trip (CU 0→1→0) |
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
| `database/api.py` | Atlas vector/search gateway shared by descriptor indexing and replayable DB skills |
| `app/skill_store.py` · `chat/` | voice/chat replayable Mongo skill store and Gemini function-calling TUI |

## Implementation status

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
| `examples/demo_skills/*.macro.json` | ✅ | Explicit demo fixtures for self-improvement drills |
| `database/api.py` | ✅ | MongoDB Atlas semantic vector search plus deterministic upsert/list helpers |
| `app/skill_store.py` · `chat/` | ✅ | flattened replayable DB skills for the voice agent and chat TUI |
| **Browser track (original concept)** | | |
| `app/cu_runner.py` | ✅ | Gemini CU loop on a Playwright browser → `Trajectory` |
| `app/executor.py` | ✅ | Playwright action executor (full 3.5 browser action space) |
| `app/runner.py` | ✅ | browser entry point / smoke test |
| `app/trace.py` | ✅ | trajectory recorder |
| **Not built yet** | | |
| Automatic learned-skill Atlas seeding, desktop eval fleet | ✅ |
| `app/mcp_server.py` | ✅ |

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

The MCP server uses MongoDB Atlas for semantic discovery and the local versioned registry as the
source of executable macros. This is intentionally separate from the voice/chat replayable DB skill
store. After the DB pivot there is no tracked local seed catalog, so the dry-run may return `[]`
until a runtime/demo skill has been promoted locally:

```bash
python -m app.skill_search_index --dry-run   # inspect descriptors without writing Atlas
python -m app.skill_search_index             # embed and upsert descriptors in Atlas
python -m app.mcp_server                     # stdio server (normally launched by the MCP client)
```

For MCP, Atlas is a search index, not the executable source of truth: `app.skill_search_index`
publishes descriptors for active local desktop macros, and MCP resolves the exact executable version
from the local registry before replay. Voice/chat use a separate `doc_type=skill` lane that stores
flattened, checker-backed macro documents for DB-only replay. Newly learned fresh/hybrid artifacts
are saved locally today; automatic Atlas seeding for those artifacts is still a TODO.

Example client configuration (use absolute paths on your machine):

```json
{
  "mcpServers": {
    "rote": {
      "command": "/absolute/path/to/rote/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/absolute/path/to/rote"
    }
  }
}
```

The Atlas Vector Search index `description` must index `embedding` as the vector field and
`doc_type`, `surface`, `status`, `checker_verified`, and `app` as filter fields. Desktop replay
controls the real keyboard and mouse and therefore requires `confirm_execution=true` on every call.

⚠️ The desktop track moves your real mouse and keyboard. Keep hands off while it runs; slam the mouse into a screen corner to abort (pyautogui failsafe).

## License
MIT
