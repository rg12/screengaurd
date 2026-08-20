# Latest-Wins Reply Queue

## Goal

Fix the queueing-staleness problem measured in this session's latency testing: in a rapid back-and-forth Q&A, questions queue up faster than the app can answer them, so later questions' suggested replies arrive stale (in the tested simulation, a question asked at 0.8s didn't get its answer until 2.7s — 1.9s of pure queueing delay on top of normal ~0.9-1.4s LLM latency). The app should always be answering "what was just asked," not working through a backlog.

## Context: current pipeline

`tray_app.py` has a single `self.response_queue` (`queue.Queue`) fed by two producers:
- `_add_transcript` pushes each finalized transcript string onto it.
- `_capture_and_queue_screen` pushes `{"type": "image", "data": <base64>}` dicts onto it (screen-analysis jobs, triggered by the "Analyze screen" button/`ctrl+alt+s`).

A single background thread, `_response_worker`, pulls jobs off this queue one at a time (`job = self.response_queue.get()`, blocking), branches on job shape, and either runs the transcript→reply path (Claude/Gemini/GPT, respecting `self.response_provider`) or `_handle_screen_analysis_job` (always Claude). Because it's one queue and one worker, a screen-analysis click can get stuck behind pending transcript replies and vice versa, and any number of rapid-fire transcripts all get processed in full FIFO order regardless of how stale they've become by the time their turn comes up.

## Decisions (confirmed via brainstorming)

- **Latest wins**: when a new job arrives while an older one is still waiting (not yet started), the older one is dropped. Only the most recently asked question (or most recent screen-analysis trigger) gets answered.
- **Screen analysis gets its own lane**: it must never be dropped or delayed by transcript activity, and vice versa — a deliberate "Analyze screen" click is a distinct, always-wanted action, unlike passively-arriving transcript questions.
- **In-flight replies are not cancelled**: a reply that's already being generated (mid-network-call/mid-stream) finishes normally. Only jobs still waiting in the queue (not yet started) are subject to being dropped.

## Architecture

Two independent single-slot "latest wins" queues instead of one shared FIFO:

1. **`self.response_queue`** (existing, kept) — now carries transcript strings only.
2. **`self.screen_analysis_queue`** (new `queue.Queue`) — carries the `{"type": "image", "data": ...}` jobs, now entirely separate from transcript replies.

A new helper, `_enqueue_latest(self, q, job)`, drains any items currently sitting in a queue (via non-blocking `get_nowait()` in a loop until `queue.Empty`) before putting the new job in. Both producers call this instead of a plain `.put(...)`:
- `_add_transcript` calls `self._enqueue_latest(self.response_queue, transcript)` instead of `self.response_queue.put(transcript)`.
- `_capture_and_queue_screen` calls `self._enqueue_latest(self.screen_analysis_queue, {"type": "image", "data": image_b64})` instead of `self.response_queue.put(...)`.

Because a worker thread only ever has at most one item in flight (already dequeued, currently being processed) and at most one item waiting, draining-then-putting guarantees that whenever the worker next calls `.get()`, it receives the most recently enqueued job — anything dropped in between was simply overwritten before the worker ever saw it. This requires no changes to the worker loop's blocking-`get()` shape at all.

`_response_worker` reverts to handling transcript strings only — the `isinstance(job, dict) and job.get("type") == "image"` branch and its dispatch to `_handle_screen_analysis_job` move into a new, symmetrical `_screen_analysis_worker` method that loops on `self.screen_analysis_queue` instead. Both worker threads are started the same way, alongside each other, where `_response_worker`'s thread is currently started (in `_run_tk`).

## Data flow after the change

- Interviewer asks 3 questions rapidly → `_add_transcript` fires 3 times → `_enqueue_latest` drops questions 1 and 2 from the queue before the worker ever picks them up (assuming the worker hadn't already started on question 1 — if it had, question 1 finishes per "in-flight is not cancelled," then the worker immediately answers question 3, skipping question 2 entirely).
- User clicks "Analyze screen" while a transcript reply is mid-generation → the screen-analysis job goes to its own queue/worker and is answered independently, without waiting on the transcript reply.
- Rapidly clicking "Analyze screen" multiple times before the first capture's reply comes back → same latest-wins behavior applies within that lane; only the last click's capture gets analyzed.

## Error handling

No change to existing per-job error handling (missing API key, empty reply, exceptions during generation) — those are unaffected by which queue a job came from. `_enqueue_latest`'s drain loop only ever raises/catches `queue.Empty`, which is expected control flow, not an error.

## Testing

- `_enqueue_latest` is testable directly and in isolation: put several items on a plain `queue.Queue()` via `_enqueue_latest`, assert only the last one is ever retrievable.
- A worker-level regression test (mirroring the latency simulation used to find this bug, but against real code): mock the network-calling methods to sleep a fixed short duration, fire several transcripts in rapid succession through the real `_response_worker`, and assert stale ones never produce output — only the latest.
- A test confirming `_capture_and_queue_screen` and `_add_transcript` write to their own separate queues (`screen_analysis_queue` vs `response_queue`), so a screen-analysis job is never observed on the transcript queue or vice versa.

## Out of scope

- No UI change — this is purely pipeline behavior; the output box, tagging (`[Screen] `), and toolbar are untouched.
- No cancellation of in-flight requests (per decision).
- No retry/backoff on transient API failures, no streaming for Gemini/GPT, no visible "thinking" indicator in the collapsed view — these were separate findings from the same latency-testing session and are tracked as independent follow-ups, not part of this change.
