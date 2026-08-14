# Screen Capture Vision Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user capture the primary monitor and have Claude vision analyze it (describe what's on screen / answer a visible question), streamed into the existing response box, while the app's own window stays excluded from the capture via the existing privacy-mode affinity.

**Architecture:** Extend the existing single-file `tray_app.py` app. A hotkey and a button both call `trigger_screen_analysis`, which captures + downscales + base64-encodes the screenshot on the Tk main thread (fast, <100ms) and enqueues an `{"type": "image", "data": ...}` job onto the existing `response_queue`. The existing background `_response_worker` thread — which currently only ever pulls transcript strings off that queue — is extended to branch on job shape: strings go through the existing transcript→reply path unchanged, image jobs go through a new `_handle_screen_analysis_job` → `_generate_screen_analysis` path that always uses Claude (regardless of the `response_provider` dropdown) and streams into the same `response_box`, tagged `[Screen] `.

**Tech Stack:** Python, Tkinter, Pillow (`PIL.ImageGrab`, already a dependency), `anthropic` SDK (already a dependency), Python stdlib `base64`/`io`. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-screen-capture-vision-analysis-design.md`
- Primary monitor only, full screen — no region select, no multi-monitor (per spec "Out of scope").
- Always Claude for screen analysis, independent of the `response_provider` dropdown (per spec).
- No `response_history`/conversation context attached to screen-analysis requests — standalone per capture (per spec).
- Fixed system prompt only — no user-editable prompt UI (per spec "Out of scope").
- Downscale images so the long edge is at most 1568px (Anthropic's documented recommendation), never upscale.
- Follow existing test convention: `unittest` (not pytest — not installed in this environment), tests live in `tests/`, run via `python -m unittest tests.<module> -v`. Construct app instances via `HelloWorldApp.__new__(HelloWorldApp)` to test methods without going through `__init__`'s Tk thread spin-up (see `tests/test_icon_assets.py` for the established pattern).

---

### Task 1: Constants + pure image-prep helper

**Files:**
- Modify: `tray_app.py:22` (imports), `tray_app.py:61` (hotkey constants), `tray_app.py:68-73` (prompt constants area)
- Test: `tests/test_screen_analysis.py` (new)

**Interfaces:**
- Produces: `SCREEN_ANALYSIS_HOTKEY` (str constant, `"ctrl+alt+s"`), `SCREEN_ANALYSIS_MAX_EDGE` (int constant, `1568`), `SCREEN_ANALYSIS_SYSTEM_PROMPT` (str constant), module-level function `_prepare_vision_image(image: PIL.Image.Image, max_edge: int = SCREEN_ANALYSIS_MAX_EDGE) -> str` returning a base64-encoded PNG string.

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_analysis.py`:

```python
import base64
import importlib
import io
import unittest

from PIL import Image


class PrepareVisionImageTests(unittest.TestCase):
    def test_downscales_large_image_preserving_aspect_ratio(self):
        tray_app = importlib.import_module("tray_app")
        image = Image.new("RGB", (3840, 2160), color="blue")

        encoded = tray_app._prepare_vision_image(image, max_edge=1568)

        decoded = base64.b64decode(encoded)
        result = Image.open(io.BytesIO(decoded))
        self.assertEqual(result.format, "PNG")
        self.assertEqual(result.size, (1568, 882))

    def test_does_not_upscale_small_image(self):
        tray_app = importlib.import_module("tray_app")
        image = Image.new("RGB", (400, 300), color="green")

        encoded = tray_app._prepare_vision_image(image, max_edge=1568)

        decoded = base64.b64decode(encoded)
        result = Image.open(io.BytesIO(decoded))
        self.assertEqual(result.size, (400, 300))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_screen_analysis -v`
Expected: FAIL with `AttributeError: module 'tray_app' has no attribute '_prepare_vision_image'`

- [ ] **Step 3: Add the import, constants, and helper function**

In `tray_app.py`, add `base64` to the stdlib imports (line 9-17 block, alongside `io`):

