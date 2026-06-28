---
id: skill-web-or-desktop-001
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
validations_count: 1
---

# Skill: Create a Word file and type a sentence (macOS)

A verified, step-by-step recipe. Follow the steps **in order**, re-locating each
target visually on the CURRENT screenshot (do not assume fixed coordinates).

## Steps
1. Press **Command+Space** to open Spotlight.
2. Type **`Microsoft Word`**, then press **Return**. Wait for Word to finish launching.
3. If a template gallery appears, double-click **Blank Document** to create a new doc.
4. Click into the empty document body, then type: **`{text}`**
5. Press **Command+S** to open the Save dialog.
6. In the **Save As** field, type the filename **`{filename}`** (clear any existing text first with Command+A).
7. Set the save location to **`{location}`** (e.g. select Desktop in the sidebar).
8. Click **Save** (or press **Return**) to write the `.docx` file.

## Success check
A file named `{filename}.docx` exists in `{location}` and contains the text `{text}`.
