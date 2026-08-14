# Screen Capture Vision Analysis

## Goal

Let the user capture their own screen and have Claude analyze it (e.g. explain what's on screen, answer a visible question), while the ScreenGuard app's own UI stays invisible to any screen-share/recording tool via the existing privacy-mode capture exclusion.

## Context: existing pipeline

`tray_app.py` already has a speech pipeline: microphone/system audio is transcribed (Deepgram or GPT), each finalized transcript is pushed onto `self.response_queue`, and a single background thread (`_response_worker`) pulls jobs off that queue, calls an LLM (`response_provider`: Claude/Gemini/GPT), and streams the reply into `self.response_box`. This spec extends that same queue/worker instead of adding a parallel pipeline, so screen analyses and speech replies never write to `response_box` concurrently or out of order.

Privacy mode (`self.privacy_mode`, toggled via `ctrl+alt+p`) already calls `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` on the main window and every tracked extra window (combobox popdowns, the API Settings dialog). This affinity flag affects **all** capture methods system-wide (GDI BitBlt, PrintWindow, Desktop Duplication/DXGI) — not just third-party screen-share tools — so a screenshot taken by this app while privacy mode is on will not include the app's own window either. No extra masking step is required.

## Trigger & UI

- New hotkey `ctrl+alt+s`, registered alongside the existing hotkeys in `_start_hotkey_listener`, following the same `keyboard.add_hotkey(...)` pattern.
- New "Analyze screen" button placed in the response section of the main window (near the existing response status label), calling the same handler as the hotkey.
- Both entry points call a single method, `trigger_screen_analysis`, which — like `toggle_privacy_mode` — marshals onto the Tk main thread via `self.root.after(0, ...)` before touching any Tk/Win32 state.

## Capture

- `trigger_screen_analysis` (on the main thread) calls `PIL.ImageGrab.grab()` (no new dependency — `PIL.Image`/`ImageDraw` are already imported) to capture the primary monitor only.
- The captured image is downscaled (preserving aspect ratio, only if larger) so its long edge is at most 1568px, matching Anthropic's documented recommendation for image inputs — this keeps token cost and latency down since screenshots of UI/text don't need full native resolution.
- The resized image is encoded as PNG bytes, then base64-encoded.
- The capture + resize + encode happens synchronously on the main thread (a single `ImageGrab.grab()` + Pillow resize is fast, sub-100ms, consistent with other quick synchronous Win32 calls already done inline elsewhere in this file); the resulting base64 payload is then put on `self.response_queue` as a job, and the (slow) network call happens on the existing worker thread.

## Job representation on the queue

`response_queue` currently carries raw transcript strings. It's extended to carry either:
- a `str` (existing behavior: transcript from speech), or
- a small marker object/dict distinguishing an image job, e.g. `{"type": "image", "data": <base64 str>}`.

`_response_worker` branches on the job type: strings go through the existing transcript-response path (respecting `self.response_provider`); image jobs go through a new `_generate_screen_analysis` path that always uses Claude, regardless of `self.response_provider`.

## Sending to Claude

- New method `_generate_screen_analysis(api_key, image_b64)`, sibling to `_generate_response_streaming`, reuses `_get_anthropic_client`.
- Uses a fixed system prompt (new constant `SCREEN_ANALYSIS_SYSTEM_PROMPT`), e.g. instructing Claude to describe what's on screen and answer any visible question or problem concisely.
- Sends a single user message containing an `image` content block (`type: "base64"`, `media_type: "image/png"`, the encoded data) — no `response_history` is attached; each capture is a standalone request with no prior context.
- Streams the reply via `client.messages.stream(...)` the same way `_generate_response_streaming` does, reusing `_append_response_chunk` / `_end_response_chunk_stream` for incremental UI updates.
- If the Anthropic key is missing, surfaces "Add your Anthropic key in API settings." via `_set_response_status`, matching the existing missing-key message pattern.

## Output

- Before streaming begins, insert a small tag into `response_box` (e.g. `[Screen] `) so screen-analysis entries are visually distinguishable from speech-reply entries in the shared log.
- Otherwise reuses the same box, same streaming chunk mechanics, same "Ready for the next sentence." / `Response error: ...` status messages as the existing path.

## Error handling

- Capture failure (e.g. `ImageGrab.grab()` raising) is caught in `trigger_screen_analysis` and reported via `_set_response_status` without enqueueing a job.
- API/streaming failure inside the worker is caught by the existing `try/except` wrapping job processing in `_response_worker`, unchanged.

## Out of scope

- No user-editable prompt/settings UI for the vision instruction (fixed prompt, per decision).
- No multi-monitor or region-select capture (primary monitor only, per decision).
- No use of `response_history`/conversation context for image jobs (standalone requests, per decision).
- No provider choice for screen analysis — always Claude, independent of the `response_provider` dropdown.

## Verification

- `tray_app.py` still imports/compiles successfully.
- Manual test: with privacy mode on and some other window sharing content on screen, trigger via hotkey and via button; confirm a `[Screen]`-tagged reply streams into the response box and the app's own window does not appear in the captured image.
- Manual test: trigger with no Anthropic key saved; confirm the "Add your Anthropic key..." status message appears and no request is sent.
- Manual test: trigger a screen analysis while a transcript reply is also being generated; confirm both appear in the response box without interleaving/corrupting each other's streamed text.
