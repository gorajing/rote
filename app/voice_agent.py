"""Rote voice agent — talk to the fastest desktop agent in the world.

LiveKit Agents voice pipeline (STT -> Gemini 3.5 Flash -> TTS) whose single job is to map a spoken
request to a *learned skill* and replay it on the Mac instantly (0 tokens, 0 model calls). The
desktop automation + notch HUD run in a SEPARATE process (`app.desktop_hud`) so AppKit's main-thread
run loop never clashes with this agent's asyncio loop.

  Gemini 3.5 Flash is reached through LiveKit Inference (model="google/gemini-3.5-flash") — no
  separate Google key needed; it runs on LiveKit infra and bills to your LiveKit credits.

Run (dev mode, connects to your LiveKit Cloud project and the Agent Console):
  python3 -m app.voice_agent dev
"""
import asyncio
import atexit
import json
import os
import random
import re
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    AgentServer, AgentSession, Agent, RunContext, function_tool, inference, TurnHandlingOptions,
)
from livekit.agents.llm import ToolError

import tempfile

from .notch_client import NotchClient, ensure_daemon
from . import skill_store
from .desktop_skill_compiler import compile_macro

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")                       # LIVEKIT_* + GEMINI/GOOGLE keys

# Skills live ONLY in MongoDB (the `tasks` collection) — no local files at runtime. database_get
# searches it, computer_use learns + pushes to it. _FOUND caches the macro from the last successful
# search so run_skill replays it without a second round-trip.
_FOUND: dict[str, dict] = {}

# The persistent notch companion. NOTCH.send() is fire-and-forget and no-ops if the notch isn't up,
# so voice works with or without it. _BUSY guards the working ring from being overridden by the
# agent's own speaking/listening state changes mid-replay.
NOTCH = NotchClient()
_BUSY = {"skill": False}
_DAEMON = None

# Push-to-talk: hold a key to talk, otherwise the mic is muted. Opt-in via ROTE_PTT=1 — essential in
# a loud room (a hackathon) where ambient speech keeps interrupting the agent. Key via ROTE_PTT_KEY
# (a pynput Key name, default right shift).
_PTT = os.getenv("ROTE_PTT", "").lower() in ("1", "true", "yes", "on")
_PTT_KEY_NAME = os.getenv("ROTE_PTT_KEY", "shift_r")


def _start_notch():
    """Spawn the notch daemon once and make sure it's torn down on exit."""
    global _DAEMON
    if _DAEMON is not None:
        return
    _DAEMON = ensure_daemon()
    if _DAEMON is not None:
        atexit.register(lambda: _DAEMON and _DAEMON.terminate())


def _skill_catalog() -> dict[str, str]:
    """User-facing tasks {name: description}, read from MongoDB (the `tasks` collection). DB-only —
    no local files. Empty if the DB is unreachable/empty; the agent then learns on demand."""
    return {s["name"]: s["description"] for s in skill_store.list_skills()}


def _keyterms(catalog: dict[str, str]) -> list[str]:
    """Bias the recognizer toward our fixed command vocabulary + skill names (big accuracy win)."""
    terms = ["Rote", "Word", "Microsoft Word", "Calculator", "Desktop", "meeting notes",
             "calculation", "create", "save", "file", "document", "spreadsheet"]
    for key in catalog:
        terms += key.replace("_", " ").split()
    seen, out = set(), []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out


CATALOG = _skill_catalog()
KEYTERMS = _keyterms(CATALOG)
print(f"[ROTE] startup: loaded {len(CATALOG)} skill(s) from MongoDB: "
      f"{', '.join(CATALOG) or '(none — DB empty/unreachable)'}", flush=True)


def _say(session, text: str) -> None:
    """Speak a phrase without blocking (fire-and-forget), so narration tracks the live action."""
    r = session.say(text)
    if asyncio.iscoroutine(r):
        asyncio.ensure_future(r)


def _eval_calc(expr: str) -> str | None:
    """Safely evaluate arithmetic and format like macOS Calculator (e.g. 52*68 -> '3,536').
    Only digits and + - * / ( ) are allowed; returns None if it can't be parsed."""
    expr = (expr or "").replace(" ", "")
    if not expr or not re.fullmatch(r"[0-9+\-*/().]+", expr):
        return None
    try:
        val = eval(expr, {"__builtins__": {}}, {})       # sandboxed: no builtins, validated chars
    except Exception:
        return None
    if isinstance(val, float):
        if val.is_integer():
            val = int(val)
        else:
            return str(val)                              # best-effort for non-integer results
    return f"{val:,}"                                     # thousands comma, matching Calculator


