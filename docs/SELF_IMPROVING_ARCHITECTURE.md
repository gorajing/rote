# Rote Self-Improving Architecture

> **Audience:** AI agents and developers joining the `feat/self-improving` branch.  
> **Goal:** Explain how Rote turns a successful Gemini Computer Use run into a reusable, model-free skill — and how it repairs that skill when the UI drifts.

---

## 1. One-sentence summary

Rote watches Gemini 3.5 Flash operate a real computer, **compiles the successful trajectory into a keyboard-first macro**, **replays it deterministically with zero model calls**, and when a step fails verification, **asks Gemini to patch only that step**, validates the patch end-to-end, and **promotes it into a versioned local registry**.

---

## 2. The core loop

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Stage 1: Do    │     │ Stage 2: Compile │     │  Stage 3: Replay    │
│  Gemini CU run  │ ──► │ Gemini reads     │ ──► │ Deterministic exec  │
│  (slow, tokens) │     │ intent log →     │     │ (fast, 0 tokens)    │
│                 │     │ macro JSON       │     │                     │
└─────────────────┘     └──────────────────┘     └──────────┬──────────┘
                                                              │
                                         step/postcondition   │ failure
                                         or final checker     ▼
                                                    ┌─────────────────────┐
                                                    │ Stage 4: Repair     │
                                                    │ Gemini patches ONE  │
                                                    │ failed step only    │
                                                    │ → validate → promote│
                                                    └─────────────────────┘
```

**Two Gemini roles:**

| Role | When it runs | What it produces |
|------|--------------|------------------|
| **Doer** | Stage 1 (record) | Annotated intent log: per-step reasoning, actions, coordinates |
| **Compiler** | Stage 2 (compile) | A v2 macro JSON with keyboard-first ops and verification conditions |
| **Repairer** | Stage 4 (repair) | 1–6 replacement steps for the single failed transition |

Replay and validation never call a model. Repair is the only model touchpoint after compilation.

---

## 3. Surfaces

Rote supports two execution surfaces through a **shared replay/repair engine**:

| Surface | Backend | Input targeting | Example skills |
|---------|---------|-----------------|----------------|
| **desktop** | `MacOSDesktopBackend` in `app/verified_replay.py` | Keyboard shortcuts, app names (`pyautogui` + AppleScript) | `create_word_file`, `meeting_notes`, `calc_to_word` |
| **browser** | `PlaywrightBrowserBackend` in `app/browser_backend.py` | Semantic Playwright locators (`role`, `label`, `text`, `testid`, `css`) | `acme_settings_email`, `youtube_hackathon_top_video` |

Both surfaces use the same:
- Macro schema v2 (`app/macro_skill.py`)
- Condition DSL (`app/verification.py`)
- Verified replay loop (`app/verified_replay.py`)
- Repair service (`app/skill_repair.py`)
- Local registry (`app/local_skill_registry.py`)

---

## 4. Module map

| Module | Responsibility |
|--------|----------------|
| `app/desktop_cu.py` | Desktop **doer**: Gemini CU + `pyautogui`. Also supports legacy `--replay` of v1 macros and dynamic waits (`ensure_app`, `settle`). |
| `app/desktop_skill_compiler.py` | Desktop-specific compiler (trace → macro). |
| `app/universal_skill_compiler.py` | Unified compiler entry point for `desktop` or `browser` surfaces. |
| `app/macro_skill.py` | Schema v2 contracts, legacy v1 → v2 migration, parameter binding (`{{param}}`), validation. |
| `app/verified_replay.py` | **Shared replay engine**: expand subskills, check pre/postconditions per step, run final checker, optionally delegate to repair. |
| `app/verification.py` | Surface-neutral condition DSL and final checkers (files, DOCX, HTTP JSON, composed conditions). |
| `app/skill_repair.py` | Localized Gemini repair, candidate creation, clean-state end-to-end validation, promotion/rejection. |
| `app/local_skill_registry.py` | Atomic JSON version store under `database/skills/registry/` (gitignored). |
| `app/self_improve.py` | CLI for desktop replay / repair / demo / history. |
| `app/browser_backend.py` | Playwright execution + page state inspection. |
| `app/browser_self_improve.py` | CLI for browser replay / repair. |
| `app/notch.py` + `app/desktop_hud.py` | macOS Dynamic Island-style HUD for live replay narration. |
| `database/skills/*.macro.json` | Source-of-truth skill definitions (v1 or v2). Runtime versions live in `registry/`. |

---

## 5. Macro schema v2

Every skill is a JSON file (`*.macro.json`) with `schema_version: 2`.

### Top-level fields

```json
{
  "schema_version": 2,
  "surface": "desktop",
  "name": "create_word_file",
  "app": "Microsoft Word",
  "os": "macos",
  "version": 1,
  "parent_version": null,
  "status": "active",
  "params": { "text": "Hello", "filename": "demo", "location": "Desktop" },
  "checker": { "type": "word_docx", "location": "{{location}}", "filename": "{{filename}}.docx", "contains": "{{text}}" },
  "stats": { "uses": 0, "successes": 0, "failures": 0, "success_rate": 0.0, "avg_duration": 0.0, "model_calls": 0 },
  "steps": [ /* see below */ ]
}
```

Browser skills additionally use `start_url`, `reset` (HTTP reset endpoint), and `"surface": "browser"`.

### Step fields

Each step is a state transition with explicit verification gates:

```json
{
  "id": "create_blank_document",
  "op": "hotkey",
  "keys": ["command", "n"],
  "precondition": { "foreground_app": "Microsoft Word" },
  "postcondition": { "word_document": true },
  "timeout": 5,
  "retry_limit": 1,
  "fallback": [{ "op": "key", "key": "return" }],
  "why": "Create a blank document and handle the template chooser"
}
```

### Allowed operations

**Desktop:** `open_app`, `quit_app`, `wait`, `hotkey`, `key`, `type`, `call`  
**Browser:** `navigate`, `click`, `fill`, `press`, `select`, `check`, `uncheck`, `scroll`, `wait`, `call`

The `call` op composes subskills:

```json
{
  "id": "prepare_document",
  "op": "call",
  "skill": "ensure_blank_document",
  "params": {}
}
```

At replay time, `call` steps are **expanded inline** by `_expand_steps()` in `verified_replay.py`. Each expanded step carries `_source_skill` metadata so repair knows which skill file owns the failed step.

### Parameter binding

User-specific literals are extracted into `params` and referenced as `{{param}}` in steps and checkers. `resolve_params()` substitutes them at runtime. Hardcoded literals in repair patches are rejected.

### Legacy v1 migration

v1 macros (flat step lists without conditions) are migrated in-memory by `migrate_macro()`. The source file on disk is never modified. Migration adds default `id`, `precondition`, `postcondition`, `timeout`, `retry_limit`, and `fallback` to each step.

---

## 6. Verified replay (model-free execution)

Entry point: `replay_verified()` in `app/verified_replay.py`.

### Algorithm

```
1. migrate_macro(skill)
2. merge runtime params
3. pick backend (MacOSDesktopBackend or injected PlaywrightBrowserBackend)
4. expand all `call` subskills into a flat step list
5. FOR each step:
     a. inspect() → current UI state
     b. evaluate precondition  → FAIL → stop (circuit breaker)
     c. execute step (retry up to retry_limit)
     d. inspect() → new state
     e. evaluate postcondition → FAIL → try fallbacks, then stop
