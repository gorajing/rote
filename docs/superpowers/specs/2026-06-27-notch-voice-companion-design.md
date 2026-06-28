# Notch voice companion — design spec

**Date:** 2026-06-27 · **Branch:** `feat/notch-voice-companion`

Turn the one-shot replay HUD at the MacBook notch into a **persistent Dynamic-Island companion**
that reflects the whole voice interaction — idle → listening → (your transcript) → thinking →
speaking → the task replay ring → back to idle — as one surface that never disappears.

## Goal / non-goals
- **Goal:** the voice experience lives in the notch, not just the terminal. One morphing island
  through the full loop. Launch stays `python3 -m app.voice_agent console`.
- **Non-goals:** no browser/web UI; no change to the replay engine or skills; voice must still work
  with no notch present (graceful no-op).

## Architecture (preserves the main-thread / asyncio split)
Two processes, talking over a Unix domain socket — chosen so one persistent notch serves both the
voice states and the replay progress, and either side can restart independently.

```
you speak → AgentSession event → voice_agent → NotchClient → /tmp/rote-notch.sock
          → notch_daemon → NotchIsland (AppKit main thread) → pill expands, renders state
replay:   desktop_hud --headless emits @@EV → voice_agent → NotchClient → … → working ring
```

## Components (clean boundaries)
| Unit | Responsibility | Depends on |
|---|---|---|
| `app/notch.py` (refactor) | `NotchIsland`: persistent, state-driven UI. Pill ⇄ expanded; renders `idle/listening/thinking/speaking/working/done/error` from a `STATE` dict. **Pure UI** — no sockets, no voice. Drops auto-terminate; "done" collapses to pill. | AppKit/Quartz |
| `app/notch_daemon.py` (new) | Unix-socket server. Reads newline-JSON messages, updates `STATE`. Runs `NotchIsland` on the main thread; socket reader on a worker thread. Entry: `python -m app.notch_daemon`. Self-exits if no client for N seconds. | `notch` |
| `app/notch_client.py` (new) | `NotchClient.send(mode, **kw)` — non-blocking, fire-and-forget; **silently no-ops if the socket is absent**. Auto-spawns the daemon on first use (optional). | stdlib `socket`/`json` |
| `app/voice_agent.py` (wire-in) | Spawn daemon at startup; subscribe to session events → forward to notch; run replay `--headless` and forward `@@EV`; tear daemon down on exit. | `notch_client` |
| `app/desktop_hud.py` (small) | Add `--headless`: run the replay + emit `@@EV` without spawning its own `NotchIsland` (so voice never gets a 2nd notch). | — |

## Protocol (newline-delimited JSON over the socket)
```json
{"mode":"listening","title":"Listening…","subtitle":"calculate 52 times 68"}
{"mode":"thinking","title":"Thinking…"}
{"mode":"speaking","title":"Speaking"}
{"mode":"working","title":"Saving to Desktop","i":3,"total":7}
{"mode":"done","title":"Done"}
{"mode":"error","title":"Couldn't finish"}
{"mode":"idle"}
```
Unknown keys ignored; missing keys keep prior value. `level` (0–1, optional) drives the mic pulse.

## Event mapping (verified against LiveKit docs)
| Session event | Field | Notch |
|---|---|---|
| `agent_state_changed` | `new_state` listening/thinking/speaking/idle | mode |
| `user_input_transcribed` | `transcript`, `is_final` | listening subtitle (live) |
| `user_state_changed` | VAD speaking | mic pulse `level` |
| `error` (unrecoverable) | — | error |
| replay `@@EV` step | `i`,`total`,`why`,`op` | working ring + title |

## Design layer (three lenses)
- **Concept (frontend-design):** native is the bold move — SF Pro, `controlAccentColor`,
  hardware-blended black. One surface that morphs through the whole loop. No orb, no waveform, no
  purple (rejected category reflexes).
- **Hierarchy (impeccable):** Restrained color (tinted near-black + one accent; pure black only at
  the top edge to blend the notch bezel). Title = state verb (SF semibold 13.5); subtitle = content
  (regular 10.5, 0.5α). "step x/y" only in `working`. Terse copy, no em dashes. No glass.
- **Motion (emil-design-eng):** pill⇄expanded = **interruptible** `CASpringAnimation` (Apple spring,
  bounce ~0.18, from scale 0.9 — never 0). Collapse snappy 200ms ease-out `(0.23,1,0.32,1)`
  (asymmetric). State-text swap = opacity crossfade (+ subtle blur). Listening dot pulses on live
  VAD level (spring) else breathes. Thinking = fast indeterminate arc (perceived speed). Done =
  green-check draw + spring pop, hold ~1.5s, collapse. `prefers-reduced-motion`: opacity-only.

## Window strategy
Keep one transparent `W×H` window; animate the **panel layer's** bounds/mask path between a small
pill rect and the full panel (grows down from the notch top-center). NSWindow itself is not resized
(avoids jank). Ring/dot anchored left; text fades per state.

## Lifecycle
- `voice_agent` spawns `notch_daemon` as a child; terminates it on shutdown.
- Daemon self-exits if no client connects/pings within ~30s (no orphan notch).
- Stale socket file removed on daemon start.

## Testing
- `notch_daemon` + `notch_client` are tested **headless**: feed scripted JSON, assert `STATE`
  transitions — no mic, no AppKit window. (The current notch has no tests; this adds real coverage.)
- Manual smoke: `python -m app.notch_daemon` then a scripted client driving every mode.

## Risks
- AppKit pill⇄expand morph polish — reuse existing spring/ease vocabulary in `notch.py`.
- LiveKit `console` vs `dev` mode: notch shows on the machine running the agent (fine for local demo).
- Mic-level pulse is a progressive enhancement; falls back to breathe if level isn't available.