# Conversational, presenter-style narration — varied so it never sounds canned.
_INTROS = ["Sure thing — watch how fast this is.", "Happy to. Here we go.",
           "On it. Let me walk you through it.", "You got it. Check this out."]
_OPEN_FIRST = ["First, I'm pulling up {app}.", "Okay, let me open {app} to start.",
               "Alright, kicking things off in {app}."]
_OPEN_NEXT = ["Now I'm hopping over to {app}.", "Great — switching across to {app}.",
              "Next, let me jump into {app}."]
_WORKING = ["Now let me work through it.", "Okay, putting it together for you.",
            "Give me one sec, I'm on it."]
_SAVING = ["And I'm saving it right onto your Desktop.", "Last step, dropping it onto your Desktop.",
           "And finally, tucking it away on your Desktop."]
_DONE = ["And that's it, all done and saved to your Desktop, and it didn't cost a single token.",
         "There we go, finished and waiting on your Desktop, with zero AI tokens used.",
         "All set, it's on your Desktop now, and that ran completely free."]


def _milestones(steps: list) -> dict[int, str]:
    """Map step numbers (1-based) to natural, flowing spoken lines, so the agent narrates like a
    presenter walking the user through it, not a robot reading commands."""
    out, opened, save_seen, wrote = {}, 0, False, False
    for i, s in enumerate(steps, 1):
        op = s.get("op")
        if op == "open_app":
            app = s.get("app", "the app")
            out[i] = random.choice(_OPEN_FIRST if opened == 0 else _OPEN_NEXT).format(app=app)
            opened += 1
        elif op == "hotkey" and {k.lower() for k in s.get("keys", [])} == {"command", "s"} and not save_seen:
            save_seen = True
            out[i] = random.choice(_SAVING)
        elif op == "type" and not wrote and not save_seen and len(str(s.get("text", ""))) > 4:
            wrote = True
            out[i] = random.choice(_WORKING)
    return out