6. IF all steps passed:
     run final checker (e.g. verify DOCX on disk, HTTP /state endpoint)
7. IF allow_repair and repair_service provided and result.success == False:
     delegate to RepairService.repair_and_validate()
8. return result dict
```

### Backend `inspect()` state

**Desktop** (`MacOSDesktopBackend.inspect()`):
- `foreground_app`, `windows`, `ui_text`
- `word_document_count`, `running_apps`, `clipboard`

**Browser** (`PlaywrightBrowserBackend.inspect()`):
- `url`, `title`, `visible_text`

### Result shape

```python
{
  "success": bool,           # all steps + final checker passed
  "checker_passed": bool,
  "checker_failures": [...],
  "failed_step_id": str | None,
  "failure": {               # present when a step or checker fails
    "step_id", "step", "state", "reason"  # "precondition" | "postcondition"
  },
  "records": [...],          # per-step audit trail
  "elapsed_s": float,
  "model_calls": 0,          # always 0 for pure replay
  "repair_calls": 0,
  "mode": "verified_replay"
}
```

---

## 7. Condition DSL

Defined in `app/verification.py`. Used for step pre/postconditions and final checkers.

### Leaf conditions

| Key | Meaning |
|-----|---------|
| `foreground_app` | Exact foreground app name (desktop) |
| `app_running` | App appears in running process list |
| `app_window` / `ui_text` / `dialog` | Substring match in window or UI text |
| `word_document` | `true` → Word has ≥1 open document |
| `clipboard_contains` | Substring in clipboard |
| `url_contains` / `title_contains` / `text_contains` | Substring match (browser) |
| `element_visible` | Text appears in page visible text |
| `file_exists` | File exists at location + filename |
| `state_equals` | Dot-path lookup into inspect state |

### Composition

```json
{ "all": [ { "foreground_app": "Microsoft Word" }, { "word_document": true } ] }
{ "any": [ { "text_contains": "Saved" }, { "text_contains": "Updated" } ] }
{ "not": { "title": "Error" } }
```

### Final checkers (`check_final`)

| Type | Validates |
|------|-----------|
| `condition` | Any condition DSL expression against final state |
| `word_docx` | DOCX file exists and contains expected text (unzips `word/document.xml`) |
| `file` / `text_file` | Plain file existence + text content |
| `http_json` | HTTP GET/POST, then dot-path equality on JSON response |
| `all` / `any` | Compose multiple checkers |

Checkers are **never self-reported by the model**. They read filesystem, HTTP endpoints, or OS state. This is the integrity spine: memory is success-gated.

---

## 8. Localized repair and promotion

Entry point: `RepairService.repair_and_validate()` in `app/skill_repair.py`.

### When repair triggers

Repair runs only when:
1. `replay_verified(..., allow_repair=True, repair_service=...)` is called, AND
2. A step precondition/postcondition or the final checker fails.

### Repair flow

```
1. Identify the owning skill of the failed step
   (via _source_skill metadata from subskill expansion)

