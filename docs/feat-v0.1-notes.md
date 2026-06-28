# feat/v0.1 — voice layer + engine changes (merge notes)

What this branch adds on top of unified `main`, and where to start. Aimed at teammates reviewing
before merging `feat/v0.1 → main`.

## TL;DR
- **New: a voice agent** — talk to Rote, it runs your learned desktop skills and narrates live.
- **Engine: optimistic fast replay (opt-in for user-facing paths)** — user-facing replay (voice HUD,
  plain replay) can opt in to a blind/fast happy path, with per-step verification kicking in only to
  *diagnose* a failure. The engine default stays verified per-step; optimistic is turned on at the call
  site. Restores the "fastest agent" speed lost to per-step inspection.
- **Engine: macOS-style unique filenames** — never overwrite; saves `name_2.docx`, `name_3.docx`, …
- **Two real bug fixes** in the desktop save path (found via systematic debugging).
- **Dynamic calculations** — "calculate 52 times 68 and save it in Word" works from speech.

## Run / test the voice
```bash
pip install -r requirements-voice.txt        # livekit-agents
# .env needs LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET (from `lk cloud auth`).
# .env.example ships none of these — add them yourself. No Google key is needed on the voice
# replay path: Gemini 3.5 runs via LiveKit Inference (model="google/gemini-3.5-flash").

python3 -m app.voice_agent console            # talk in the terminal (Mac mic/speaker) — easiest
python3 -m app.voice_agent dev                # or via the LiveKit Cloud Agent Console (agent name: rote)
```
Then say: *"create a word file"*, *"create my meeting notes"*, or *"calculate 52 times 68 and save it in Word"*.
First run of a task records to the registry; repeats save as `_2`, `_3`, … (nothing overwritten).

Pipeline: **Deepgram STT → Gemini 3.5 Flash → Cartesia TTS** (LiveKit Inference). NOTE: Gemini Live
*realtime* models were tried and can't do reliable tool-calling + spoken narration together, so the
STT→LLM→TTS pipeline is the working path (see commit messages).

## Files to review on merge
| File | Change |
|---|---|
| `app/voice_agent.py` | **new** — LiveKit voice agent; `run_skill(skill, calculation)` tool; presenter-style narration from the engine's event stream; latency tuning (preemptive TTS, short endpointing); skill list filtered to real desktop tasks. |
| `app/verified_replay.py` | `replay_verified(..., optimistic=False)` — verified per-step is the **default**; `optimistic` is **opt-in**, enabled at the call sites (`app/desktop_hud.py` passes `optimistic=not a.repair`; `app/desktop_cu.py` `replay()` passes `optimistic=True`). Optimistic = blind fast path + one final check (per-step inspection runs only to diagnose on failure, when `--repair`); `_ensure_unique_filename` (never overwrite); final check skips the expensive `inspect()`. |
| `app/desktop_hud.py` | opt-in `--events` (stdout `@@EV <json>` for the narrator) and `--params` (JSON overrides → `replay_verified`); sets `optimistic=not a.repair`, so passing `--repair` forces verified per-step replay (optimistic and `--repair` are mutually exclusive). |
| `app/macro_skill.py` | (touched then reverted — no net op changes). |
| `database/skills/calc_to_word.macro.json` | `expected_result` `6912 → 6,912` (Calculator copies a thousands comma). |
| `database/skills/save_word_document.macro.json` | `Cmd+A` before typing the filename (Word auto-fills the name from the doc's first line, which can contain an illegal `:`). |
| `requirements-voice.txt` | **new** — voice deps, separate so it won't collide with `requirements.txt`. |

## Bugs fixed (with evidence)
1. **calc stopped after the copy** — macro asserted clipboard `6912`, Calculator copies `6,912`; the
   `clipboard_contains` substring check failed and the engine halted. → fixed the expected value.
2. **save produced no file / wrong name** — filename field wasn't cleared, so Word's auto-name (with
   a `:`) won. → `Cmd+A` clear before typing.

## Continuous learning (the hackathon theme) — still intact
The registry records every run, and the verified **diagnose + repair** path is unchanged (run a skill
with `--repair`). Optimistic mode just defers the slow per-step verification to the failure case.
Designed-but-not-built next step: a background **shadow verifier** that audits each step off the
critical path for live per-step learning (notes in chat history).

## Merged to main (changelog)
`feat/v0.1` is merged to `main` via **PR #9 ("make optimistic replay opt-in")**, which also flipped the
`replay_verified` default back to verified per-step (optimistic now opt-in at the call sites). Voice +
macro changes landed additively.