class RoteAssistant(Agent):
    def __init__(self):
        self._catalog = CATALOG
        listing = "\n".join(f"  - {k}: {v}" for k, v in self._catalog.items()) or "  (none yet)"
        super().__init__(instructions=(
            "You are Rote, a fast voice assistant that performs tasks directly on the user's Mac. "
            "Your skills live in a database. For EVERY task request, follow this exact flow:\n"
            "1. ALWAYS call database_get first, passing a short description of what the user wants. "
            "It searches the skill database.\n"
            "2. If database_get returns a skill name, immediately call run_skill with that exact "
            "name to replay it (instant, free). For a calculation (e.g. '52 times 68'), use the "
            "matched skill and set the 'calculation' argument to a math expression like '52*68'.\n"
            "3. If database_get finds nothing, immediately call computer_use with the user's full "
            "request as the 'intent'. That figures the task out live AND learns it, so next time it "
            "is instant. Never tell the user you cannot do a task — learn it with computer_use.\n"
            "Always actually invoke the tools, never just say you will. Speak warmly and "
            "conversationally, like a friendly live demo, never robotic. No markdown, lists, or emojis.\n\n"
            f"Skills currently in the database (name: what it does):\n{listing}"
        ))

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        # In push-to-talk, a key tap with no speech commits an empty turn; don't reply to nothing.
        if not (getattr(new_message, "text_content", "") or "").strip():
            from livekit.agents.llm import StopResponse
            raise StopResponse()

    @function_tool()
    async def database_get(self, context: RunContext, description: str) -> str:
        """ALWAYS call this first for any task. Searches the MongoDB skill database semantically for
        a skill matching the user's request.

        Args:
            description: A short description of what the user wants done (e.g. "calculate two numbers
                and save the result in Word", "create a word file", "take meeting notes").

        Returns a message telling you whether a skill was found and, if so, its exact name to pass to
        run_skill. If nothing is found, call computer_use to learn the task.
        """
        print(f"[ROTE] database_get: searching MongoDB `tasks` for: {description!r}", flush=True)
        NOTCH.send("thinking", title="Searching skills…")
        macro = await asyncio.to_thread(skill_store.search, description)
        if macro is None:
            print("[ROTE] database_get: NO MATCH in MongoDB -> agent should call computer_use to learn", flush=True)
            return ("No matching skill is in the database. Call computer_use with the user's full "
                    "request as the intent to learn it now.")
        name = (macro.get("name") or "task").strip().replace(" ", "_").lower()
        calls = sum(1 for s in macro.get("steps", []) if s.get("op") == "call")
        print(f"[ROTE] database_get: MATCH '{name}' from MongoDB — {len(macro.get('steps', []))} steps, "
              f"call-ops={calls} ({'LOCAL DEPENDENCY!' if calls else 'self-contained'})", flush=True)
        _FOUND[name] = macro                          # cache so run_skill needs no second DB round-trip
        return f"Found a learned skill named '{name}'. Call run_skill with skill='{name}' now."

    @function_tool()
    async def run_skill(self, context: RunContext, skill: str, calculation: str = "") -> str:
        """Replay a skill found via database_get on the user's Mac right now. Only call this after
        database_get returned a skill name.

        Args:
            skill: The exact skill name database_get returned.
            calculation: Only for the calculation skill — the arithmetic the user asked for, as a
                plain math expression using + - * / (for example "52*68" for "52 times 68"). Leave
                empty for other skills or when no calculation was requested.
        """
        skill = skill.strip().replace(" ", "_").lower()
        cached = skill in _FOUND
        macro = _FOUND.get(skill) or await asyncio.to_thread(skill_store.search, skill.replace("_", " "))
        if macro is None:
            print(f"[ROTE] run_skill '{skill}': NOT in MongoDB", flush=True)
            raise ToolError(
                f"No skill '{skill}' in the database. Use computer_use to learn it first."
            )
        calls = sum(1 for s in macro.get("steps", []) if s.get("op") == "call")
        print(f"[ROTE] run_skill '{skill}': replaying macro from "
              f"{'DB cache' if cached else 'fresh MongoDB search'} — {len(macro.get('steps', []))} steps, "
              f"call-ops={calls} ({'LOCAL DEPENDENCY!' if calls else 'self-contained, DB-only'})", flush=True)
        context.disallow_interruptions()             # desktop action — don't cut it off mid-run
        pretty = skill.replace("_", " ")

        # write the DB macro to a temp file so the (file-based) replay CLI can run it
        tmp = tempfile.NamedTemporaryFile("w", suffix=".macro.json", delete=False)
        json.dump(macro, tmp, default=str); tmp.close()
        replay_path = tmp.name
        # --headless: the replay emits @@EV but does NOT open its own notch; this agent owns the one
        # persistent notch and renders the step stream onto it (see the @@EV loop below).
        cmd = ["python3", "-u", "-m", "app.desktop_hud", "--replay", replay_path, "--events", "--headless"]
        if calculation.strip():                       # dynamic calculation -> override macro params
            expected = _eval_calc(calculation)
            if expected is None:
                raise ToolError("I couldn't work out that calculation. Try, say, fifty-two times sixty-eight.")
            cmd += ["--params", json.dumps({"calculation": calculation.replace(" ", ""),
                                            "expected_result": expected})]

        _say(context.session, random.choice(_INTROS))
        _BUSY["skill"] = True                         # working ring owns the notch until we finish
        NOTCH.send("working", title="Starting…", i=0, total=1)
        # run main's verified replay in a subprocess (headless), asking it to stream engine events
        # (@@EV json) which we narrate live AND render on the notch. -u = unbuffered.
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(REPO),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        tail = []
        opened, said_work, said_save = 0, False, False
        saved_as = None                               # the real (possibly de-duped) filename
        assert proc.stdout is not None
        try:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "ignore")
                tail.append(line)
                if not line.startswith("@@EV "):
                    continue
                try:
                    ev = json.loads(line[5:])
                except Exception:
                    continue
                kind = ev.get("kind")
                if kind == "step":
                    op = ev.get("op") or ""
                    why = (ev.get("why") or "").lower()
                    keys = {str(k).lower() for k in (ev.get("keys") or [])}
                    ref = (ev.get("skill") or "").lower()
                    app = ev.get("app") or "the app"
                    is_save = keys == {"command", "s"} or "save" in ref or "save" in why
                    NOTCH.send("working", i=ev.get("index", 0), total=ev.get("total", 1),
                               title=_notch_step_title(op, app, is_save))
                    if op == "open_app":
                        _say(context.session,
                             random.choice(_OPEN_FIRST if opened == 0 else _OPEN_NEXT).format(app=app))
                        opened += 1
                    elif is_save and not said_save:
                        said_save = True
                        _say(context.session, random.choice(_SAVING))
                    elif op in ("type", "call") and not said_work:
                        said_work = True
                        _say(context.session, random.choice(_WORKING))
                elif kind == "repairing":
                    NOTCH.send("working", title="Fixing a step…")
                    _say(context.session, "One sec, let me fix a step for you.")
                elif kind == "promoted":
                    _say(context.session, "Fixed and verified.")
                elif kind == "rejected":
                    _say(context.session, "Hmm, that fix didn't hold.")
                elif kind == "result" and ev.get("filename"):
                    saved_as = f"{ev['filename']}.docx"   # closes the loop + shows the no-overwrite name
            await proc.wait()
            if proc.returncode != 0:
                NOTCH.send("error", title="Couldn't finish")
                await asyncio.sleep(1.4)              # let the error read before it collapses
                raise ToolError(f"The {pretty} task didn't complete: {''.join(tail)[-300:]}")
            NOTCH.send("done", title="Done", subtitle=(f"Saved {saved_as}" if saved_as else ""))
            await asyncio.sleep(1.2)                  # hold the green check before the spoken wrap-up
            return random.choice(_DONE)
        finally:
            _BUSY["skill"] = False
            try:
                os.unlink(replay_path)
            except OSError:
                pass

    @function_tool()
    async def computer_use(self, context: RunContext, intent: str) -> str:
        """Learn a NEW task the database doesn't have yet. Drives the Mac with Gemini computer-use to
        do the task live, then compiles what it did into a skill and pushes it to the database, so
        next time database_get finds it and it replays instantly. Only call this after database_get
        found nothing.

        Args:
            intent: The user's full request, phrased as a task to perform (e.g. "Create a new
                Microsoft Word document, type 'Hello', and save it to the Desktop as notes").
        """
        print(f"[ROTE] computer_use: LEARNING new task via Gemini computer-use: {intent!r}", flush=True)
        context.disallow_interruptions()              # long live operation — don't cut it off
        _BUSY["skill"] = True
        NOTCH.send("thinking", title="Learning this…", subtitle=intent[:44])
        _say(context.session, "I haven't done this one before. Let me work it out live, watch.")
        trace = tempfile.NamedTemporaryFile("w", suffix=".trace.json", delete=False); trace.close()
        cmd = ["python3", "-u", "-m", "app.desktop_cu", "--intent", intent, "--trace", trace.name]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(REPO),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            tail = []
            assert proc.stdout is not None
            async for raw in proc.stdout:
                tail.append(raw.decode("utf-8", "ignore"))
            await proc.wait()
            if proc.returncode != 0:
                NOTCH.send("error", title="Couldn't finish")
                await asyncio.sleep(1.4)
                raise ToolError(f"I couldn't complete that task: {''.join(tail)[-300:]}")

            # the doer recorded a trace (its logs); the compiler (a 2nd Gemini pass) authors the skill
            NOTCH.send("thinking", title="Saving the new skill…")
            with open(trace.name, encoding="utf-8") as fh:
                trace_data = json.load(fh)
            macro = await asyncio.to_thread(compile_macro, trace_data)
            name = (macro.get("name") or "task").strip().replace(" ", "_").lower()
            await asyncio.to_thread(skill_store.save_skill, macro, intent, name)
            _FOUND[name] = macro                      # ready to replay immediately if asked again
            NOTCH.send("done", title="Learned", subtitle=name.replace("_", " "))
            await asyncio.sleep(1.2)
            return ("Done — I worked it out and saved it to the database, so next time it runs "
                    "instantly with no thinking.")
        finally:
            _BUSY["skill"] = False
            try:
                os.unlink(trace.name)
            except OSError:
                pass