2. Call Gemini (repair_failed_step):
   - Input: failed step, failure reason, condition failures,
            current inspect state, successful prior steps, optional screenshot
   - Output: JSON { "replacement_steps": [ 1..6 steps ] }
   - Constraints: no coordinates; keyboard-first (desktop) or semantic targets (browser);
                  preserve {{param}} references

3. Replace the failed step in the owner skill with replacement_steps

4. Create a candidate version in the registry
   (version = max(existing) + 1, status = "candidate")

5. Reset environment to a clean starting state
   (e.g. reset_stale_word() for the Word demo)

6. Replay the FULL workflow from scratch using the candidate (via overlay registry)
   - allow_repair=False (no recursive repair during validation)

7. IF validation.success AND validation.checker_passed:
     promote candidate → active version
   ELSE:
     reject candidate with failure reason
```

### Subskill repair propagation

If `ensure_blank_document` is repaired and promoted, any workflow that `call`s it — e.g. `create_word_file`, `meeting_notes` — automatically picks up the new version on the next `registry.load_skill("ensure_blank_document")`. This is how localized repair transfers across shared workflows without recompiling the root skill.

### Registry layout

```
database/skills/
  create_word_file.macro.json          ← source definition (committed)
  ensure_blank_document.macro.json
  registry/                            ← runtime versions (gitignored)
    index.json                         ← { skills: { name: { active_version, history } } }
    ensure_blank_document/
      v1.json                          ← original
      v2.json                          ← promoted repair candidate
```

Operations: `load_skill`, `create_candidate`, `promote`, `reject`, `record_run`, `get_history`.

---

## 9. Self-improvement demo (stale skill fixture)

The branch includes **deterministic stale fixtures** for live demos:

| Skill | Purpose |
|-------|---------|
| `stale_ensure_blank_document` | Subskill that omits `Cmd+N` — Word opens but no document is created |
| `stale_create_word_file` | Root workflow that calls the stale subskill |
| `stale_youtube_hackathon_video` | Browser-side stale fixture |

`reset_stale_word()` creates the drift state: Word is frontmost with **zero open documents**. Replay fails at the subskill's postcondition (`word_document: true`). Repair generates a replacement step (typically `Cmd+N`), validates end-to-end, and promotes.

```bash
python -m app.self_improve demo stale_create_word_file --metrics traces/self_improvement.json
```

This runs: reset → replay (expect fail) → repair → reset → replay (expect pass) → JSON report.

---

## 10. CLI reference

### Desktop

```bash
# Verified model-free replay
python -m app.self_improve replay create_word_file

# Replay with optional localized repair on failure
python -m app.self_improve repair create_word_file

# Full self-improvement demo (stale → repair → promote → verify)
python -m app.self_improve demo stale_create_word_file

# Inspect version history
python -m app.self_improve history ensure_blank_document

# Replay with notch HUD
python -m app.desktop_hud --skill create_word_file
python -m app.desktop_hud --skill stale_create_word_file --repair
```

### Browser

```bash
python -m app.browser_self_improve replay acme_settings_email --headless
python -m app.browser_self_improve repair acme_settings_email --headless
```

### Compile a new skill from a trace

```bash
# Desktop
python -m app.desktop_skill_compiler --trace traces/run.json --out database/skills/demo.macro.json

# Either surface
python -m app.universal_skill_compiler --surface browser --trace traces/run.json --out database/skills/new.macro.json
```

### Record a new trace (Stage 1)

```bash
python -m app.desktop_cu \
  --max-turns 26 \
  --trace traces/run.json \
  --intent "Create a new Microsoft Word document, type 'Hello', and save it to the Desktop as 'demo'."
