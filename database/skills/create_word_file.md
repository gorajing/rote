---
id: skill-desktop-create-word-001
title: Create a Microsoft Word file on macOS and save it to the Desktop
platform: desktop
os: macos
app: Microsoft Word
purpose: generate
params:
  - name: text
    description: the sentence to type into the document
  - name: filename
    description: the name to save the document as (without extension)
  - name: location
    description: where to save (folder name visible in the save dialog)
---

# Skill: Create a Word file and type a sentence (macOS)

A verified, step-by-step recipe. Follow the steps **in order**, re-locating each
target visually on the CURRENT screenshot (do not assume fixed coordinates).

## Steps
1. Word is launched and focused for you (the harness runs `open -a "Microsoft Word"`). If Word is not the frontmost window, press **Command+Tab** to switch to it. Do **not** use Spotlight / Command+Space.
2. Create a new blank document with **Command+N**. If a template chooser is focused, press **Return** to accept the blank document.
3. Click into the empty document body, then type: **`{text}`**
4. Press **Command+S** to open the Save dialog.
5. In the **Save As** field, type the filename **`{filename}`** (clear any existing text first with Command+A).
6. Press **Command+D** to jump to the Desktop. Only the Desktop is supported by the deterministic replay — **`{location}`** must be the Desktop.
7. Click **Save** (or press **Return**) to write the `.docx` file.

## Success check
A file named `{filename}.docx` exists in `{location}` and contains the text `{text}`.

> Note: the run never overwrites an existing file. If `{filename}.docx` already exists on the Desktop, it is saved as `{filename}_2.docx` (then `_3`, …) instead.
