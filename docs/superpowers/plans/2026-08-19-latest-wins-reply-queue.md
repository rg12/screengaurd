# Latest-Wins Reply Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop suggested replies from arriving stale during rapid back-and-forth Q&A by making the transcript-reply and screen-analysis pipelines each answer only the most recently requested thing, and by giving screen analysis its own lane so it's never delayed by pending transcript replies.

**Architecture:** Replace the single shared `response_queue` (currently fed by both transcripts and screen-analysis jobs) with two independent queues, each drained-before-put so only one item is ever waiting per queue — `response_queue` (transcripts) and a new `screen_analysis_queue` (image jobs), each with its own worker thread. In-flight replies (already dequeued, currently generating) are left alone; only not-yet-started queued items get dropped.

**Tech Stack:** Python stdlib `queue.Queue`, `threading` — no new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-19-latest-wins-reply-queue-design.md`
- Latest wins: a newly enqueued job drops any not-yet-started job still waiting in the same queue.
- In-flight jobs (already dequeued by a worker) are never cancelled/interrupted — they finish normally.
- Screen analysis and transcript replies must be fully independent lanes — neither can block or drop the other.
- No UI changes — output box, `[Screen] ` tagging, and the toolbar are untouched.
- Follow existing test convention: `unittest`, tests in `tests/`, run via `python -m unittest discover -s tests -v`. Construct app instances via `HelloWorldApp.__new__(HelloWorldApp)`; fake `self.root = mock.Mock()` with `root.after.side_effect = lambda delay, fn: fn()` for methods that touch Tk state (see `tests/test_screen_analysis.py`).

---

### Task 1: `_enqueue_latest` helper

**Files:**
- Modify: `tray_app.py` (add method, near `_add_transcript`)
- Test: `tests/test_latest_wins_queue.py` (new)

**Interfaces:**
- Produces: `HelloWorldApp._enqueue_latest(self, q: queue.Queue, job) -> None` — drains `q` of any not-yet-started items, then puts `job`. Does not affect an item a worker has already dequeued (that item is no longer in `q`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_latest_wins_queue.py`:

```python
import importlib
import queue
import unittest


class EnqueueLatestTests(unittest.TestCase):
    def test_only_the_last_item_is_retrievable(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        q = queue.Queue()

        app._enqueue_latest(q, "first")
        app._enqueue_latest(q, "second")
        app._enqueue_latest(q, "third")

        self.assertEqual(q.get_nowait(), "third")
        self.assertTrue(q.empty())

    def test_works_normally_when_queue_starts_empty(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        q = queue.Queue()

        app._enqueue_latest(q, "only")

        self.assertEqual(q.get_nowait(), "only")

    def test_does_not_touch_an_item_already_taken_by_a_worker(self):
        """An item pulled off the queue via .get() (i.e. already 'in
        flight') is no longer in the queue, so _enqueue_latest must not
        affect it — only items still waiting are subject to being
        dropped."""
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        q = queue.Queue()
        q.put("in-flight")
        in_flight = q.get_nowait()  # simulate a worker having already taken this one

        app._enqueue_latest(q, "new")

        self.assertEqual(in_flight, "in-flight")
        self.assertEqual(q.get_nowait(), "new")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_latest_wins_queue -v`
Expected: FAIL with `AttributeError: 'HelloWorldApp' object has no attribute '_enqueue_latest'`

- [ ] **Step 3: Add `_enqueue_latest`**

In `tray_app.py`, add this method right before `_add_transcript`:

```python
    def _enqueue_latest(self, q, job):
        """Drops any not-yet-started items before adding the new one, so a
        worker pulling from this queue always answers the most recently
        enqueued job instead of working through a stale backlog. Only
        affects items still waiting — anything a worker has already
        dequeued (in flight) is untouched."""
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        q.put(job)

    def _add_transcript(self, transcript):
```