```

---

## 11. Example skill graph (Word workflows)

```
create_word_file
├── call → ensure_blank_document
│           ├── open_app (Microsoft Word)
│           └── hotkey Cmd+N  → postcondition: word_document=true
├── type {{text}}
└── call → save_word_document
            ├── hotkey Cmd+S
            ├── type {{filename}}
            └── key return

meeting_notes
├── call → ensure_blank_document   ← shared subskill
├── ... formatting steps ...
└── call → save_word_document      ← shared subskill
```

Repairing `ensure_blank_document` v1 → v2 benefits both `create_word_file` and `meeting_notes` without touching their root macros.

---

## 12. Tests

| File | What it covers |
|------|----------------|
| `tests/test_self_improving.py` | v1 migration, parameter resolution, verified replay with fake backend, repair patch cleaning, candidate promotion/rejection, subskill propagation |
| `tests/test_cross_surface.py` | Browser semantic target validation, shared replay engine on browser skills, condition composition, HTTP/file checkers |

Run:

```bash
python -m unittest tests.test_self_improving tests.test_cross_surface
```

Tests use `FakeBackend` / `BrowserStateBackend` — no real OS or browser required.

---

## 13. Key invariants (do not break)

1. **Replay is model-free.** `replay_verified()` must not call Gemini or capture screenshots unless explicitly routed through repair.
2. **Checkers are external.** Success is determined by filesystem, HTTP, or OS state — never by model self-report.
3. **Repair is localized.** At most one failed transition is patched per repair call; patches are 1–6 steps, no coordinates.
4. **Promotion is success-gated.** A candidate is promoted only after a full clean-state end-to-end replay passes the deterministic checker.
5. **Subskill ownership matters.** Repair patches the skill that owns the failed step (`_source_skill`), not always the root workflow.
6. **Registry writes are atomic.** Use `_atomic_json()` (write temp → `os.replace`) to avoid partial writes.

---

## 14. Measured performance (desktop, this branch)

| Task | Doer (Gemini CU) | Verified replay | Speedup |
|------|------------------|-----------------|---------|
| Create Word file | 130s · 124,859 tok | 16s · **0 tok** | ~8× |
| Formatted meeting notes | 175s · 173,351 tok | 23s · **0 tok** | ~7.6× |
| Calculator → Word | 132s · 256,063 tok | 17–35s · **0 tok** | ~4–8× |

The headline demo metric: **CU model calls: N → 0** on verified replay.

---

## 15. Not yet implemented

These are planned (see `docs/PLAN.md`) but **not** part of the current branch:

- MongoDB Atlas remote registry sync
- Desktop eval fleet
- MCP server for cross-agent skill sharing
- Hard arena (AcmeBilling structural mutation with `/reset?mutation=`)

The local versioned registry and localized repair lifecycle are fully implemented locally.

---

## 16. Quick mental model for agents

When asked to **add a new skill:**
1. Record a successful CU trace (Stage 1).
2. Compile it with `universal_skill_compiler` (Stage 2).
3. Add a deterministic `checker` that reads external state.
4. Add step pre/postconditions so replay fails fast on drift.
5. Test with `self_improve replay <name>`.

When asked to **debug a failing replay:**
1. Read `failed_step_id` and `failure.reason` in the result JSON.
2. Inspect `failure.state` — what did the backend see?
3. Check whether pre/postconditions match the actual UI state.
4. If the skill is intentionally stale, run `self_improve demo <name>` to exercise repair.

When asked to **extend to a new surface:**
1. Implement a backend with `execute(step)` and `inspect() -> dict`.
2. Add allowed ops to `BROWSER_OPS` or a new op set in `macro_skill.py`.
3. Extend `evaluate_condition()` if new state keys are needed.
4. Pass the backend to `replay_verified()`. Repair and registry work unchanged.

---

## 17. File index (skills shipped on this branch)

| File | Surface | Description |
|------|---------|-------------|
| `ensure_blank_document.macro.json` | desktop | Shared subskill: open Word + Cmd+N |
| `save_word_document.macro.json` | desktop | Shared subskill: Cmd+S save flow |
| `create_word_file.macro.json` | desktop | Compose ensure + type + save |
| `meeting_notes.macro.json` | desktop | Formatted document workflow |
| `calc_to_word.macro.json` | desktop | Calculator → clipboard → Word |
| `stale_ensure_blank_document.macro.json` | desktop | Stale subskill (no Cmd+N) for demo |
| `stale_create_word_file.macro.json` | desktop | Root stale demo workflow |
| `acme_settings_email.macro.json` | browser | Settings form + HTTP checker |
| `youtube_hackathon_top_video.macro.json` | browser | YouTube search workflow |
| `stale_youtube_hackathon_video.macro.json` | browser | Stale browser demo fixture |