def _notch_step_title(op: str, app: str, is_save: bool) -> str:
    """Terse, presentable label for the notch (the spoken narration stays conversational)."""
    if op == "open_app":
        return f"Opening {app}"
    if is_save:
        return "Saving to Desktop"
    return "Working…"


def _wire_notch(session) -> None:
    """Forward AgentSession state to the persistent notch. All handlers no-op while a skill is
    replaying (the working ring owns the notch then) and silently no-op if no notch is up."""
    @session.on("agent_state_changed")
    def _on_state(ev):
        if _BUSY["skill"]:
            return
        st = getattr(ev, "new_state", None)
        if st == "listening":
            NOTCH.send("listening", title="Listening…")
        elif st == "thinking":
            NOTCH.send("thinking", title="Thinking…")
        elif st == "speaking":
            NOTCH.send("speaking", title="Speaking")
        elif st == "idle":
            NOTCH.send("idle", title="Rote", subtitle="")

    @session.on("user_input_transcribed")
    def _on_tx(ev):
        if _BUSY["skill"]:
            return
        text = (getattr(ev, "transcript", "") or "").strip()
        if text:
            NOTCH.send("listening", title="Listening…", subtitle=text[-44:])

    @session.on("user_state_changed")
    def _on_user(ev):
        if _BUSY["skill"]:
            return
        NOTCH.send(level=1.0 if getattr(ev, "new_state", None) == "speaking" else 0.0)

    @session.on("error")
    def _on_err(ev):
        err = getattr(ev, "error", None)
        if err is not None and not getattr(err, "recoverable", True):
            NOTCH.send("error", title="Couldn't finish")