(That last line is the existing method signature shown for placement context — don't duplicate it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_latest_wins_queue -v`
Expected: `OK` (3 tests)

- [ ] **Step 5: Verify the app still imports cleanly**

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 6: Commit**

```bash
git add tray_app.py tests/test_latest_wins_queue.py
git commit -m "Add _enqueue_latest helper for drop-stale-jobs queueing"
```

---

### Task 2: Split into separate transcript/screen-analysis queues and workers

**Files:**
- Modify: `tray_app.py:225` (`__init__`, add `screen_analysis_queue`), `tray_app.py:419` (start the new worker thread), `tray_app.py:501` (`_capture_and_queue_screen`, enqueue onto the new queue via `_enqueue_latest`), `tray_app.py:826` (`_add_transcript`, use `_enqueue_latest`), `tray_app.py:828-838` (`_response_worker`, remove the image-job branch), add new `_screen_analysis_worker` method (near `_handle_screen_analysis_job`)
- Test: `tests/test_latest_wins_queue.py` (extend)

**Interfaces:**
- Consumes: `_enqueue_latest` (Task 1), `_handle_screen_analysis_job` (existing, unchanged signature `self, image_b64`).
- Produces: `HelloWorldApp.screen_analysis_queue` (new `queue.Queue` instance attribute, carries `{"type": "image", "data": <base64 str>}` jobs). `HelloWorldApp._screen_analysis_worker(self)` — loops on `screen_analysis_queue`, calling `_handle_screen_analysis_job(job["data"])` per item, returns on a `None` sentinel (mirrors `_response_worker`'s existing shutdown shape). `_response_worker` reverts to only ever receiving transcript strings from `response_queue` (no more dict jobs).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_latest_wins_queue.py` (add these imports at the top: `import threading`, `import time`, `from unittest import mock`):

```python
import threading
import time
from unittest import mock


class SeparateQueuesTests(unittest.TestCase):
    def test_screen_analysis_goes_to_its_own_queue_not_response_queue(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.response_queue = queue.Queue()
        app.screen_analysis_queue = queue.Queue()
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, fn: fn()
        app._set_response_status = lambda msg: None
        fake_screenshot = tray_app.Image.new("RGB", (4, 4), color="red")

        with mock.patch.object(tray_app.ImageGrab, "grab", return_value=fake_screenshot):
            app.trigger_screen_analysis()

        self.assertTrue(app.response_queue.empty())
        job = app.screen_analysis_queue.get_nowait()
        self.assertEqual(job["type"], "image")

    def test_add_transcript_goes_to_response_queue_not_screen_analysis_queue(self):
        import tkinter as tk
        from tkinter import scrolledtext

        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.response_queue = queue.Queue()
        app.screen_analysis_queue = queue.Queue()
        root = tk.Tk()
        try:
            app.transcript_box = scrolledtext.ScrolledText(root)

            app._add_transcript("hello")

            self.assertEqual(app.response_queue.get_nowait(), "hello")
            self.assertTrue(app.screen_analysis_queue.empty())
        finally:
            root.destroy()


class ScreenAnalysisWorkerTests(unittest.TestCase):
    def test_dispatches_jobs_to_handler_and_stops_on_none(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.screen_analysis_queue = queue.Queue()
        handled = []
        app._handle_screen_analysis_job = handled.append
        app.screen_analysis_queue.put({"type": "image", "data": "abc"})
        app.screen_analysis_queue.put(None)

        app._screen_analysis_worker()

        self.assertEqual(handled, ["abc"])


class ResponseWorkerLatestWinsTests(unittest.TestCase):
    def test_stale_transcripts_are_dropped_under_rapid_fire(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.response_queue = queue.Queue()
        app.response_provider = "Claude"
        app.response_history = []
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, fn: fn()
        app._set_response_status = lambda message: None
        processed = []

        def slow_reply(api_key, transcript):
            time.sleep(0.2)
            processed.append(transcript)
            return f"reply to {transcript}"

        app._generate_response_streaming = slow_reply

        with mock.patch.object(tray_app.keyring, "get_password", return_value="key"):
            worker = threading.Thread(target=app._response_worker, daemon=True)
            worker.start()

            app._enqueue_latest(app.response_queue, "question 1")
            time.sleep(0.05)  # let the worker pick up question 1 and become busy
            app._enqueue_latest(app.response_queue, "question 2")
            app._enqueue_latest(app.response_queue, "question 3")

            time.sleep(0.6)  # let question 1 finish, then question 3 process
            app.response_queue.put(None)
            worker.join(timeout=2)

        self.assertEqual(processed, ["question 1", "question 3"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_latest_wins_queue -v`
Expected: FAIL — `AttributeError: 'HelloWorldApp' object has no attribute 'screen_analysis_queue'` (and similar for `_screen_analysis_worker`)

- [ ] **Step 3: Add `screen_analysis_queue` to `__init__`**

In `tray_app.py`, in `__init__`, change:

```python
        self.response_queue = queue.Queue()
        self.response_history = []
```

to:

```python
        self.response_queue = queue.Queue()
        self.screen_analysis_queue = queue.Queue()
        self.response_history = []
```

- [ ] **Step 4: Route `_capture_and_queue_screen` to the new queue via `_enqueue_latest`**

In `tray_app.py`, in `_capture_and_queue_screen`, change:

```python
        self.response_queue.put({"type": "image", "data": image_b64})
        self._set_response_status("Analyzing screen...")
```

to:

```python
        self._enqueue_latest(self.screen_analysis_queue, {"type": "image", "data": image_b64})
        self._set_response_status("Analyzing screen...")
```

- [ ] **Step 5: Route `_add_transcript` through `_enqueue_latest`**

In `tray_app.py`, in `_add_transcript`, change:

```python
        self.response_queue.put(transcript)
```

to:

```python
        self._enqueue_latest(self.response_queue, transcript)
```

- [ ] **Step 6: Remove the image-job branch from `_response_worker` and add `_screen_analysis_worker`**

In `tray_app.py`, change `_response_worker` from:

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

to:

```python
    def _response_worker(self):
        """Turn each finalized utterance into a concise English suggested reply."""
        while True:
            transcript = self.response_queue.get()
            if transcript is None:
                return

            provider = self.response_provider
```

Add `_screen_analysis_worker` right after `_handle_screen_analysis_job` (after its closing `except Exception as error:` line's body, before `_generate_response`):

```python
    def _screen_analysis_worker(self):
        """Mirrors _response_worker's shape but for screen-analysis jobs,
        on its own queue/thread so it's never delayed by (or delays)
        pending transcript replies."""
        while True:
            job = self.screen_analysis_queue.get()
            if job is None:
                return
            self._handle_screen_analysis_job(job["data"])
```

- [ ] **Step 7: Start the new worker thread**

In `tray_app.py`, change:

```python
        self._start_hotkey_listener()
        threading.Thread(target=self._response_worker, daemon=True).start()
        self.root.mainloop()
```

to:

```python
        self._start_hotkey_listener()
        threading.Thread(target=self._response_worker, daemon=True).start()
        threading.Thread(target=self._screen_analysis_worker, daemon=True).start()
        self.root.mainloop()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m unittest tests.test_latest_wins_queue -v`
Expected: `OK` (6 tests)

- [ ] **Step 9: Run the full test suite and verify compilation**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests across all test files pass — note `tests/test_screen_analysis.py`'s `ResponseWorkerDispatchTests.test_dispatches_image_job_to_handler_and_stops_on_none` tests the *old* image-in-`_response_worker` behavior this task removes; if it fails, delete that test — its coverage is superseded by this task's `ScreenAnalysisWorkerTests`)

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 10: Manual verification**

Run: `pythonw tray_app.py`. With an Anthropic key saved:
1. Start listening, say/simulate two or three things in quick succession, confirm the app doesn't visibly lag further and further behind — each new reply reflects roughly what was just said, not a growing backlog.
2. While a transcript reply is generating, click "Analyze screen" — confirm it doesn't wait for the transcript reply to finish first.
3. Click "Analyze screen" twice in quick succession — confirm only one (the latest) analysis result appears, not two.

- [ ] **Step 11: Commit**

```bash
git add tray_app.py tests/test_latest_wins_queue.py tests/test_screen_analysis.py
git commit -m "Split reply pipeline into latest-wins transcript and screen-analysis queues"
```
