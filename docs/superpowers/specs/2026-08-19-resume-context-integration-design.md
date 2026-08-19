# Resume Context Integration

## Goal

Let the user load their resume/CV once so that suggested replies in the live transcript pipeline can draw on it — answering background/experience questions ("tell me about yourself", "what have you worked on") in first person, grounded in the user's actual history instead of generic text.

## Context: existing pipeline

`tray_app.py`'s transcript pipeline already turns each finalized utterance into a suggested reply: `_response_worker` pulls a transcript string off `response_queue`, looks up the key for whichever provider is selected (`self.response_provider`: Claude/Gemini/GPT), and calls `_generate_response_streaming` (Claude, streaming) or `_generate_response` (Gemini/GPT, non-streaming) — both currently build their prompt from the constant `RESPONSE_SYSTEM_PROMPT` plus `_build_conversation(transcript)` (the last few exchanges from `response_history`). This spec adds resume grounding to that same system-prompt construction, for all three providers, rather than adding a parallel pipeline.

The separate screen-analysis pipeline (`_generate_screen_analysis`, triggered by the "Analyze screen" button/`ctrl+alt+s`) is unrelated — it answers questions about on-screen content, not the user's background — and is not touched by this feature.

The existing "API Settings" dialog (`_open_settings_dialog`) already manages provider API keys via `keyring`/Windows Credential Manager, with a single Toplevel window that's registered for capture-exclusion via `_protect_extra_window` so it stays hidden from screen shares in privacy mode. This feature adds a resume-management section to that same dialog.

## Resume ingestion

- New "Resume" section in the API Settings dialog: a status label, a "Load resume..." button, and a "Clear resume" button.
- "Load resume..." opens a native file picker (`tkinter.filedialog.askopenfilename`) filtered to `*.pdf;*.txt`. This native picker is a separate top-level window that is **not** added to the capture-exclusion set (see Out of scope) — loading a resume is a one-time setup action done before a call starts, not during a live screen share.
- On selection:
  - `.txt` files are read directly as UTF-8 text (errors="replace" to tolerate odd encodings).
  - `.pdf` files have their text extracted via `pypdf` (`PdfReader`, concatenating `page.extract_text()` across all pages). This is a new dependency (pure Python, no external binary).
  - The extracted text is capped at `RESUME_MAX_CHARS = 6000` characters; if longer, it's truncated and a `\n\n[resume truncated — original was N characters]` note is appended, so a single oversized document can't silently balloon the token cost of every subsequent reply.
  - The result is written to `resume_context.txt` next to `tray_app.py` (plain UTF-8 text — this is the parsed/cached text, not a copy of the original file) and loaded into `self.resume_context` in memory.
  - The status label updates to `Resume loaded: {len(text)} characters from {filename}`.
- "Clear resume" deletes `resume_context.txt` (if present) and resets `self.resume_context` to `""`, updating the status label to `No resume loaded`.
- On app startup, `resume_context.txt` is read into `self.resume_context` if it exists (empty string otherwise) — this is how the resume persists across restarts. It is **not** stored via `keyring`/Windows Credential Manager, whose per-secret size limits are too small for a full resume.
- Parse failures (corrupt PDF, unreadable file, unsupported extension) are caught and shown inline in the settings dialog's status line (e.g. `Couldn't read resume: {error}`) rather than crashing the dialog or silently loading nothing.

## Reply pipeline integration

- New method `_build_response_system_prompt()`:
  - Returns `RESPONSE_SYSTEM_PROMPT` unchanged if `self.resume_context` is empty.
  - Otherwise returns `RESPONSE_SYSTEM_PROMPT` plus an appended block containing the resume text and an instruction to draw on it naturally and only when relevant (e.g. background/experience/"tell me about yourself" questions) — in first person, without inventing anything not present in the resume, and without forcing it into replies where it doesn't apply (e.g. small talk, logistics questions).
- `_generate_response_streaming` (Claude) and `_generate_response` (Gemini/GPT) both call `_build_response_system_prompt()` instead of referencing the `RESPONSE_SYSTEM_PROMPT` constant directly, so resume grounding applies regardless of which provider is currently selected in the Response provider dropdown.
- No changes to `_build_conversation`/`response_history` — resume context lives in the system prompt, not the per-turn conversation history, since it's static context rather than part of the live exchange.

## Error handling

- Missing/unreadable `resume_context.txt` at startup: treated the same as "no resume loaded" (empty string), not an error — the file may simply not exist yet.
- PDF/TXT parse failures during "Load resume...": caught, surfaced in the settings dialog, and the previously loaded resume (if any) is left untouched (a failed re-load doesn't wipe out a working one).

## Out of scope

- No capture-exclusion handling for the native file-picker dialog itself (documented rationale above).
- No support for DOCX or other resume formats — PDF and TXT only.
- No conditional/selective inclusion logic (e.g. keyword-matching to decide when to attach resume context) — it's always included in the system prompt when loaded, and the prompt instructs the model to use it only when relevant.
- No UI for viewing/editing the extracted resume text beyond the character-count status line — if the user wants to change it, they re-load a file or edit `resume_context.txt` directly.

## Verification

- `tray_app.py` still imports/compiles successfully with the new `pypdf` dependency added to the header's `pip install` line.
- Manual test: load a real PDF resume, confirm the settings status line shows a plausible character count, confirm `resume_context.txt` is created with readable text.
- Manual test: ask (via live transcript or by typing into the transcript path) a background question like "tell me about yourself" and confirm the suggested reply references real resume content, for at least the Claude provider.
- Manual test: switch the Response provider dropdown to Gemini and GPT in turn and confirm resume-grounded replies still work for each.
- Manual test: "Clear resume", confirm the status line reverts to "No resume loaded" and `resume_context.txt` is removed, and confirm subsequent replies no longer reference resume content.
- Manual test: restart the app after loading a resume, confirm it's still loaded (persistence via `resume_context.txt`) without needing to re-upload.
- Manual test: attempt to load a corrupt/invalid PDF and confirm an inline error appears instead of a crash.