```python
import threading
import ctypes
import asyncio
import base64
import io
```

Change the PIL import on line 22 from:

```python
from PIL import Image, ImageDraw
```

to:

```python
from PIL import Image, ImageDraw, ImageGrab
```

Add a new hotkey constant next to the other hotkeys (after `SPEECH_TO_TEXT_HOTKEY = "ctrl+alt+m"` on line 61):

```python
SCREEN_ANALYSIS_HOTKEY = "ctrl+alt+s"
```

Add two new constants right after `RESPONSE_SYSTEM_PROMPT`'s closing `)` (after line 73):

```python
SCREEN_ANALYSIS_MAX_EDGE = 1568  # Anthropic's documented recommended max long edge for image inputs
SCREEN_ANALYSIS_SYSTEM_PROMPT = (
    "You are looking at a screenshot of the user's screen. Describe what's on screen "
    "and, if there is a visible question or problem, answer it concisely."
)
```

Add the pure helper function at module level, right after these new constants (before `class HelloWorldApp:`):

```python
def _prepare_vision_image(image, max_edge=SCREEN_ANALYSIS_MAX_EDGE):
    """Downscale (never upscale) so the long edge is at most max_edge, then
    encode as base64 PNG. Keeps screen-analysis requests cheap and fast
    without losing legibility for typical UI/text content."""
    width, height = image.size
    longest = max(width, height)
    if longest > max_edge:
        scale = max_edge / longest
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_screen_analysis -v`
Expected: `OK` (2 tests)

- [ ] **Step 5: Verify the app still imports cleanly**

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 6: Commit**

```bash
git add tray_app.py tests/test_screen_analysis.py
git commit -m "Add screen-analysis constants and image downscale/encode helper"
```

---

### Task 2: Capture trigger (hotkey + button wiring, queues the image job)

**Files:**
- Modify: `tray_app.py:264-272` (add button in `response_frame`, next to the response-provider combobox), `tray_app.py:312` (hotkey registration in `_start_hotkey_listener`), `tray_app.py:322` area (add new methods near `_set_capture_protection`/`toggle_privacy_mode`-style methods)
- Test: `tests/test_screen_analysis.py` (extend)

**Interfaces:**
- Consumes: `_prepare_vision_image` (Task 1), `self.response_queue` (existing `queue.Queue`, currently only fed transcript strings), `self._set_response_status` (existing method, `tray_app.py:722`).
- Produces: `HelloWorldApp.trigger_screen_analysis(self, icon=None, item=None)` — entry point safe to call from the hotkey thread, a Tk button command, or the pystray menu; marshals onto the Tk thread. `HelloWorldApp._capture_and_queue_screen(self)` — does the actual capture/encode/enqueue on the Tk main thread. On success, puts `{"type": "image", "data": <base64 str>}` onto `self.response_queue`. On failure, calls `self._set_response_status(f"Screen capture error: {error}")` and does not enqueue anything.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_analysis.py` (add these imports at the top alongside the existing ones: `import queue`, `from unittest import mock`):

```python
import queue
from unittest import mock


