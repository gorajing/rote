# Team Protocol — Rote

Why this exists: `main` went red because changes were pushed straight to it. The fix is not "be
more careful" — it's a process that makes the failure mode impossible. Read this before you push.

## The five-step sequence (do them in order — step 1 blocks everything)

1. **Green main.** No work proceeds on a red `main`. A red `main` freezes all merges until it's green.
2. **Make the engine robust.** Diagnose the real generalization bugs and run the **full 11-task bank**
   for a real, reproducible number — not a hand-picked subset.
3. **Converge the architecture.** Collapse to **one skill schema → one store → the vector DB as the
   index**. Today we have four schemas, three stores, two engines. That is the real risk, not features.
4. **Bulletproof the demo + the unfakeable metric.** The metric is "CU N→0, verified by ground truth."
   Protect the demo path above all else.
5. **Converge the team on the shared core.** Everyone builds on the one engine + one store.

Voice and MCP are optional flourishes. They never destabilize the demo or the shared core.

## The rules

1. **`main` is protected. No direct pushes.** The direct push is exactly what broke it. Merge only via
   PR with a green test run. A red test run freezes merges.
2. **Sequence, not parallel.** Follow the five steps above. Uncoordinated fan-out is the root risk —
   two people independently "fixing" the same thing is how you get conflicting engines.
3. **One owner per area, named in the PR.** Before you start, check nothing is already in-flight
   (`gh pr list`, ask in chat). If it is, join it — don't open a second front.
4. **Done means:** full test bank green **and** uses the shared schema/store **and** reviewed by one
   teammate **and** it went through a PR (never direct-to-main).

## No guessing (the whole point)

Don't paper over a red test to get green. Before "reconciling" a failure, decide which side is right:

- Is the test asserting a guarantee the new code deliberately dropped? → **fix the code.**
- Is the test asserting an obsolete contract? → **update the test, and say why in the PR.**

For a crash, get the actual traceback and fix the root cause. "It probably…" is not allowed in a PR
description. Reproduce, then fix, then show the green run.

## Areas & owners

Owners are confirmed per-PR. Current map (fill in / correct as needed):

| Area | Paths | Owner |
|---|---|---|
| Verified-macro engine | `app/verified_replay.py`, `app/skill_repair.py`, `app/local_skill_registry.py`, `app/verification.py`, `app/macro_skill.py` | ikjun |
| Fusion engine | `app/fusion/*` | Jin |
| Voice agent | `app/voice_agent.py`, `app/desktop_hud.py` | Shah |
| Retrieval / vector DB | `database/*` | Riccardo |
| MCP server | `app/mcp_server.py`, `app/mcp_service.py`, `app/skill_search_index.py` | TBD |

## Mechanics

- **Run the bank before you PR:** `.venv/bin/python -m unittest discover -s tests -v` → must be all green.
- **Open a PR:** `gh pr create` from your feature branch; name the owner and the area in the body.
- **CI (recommended):** add a GitHub Actions job that runs the test bank on every PR so "green CI" is
  enforced by the machine, not by trust. Until then, paste the local green run into the PR.
- **Branch protection (recommended):** enable "require PR before merge" + "require status checks" on
  `main` in GitHub settings so direct pushes are rejected by the platform.
