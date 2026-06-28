# learn-cu Artifact Contract

This document is the handoff target for the real hybrid learner. The current
`app.real_web_skill_lab build` command synthesizes a hybrid artifact from a page
snapshot; it is a schema/demo path, not proof that Gemini discovered the
workflow. A workflow is learned only when its segments come from successful
Gemini Computer Use traces and pass deterministic replay verification.

## Goal

The `learn-cu` orchestrator should turn one successful cross-surface run into a
checked-in or runtime `.hybrid.json` skill with:

- a browser segment compiled from `app.cu_runner` output,
- a desktop segment compiled from `app.desktop_cu` output,
- an explicit clipboard or parameter handoff between the segments,
- verifiers that prove the browser state, handoff value, and desktop result.

## Required Shape

```json
{
  "schema_version": 1,
  "kind": "hybrid_skill",
  "name": "real_web_textedit_note",
  "status": "active",
  "note": "Short user-facing description.",
  "params": {
    "url": "https://example.com/",
    "handoff_text": "Example Domain",
    "marker": "rote-real-web-..."
  },
  "segments": [
    {
      "id": "read_real_web_page",
      "surface": "browser",
      "source_trace": "traces/browser_real_web.json",
      "skill": {}
    },
    {
      "id": "write_textedit_note",
      "surface": "desktop",
      "source_trace": "traces/desktop_textedit.json",
      "skill": {}
    }
  ],
  "handoffs": [
    {
      "from_segment": "read_real_web_page",
      "to_segment": "write_textedit_note",
      "kind": "clipboard",
      "param": "handoff_text"
    }
  ],
  "checker": {
    "type": "condition",
    "condition": {
      "all": [
        {"clipboard_contains": "{{handoff_text}}"},
        {"textedit_document_contains": "{{marker}}"}
      ]
    }
  },
  "learned_at": "2026-06-28T00:00:00Z",
  "provenance": {
    "learned_by": "gemini-computer-use",
    "browser_trace": "traces/browser_real_web.json",
    "desktop_trace": "traces/desktop_textedit.json",
    "verified_replay": true
  }
}
```

Template-built artifacts should use `built_at` instead of `learned_at` and
should not include `provenance.learned_by`.

## Segment Requirements

Browser segments must come from a successful `Trajectory` recorded by
`app.cu_runner` and compiled through the fusion compiler. They should preserve
the source URL as `target` or `params.url`, and they must verify with at least
one deterministic page condition such as title, visible text, or clipboard
contents.

Desktop segments must come from a successful `app.desktop_cu` trace or a
desktop `Trajectory` adapter. They should compile into the existing macro
schema used by `app.verified_replay`, not a one-off replay runner.

The handoff should be explicit. Clipboard is the first supported bridge because
Gemini can select/copy text in the browser, and the desktop replay can paste or
type that same value into TextEdit.

## Verifier Requirements

Minimum verifier set for the first real demo:

- browser: page title or visible text confirms the real URL loaded,
- clipboard: clipboard text contains the copied browser value,
- desktop: TextEdit foreground document contains both the browser value and a
  unique marker,
- hybrid: all segment verifiers pass during 0-CU replay.

## Compiler Acceptance Notes

The browser trace adapter must preserve Gemini's spatial action shapes. In
particular, `drag_and_drop` traces may use `start_x`, `start_y`, `end_x`, and
`end_y` rather than the older `x`, `y`, `destination_x`, and `destination_y`
fields. The compiler should lower those into a non-empty drag step, with a crop
precondition anchored on the start point when a screenshot is available.

The desktop trace adapter should keep Gemini's high-level intent text per step.
That intent is what lets the compiler remove fumbles while retaining an
auditable explanation for replay.