class TriggerScreenAnalysisTests(unittest.TestCase):
    def _make_app(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.response_queue = queue.Queue()
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, fn: fn()
        return tray_app, app

    def test_capture_and_queue_screen_puts_image_job(self):
        tray_app, app = self._make_app()
        statuses = []
        app._set_response_status = statuses.append
        fake_screenshot = Image.new("RGB", (10, 10), color="red")

        with mock.patch.object(tray_app.ImageGrab, "grab", return_value=fake_screenshot):
            app.trigger_screen_analysis()

        job = app.response_queue.get_nowait()
        self.assertEqual(job["type"], "image")
        decoded = base64.b64decode(job["data"])
        self.assertTrue(decoded.startswith(b"\x89PNG"))
        self.assertIn("Analyzing screen", statuses[-1])

    def test_capture_failure_sets_status_and_does_not_queue(self):
        tray_app, app = self._make_app()
        statuses = []
        app._set_response_status = statuses.append

        with mock.patch.object(tray_app.ImageGrab, "grab", side_effect=RuntimeError("boom")):
            app.trigger_screen_analysis()

        self.assertTrue(app.response_queue.empty())
        self.assertIn("Screen capture error", statuses[-1])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_screen_analysis -v`
Expected: FAIL — `AttributeError: 'HelloWorldApp' object has no attribute 'trigger_screen_analysis'`

- [ ] **Step 3: Implement `trigger_screen_analysis` and `_capture_and_queue_screen`**

In `tray_app.py`, add these two methods right after `_set_capture_protection` (after line 331, before `_hwnd_from_widget`):

```python
    def trigger_screen_analysis(self, icon=None, item=None):
        """Entry point for the hotkey, the button, and (potentially) the tray
        menu — marshals onto the Tk thread before touching Tk/Win32 state,
        matching the pattern used by toggle_privacy_mode."""
        self.root.after(0, self._capture_and_queue_screen)

    def _capture_and_queue_screen(self):
        """Capture + downscale + encode happens synchronously here since it's
        fast (<100ms); the slow network call happens later on the existing
        response-worker thread once the job comes off the queue."""
        try:
            screenshot = ImageGrab.grab()
            image_b64 = _prepare_vision_image(screenshot)
        except Exception as error:
            self._set_response_status(f"Screen capture error: {error}")
            return
        self.response_queue.put({"type": "image", "data": image_b64})
        self._set_response_status("Analyzing screen...")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_screen_analysis -v`
Expected: `OK` (4 tests)

- [ ] **Step 5: Wire up the hotkey**

In `tray_app.py`, in `_start_hotkey_listener` (around line 382-389), add a line alongside the other `keyboard.add_hotkey(...)` calls:

```python
    def _start_hotkey_listener(self):
        # Runs in the background; lets you toggle click-through even when
        # the window doesn't have focus (e.g. you're clicked into another app).
        keyboard.add_hotkey(CLICK_THROUGH_HOTKEY, self.toggle_click_through)
        keyboard.add_hotkey(SHOW_HIDE_HOTKEY, self.toggle_window)
        keyboard.add_hotkey(TASKBAR_TOGGLE_HOTKEY, self.toggle_taskbar_setting)
        keyboard.add_hotkey(PRIVACY_HOTKEY, self.toggle_privacy_mode)
        keyboard.add_hotkey(SPEECH_TO_TEXT_HOTKEY, self.start_speech_to_text)
        keyboard.add_hotkey(SCREEN_ANALYSIS_HOTKEY, self.trigger_screen_analysis)
```

- [ ] **Step 6: Add the "Analyze screen" button**

In `tray_app.py`, right after the response-provider combobox block ends (after `self._protect_combobox_popdown(response_provider_combo)` on line 296, before the `tk.Label(response_frame, textvariable=self.response_status_var, ...)` block), add:

```python
        tk.Button(response_frame, text="Analyze screen", command=self.trigger_screen_analysis).pack(
            padx=8, pady=(0, 4)
        )
```

- [ ] **Step 7: Update the status bar's hotkey hint text**

The status label built in `_run_tk` (line 191-193) lists the show/hide and click-through hotkeys. Extend it so the screen-analysis hotkey is discoverable too:

```python
        self.status_var = tk.StringVar(
            value=(
                f"Interactive  (show/hide: {SHOW_HIDE_HOTKEY}; click-through: {CLICK_THROUGH_HOTKEY}; "
                f"analyze screen: {SCREEN_ANALYSIS_HOTKEY})"
            )
        )
```

Note: `_apply_click_through` (line 359-373) also sets `self.status_var` when toggling click-through — leave that one alone, it's a transient state message, not the persistent hint text.

- [ ] **Step 8: Manually smoke-test the wiring**

Run: `pythonw tray_app.py`, then press `ctrl+alt+s` and separately click "Analyze screen". Confirm the response status line shows "Analyzing screen..." each time (the job will sit in the queue until Task 3 adds a consumer for it — that's expected at this point).

- [ ] **Step 9: Run the full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests, including `test_icon_assets.py`, still pass)

- [ ] **Step 10: Commit**

```bash
git add tray_app.py tests/test_screen_analysis.py
git commit -m "Wire up screen-analysis hotkey and button to capture and queue a job"
```

---

### Task 3: Claude vision request + response-worker dispatch + streamed, tagged output

**Files:**
- Modify: `tray_app.py:614-635` (`_response_worker`, add job-type branch), `tray_app.py` near `_generate_response_streaming` (`tray_app.py:677-694`) (add sibling method)
- Test: `tests/test_screen_analysis.py` (extend)

**Interfaces:**
- Consumes: `self.response_queue` jobs of shape `{"type": "image", "data": <base64 str>}` (Task 2) or a plain `str` (existing transcript behavior, unchanged); `self._get_anthropic_client(api_key)` (existing, `tray_app.py:659`); `self._append_response_chunk` / `self._end_response_chunk_stream` / `self._set_response_status` (existing).
- Produces: `HelloWorldApp._handle_screen_analysis_job(self, image_b64: str)` — looks up the Anthropic key, tags the response box with `"[Screen] "`, calls `_generate_screen_analysis`, reports status/errors. `HelloWorldApp._generate_screen_analysis(self, api_key: str, image_b64: str) -> str` — streams a Claude vision reply and returns the full text, mirroring `_generate_response_streaming`'s shape but with no `response_history` attached.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_analysis.py`:

```python
class FakeStream:
    def __init__(self, chunks):
        self.text_stream = iter(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeMessages:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, **kwargs):
        return FakeStream(self._chunks)


class FakeAnthropicClient:
    def __init__(self, chunks):
        self.messages = FakeMessages(chunks)


class GenerateScreenAnalysisTests(unittest.TestCase):
    def test_streams_and_returns_full_reply(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, fn: fn()
        app._anthropic_client = FakeAnthropicClient(["Hello", ", ", "world."])
        app._anthropic_client_key = "test-key"
        chunks = []
        app._append_response_chunk = chunks.append
        app._end_response_chunk_stream = lambda: chunks.append("<end>")

        reply = app._generate_screen_analysis("test-key", base64.b64encode(b"data").decode("ascii"))

        self.assertEqual(reply, "Hello, world.")
        self.assertEqual(chunks, ["Hello", ", ", "world.", "<end>"])


class HandleScreenAnalysisJobTests(unittest.TestCase):
    def _make_app(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, fn: fn()
        return tray_app, app

    def test_missing_key_sets_status_and_does_not_call_generate(self):
        tray_app, app = self._make_app()
        statuses = []
        app._set_response_status = statuses.append
        app._generate_screen_analysis = mock.Mock()

        with mock.patch.object(tray_app.keyring, "get_password", return_value=None):
            app._handle_screen_analysis_job("irrelevant")

        app._generate_screen_analysis.assert_not_called()
        self.assertIn("Anthropic key", statuses[-1])

    def test_success_tags_response_and_reports_ready_status(self):
        tray_app, app = self._make_app()
        statuses = []
        chunks = []
        app._set_response_status = statuses.append
        app._append_response_chunk = chunks.append
        app._generate_screen_analysis = mock.Mock(return_value="It's a login form.")

        with mock.patch.object(tray_app.keyring, "get_password", return_value="test-key"):
            app._handle_screen_analysis_job("base64data")

        self.assertEqual(chunks[0], "[Screen] ")
        app._generate_screen_analysis.assert_called_once_with("test-key", "base64data")
        self.assertIn("Ready for the next sentence", statuses[-1])


class ResponseWorkerDispatchTests(unittest.TestCase):
    def test_dispatches_image_job_to_handler_and_stops_on_none(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.response_queue = queue.Queue()
        handled = []
        app._handle_screen_analysis_job = handled.append
        app.response_queue.put({"type": "image", "data": "abc"})
        app.response_queue.put(None)

        app._response_worker()

        self.assertEqual(handled, ["abc"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_screen_analysis -v`
Expected: FAIL — `AttributeError: 'HelloWorldApp' object has no attribute '_generate_screen_analysis'` (and similar for the other new methods)

- [ ] **Step 3: Add `_generate_screen_analysis` and `_handle_screen_analysis_job`**

In `tray_app.py`, add these two methods right after `_generate_response_streaming` (after line 694, before `_generate_response`):

```python
    def _generate_screen_analysis(self, api_key, image_b64):
        """Standalone vision request (no response_history attached) — always
        Claude, streamed the same way _generate_response_streaming is."""
        client = self._get_anthropic_client(api_key)
        text_parts = []
        with client.messages.stream(
            model="claude-sonnet-5",
            max_tokens=400,
            system=SCREEN_ANALYSIS_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        }
                    ],
                }
            ],
        ) as stream:
            for delta in stream.text_stream:
                text_parts.append(delta)
                self.root.after(0, lambda chunk=delta: self._append_response_chunk(chunk))
        reply = "".join(text_parts).strip()
        self.root.after(0, self._end_response_chunk_stream)
        return reply

    def _handle_screen_analysis_job(self, image_b64):
        api_key = keyring.get_password(CREDENTIAL_SERVICE, ANTHROPIC_KEY_NAME)
        if not api_key:
            self.root.after(
                0, lambda: self._set_response_status("Add your Anthropic key in API settings.")
            )
            return

        try:
            self.root.after(0, lambda: self._append_response_chunk("[Screen] "))
            reply = self._generate_screen_analysis(api_key, image_b64)
            if not reply:
                raise RuntimeError("Claude returned an empty response.")
            self.root.after(0, lambda: self._set_response_status("Ready for the next sentence."))
        except Exception as error:
            self.root.after(0, lambda message=str(error): self._set_response_status(f"Response error: {message}"))
```

- [ ] **Step 4: Branch `_response_worker` on job shape**

In `tray_app.py`, modify `_response_worker` (lines 614-635) from:

```python
    def _response_worker(self):
        """Turn each finalized utterance into a concise English suggested reply."""
        while True:
            transcript = self.response_queue.get()
            if transcript is None:
                return

            provider = self.response_provider
```

to:

```python
    def _response_worker(self):
        """Turn each finalized utterance into a concise English suggested
        reply, or (for image jobs) a screen analysis."""
        while True:
            job = self.response_queue.get()
            if job is None:
                return

            if isinstance(job, dict) and job.get("type") == "image":
                self._handle_screen_analysis_job(job["data"])
                continue

            transcript = job
            provider = self.response_provider
```

The rest of `_response_worker` (the `key_name = {...}` lookup onward, lines 622-657) is unchanged — it already only runs for the `transcript` (string) path.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_screen_analysis -v`
Expected: `OK` (9 tests)

- [ ] **Step 6: Run the full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests pass, including `test_icon_assets.py`)

- [ ] **Step 7: Verify the app still imports cleanly**

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 8: Manual end-to-end verification**

Run: `pythonw tray_app.py`. With privacy mode on (default) and an Anthropic key saved in API Settings:
1. Have some content on your primary monitor (e.g. a code editor or a question in a doc).
2. Press `ctrl+alt+s` (or click "Analyze screen"). Confirm a `[Screen] ...` reply streams into the response box.
3. Start a screen-share/recording of your own screen (or just note that privacy mode already excludes the window per the earlier privacy-mode work) and confirm the app's own window doesn't appear in what gets captured.
4. Remove the saved Anthropic key, trigger again, confirm the status line shows "Add your Anthropic key in API settings." and no request is sent.
5. Start speech-to-text and, while a transcript reply is streaming, also trigger a screen analysis; confirm both replies appear fully and legibly in the response box without interleaving/corrupting each other's text (the shared worker thread processes them one at a time).

- [ ] **Step 9: Commit**

```bash
git add tray_app.py tests/test_screen_analysis.py
git commit -m "Add Claude vision screen-analysis request and response-worker dispatch"
```