def _setup_ptt(session) -> None:
    """Hold-to-talk via a global hotkey (pynput). The mic stays muted until the key is held, so a
    noisy room can't form a turn or interrupt the agent. Key-down: interrupt + start listening;
    key-up: commit the turn. pynput callbacks run off-thread, so we marshal session calls back onto
    the event loop with call_soon_threadsafe."""
    try:
        from pynput import keyboard
    except Exception as exc:                          # pynput missing or no Input-Monitoring permission
        print(f"[ROTE] push-to-talk unavailable ({exc}); falling back to open mic.", flush=True)
        session.input.set_audio_enabled(True)
        return
    loop = asyncio.get_running_loop()
    ptt_key = getattr(keyboard.Key, _PTT_KEY_NAME, keyboard.Key.shift_r)
    state = {"talking": False}
    session.input.set_audio_enabled(False)            # start muted
    print(f"[ROTE] push-to-talk ON — hold '{_PTT_KEY_NAME}' to talk, release to send.", flush=True)

    def _start():
        session.interrupt(); session.clear_user_turn(); session.input.set_audio_enabled(True)
        NOTCH.send("listening", title="Listening…")

    def _end():
        session.input.set_audio_enabled(False); session.commit_user_turn()

    def on_press(key):
        if key == ptt_key and not state["talking"] and not _BUSY["skill"]:
            state["talking"] = True
            loop.call_soon_threadsafe(_start)

    def on_release(key):
        if key == ptt_key and state["talking"]:
            state["talking"] = False
            loop.call_soon_threadsafe(_end)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()


server = AgentServer()


@server.rtc_session(agent_name="rote")
async def rote(ctx: agents.JobContext):
    # STT -> Gemini 3.5 Flash -> Cartesia. The only config that reliably supports tool calling +
    # spoken step-narration + a greeting (the realtime Live models support none of those together).
    # Tuned for low latency: preemptive TTS + short endpointing so it doesn't feel laggy.
    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="en",
                          extra_kwargs={"keyterm": KEYTERMS}),
        llm=inference.LLM(model="google/gemini-3.5-flash"),
        tts=inference.TTS(model="cartesia/sonic-3",
                          voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
        turn_handling=TurnHandlingOptions(
            # manual turn-taking when push-to-talk is on, so ambient room noise never forms a turn
            turn_detection=("manual" if _PTT else inference.TurnDetector()),
            endpointing={"mode": "fixed", "min_delay": 0.2, "max_delay": 2.5},  # was 0.5 -> snappier
            preemptive_generation={"preemptive_tts": True},                     # start TTS early
        ),
    )
    _start_notch()                                   # bring up the persistent notch companion
    _wire_notch(session)                             # forward listening/thinking/speaking + transcript
    await session.start(room=ctx.room, agent=RoteAssistant())
    if _PTT:
        _setup_ptt(session)                          # hold-to-talk via global hotkey; mic muted otherwise
    NOTCH.send("idle" if _PTT else "listening", title="Hold ⇧ to talk" if _PTT else "Listening…")
    # generate_reply is ignored on 3.1 live models, so greet with a direct spoken line
    greeting = ("Hey, I'm Rote. Hold the shift key while you talk, then let go and I'll run it."
                if _PTT else
                "Hey, I'm Rote. Tell me a task and I'll run it on your Mac instantly.")
    _say(session, greeting)


if __name__ == "__main__":
    agents.cli.run_app(server)
