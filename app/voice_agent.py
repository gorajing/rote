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

from .notch_client import NotchClient, ensure_daemon

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")                       # LIVEKIT_* + GEMINI/GOOGLE keys
SKILLS_DIR = REPO / "database" / "skills"

# The persistent notch companion. NOTCH.send() is fire-and-forget and no-ops if the notch isn't up,
# so voice works with or without it. _BUSY guards the working ring from being overridden by the
# agent's own speaking/listening state changes mid-replay.
NOTCH = NotchClient()
_BUSY = {"skill": False}
_DAEMON = None


def _start_notch():
    """Spawn the notch daemon once and make sure it's torn down on exit."""
    global _DAEMON
    if _DAEMON is not None:
        return
    _DAEMON = ensure_daemon()
    if _DAEMON is not None:
        atexit.register(lambda: _DAEMON and _DAEMON.terminate())


def _skill_catalog() -> dict[str, str]:
    """User-facing DESKTOP tasks: {skill_key: human description}. Excludes browser skills,
    test fixtures (stale_*), and reusable subskills (building blocks have no final checker)."""
    out = {}
    for p in SKILLS_DIR.glob("*.macro.json"):
        key = p.name[:-len(".macro.json")]
        if key.startswith("stale_"):
            continue
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if m.get("surface", "desktop") != "desktop":      # skip browser skills
            continue
        if not m.get("checker"):                           # subskill / building block -> skip
            continue
        out[key] = m.get("note") or m.get("description") or m.get("name") or key
    return out


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
            "You are Rote, a fast voice assistant that performs tasks directly on the user's Mac by "
            "replaying skills it already learned, instantly and for free. When the user asks you to do "
            "something that matches a skill, you MUST immediately call the run_skill function with its "
            "key — actually invoke the tool, never just say that you will. If the user asks for a "
            "calculation (for example '52 times 68' or 'multiply 7 by 9'), call run_skill with skill "
            "'calc_to_word' and the 'calculation' argument set to a math expression like '52*68'. Speak "
            "warmly and conversationally, like a friendly assistant giving a live demo, never robotic "
            "and never reading a script. No markdown, no lists, no emojis. If nothing matches, "
            "say you have not learned that task yet and ask if they want you to learn it.\n\n"
            f"Your learned skills (key: what it does):\n{listing}"
        ))

    @function_tool()
    async def run_skill(self, context: RunContext, skill: str, calculation: str = "") -> str:
        """Replay a learned desktop skill on the user's Mac right now. Use this whenever the user
        asks you to perform a task that matches one of your known skills.

        Args:
            skill: The key of the skill to run, exactly one of the known skill keys.
            calculation: Only for the 'calc_to_word' skill — the arithmetic the user asked for, as a
                plain math expression using + - * / (for example "52*68" for "52 times 68"). Leave
                empty for other skills or when no calculation was requested.
        """
        skill = skill.strip().replace(" ", "_").lower()
        path = SKILLS_DIR / f"{skill}.macro.json"
        if not path.exists():
            raise ToolError(
                f"No skill named '{skill}'. Known skills: {', '.join(self._catalog) or 'none'}."
            )
        context.disallow_interruptions()             # desktop action — don't cut it off mid-run
        pretty = skill.replace("_", " ")

        # --headless: the replay emits @@EV but does NOT open its own notch; this agent owns the one
        # persistent notch and renders the step stream onto it (see the @@EV loop below).
        cmd = ["python3", "-u", "-m", "app.desktop_hud", "--replay", str(path), "--events", "--headless"]
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
            turn_detection=inference.TurnDetector(),
            endpointing={"mode": "fixed", "min_delay": 0.2, "max_delay": 2.5},  # was 0.5 -> snappier
            preemptive_generation={"preemptive_tts": True},                     # start TTS early
        ),
    )
    _start_notch()                                   # bring up the persistent notch companion
    _wire_notch(session)                             # forward listening/thinking/speaking + transcript
    await session.start(room=ctx.room, agent=RoteAssistant())
    NOTCH.send("listening", title="Listening…")
    # generate_reply is ignored on 3.1 live models, so greet with a direct spoken line
    _say(session, "Hey, I'm Rote. Tell me a task and I'll run it on your Mac instantly.")


if __name__ == "__main__":
    agents.cli.run_app(server)
