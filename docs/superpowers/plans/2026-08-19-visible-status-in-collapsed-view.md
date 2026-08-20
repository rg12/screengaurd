# Visible Status in Collapsed View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show reply-generation status ("Generating a concise reply...", "Analyzing screen...", "Ready for the next sentence.", "Response error: ...") in the collapsed output-only view instead of only in the setup view.

**Architecture:** Remove the one line in `_enter_output_only_view` that hides `_response_status_label`. Nothing else changes — the label already receives the right text at the right times via existing `_set_response_status` calls, and `_exit_output_only_view` already re-shows it.

**Tech Stack:** No new dependencies — a one-line Tkinter change plus a one-line test flip.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-19-visible-status-in-collapsed-view-design.md`
- No new widgets, no new state, no change to what text appears or when — purely a visibility fix.
- Privacy-mode capture exclusion is window-level, not per-widget, so no capture-exclusion work is needed for this change.
- Follow existing test convention: `unittest`, tests in `tests/`, run via `python -m unittest discover -s tests -v`.

---

### Task 1: Stop hiding the status label in the collapsed view

**Files:**
- Modify: `tray_app.py:788` (`_enter_output_only_view`)
- Test: `tests/test_ui_mode.py:62` (flip an existing assertion)

**Interfaces:**
- Consumes: existing `_response_status_label` attribute (already built in `_run_tk`, already exercised by the test's `_build_app` stand-in widget tree — no changes needed there).

- [ ] **Step 1: Update the test to assert the new (correct) behavior**

In `tests/test_ui_mode.py`, in `test_enter_output_only_view_hides_setup_widgets_and_repositions`, change:

```python
            self.assertFalse(app._response_status_label.winfo_ismapped())
```

to:

```python
            self.assertTrue(app._response_status_label.winfo_ismapped())
```

- [ ] **Step 2: Run the test to verify it fails against current code**

Run: `python -m unittest tests.test_ui_mode -v`
Expected: FAIL — `test_enter_output_only_view_hides_setup_widgets_and_repositions` fails because `_response_status_label` is still unmapped (current code still hides it)

- [ ] **Step 3: Remove the `pack_forget()` call**

In `tray_app.py`, in `_enter_output_only_view`, change:

```python
        self._status_label.pack_forget()
        self._opacity_frame.pack_forget()
        self._speech_pane.forget(self._speech_frame)
        self._response_provider_frame.pack_forget()
        self._response_status_label.pack_forget()
```

to:

```python
        self._status_label.pack_forget()
        self._opacity_frame.pack_forget()
        self._speech_pane.forget(self._speech_frame)
        self._response_provider_frame.pack_forget()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_ui_mode -v`
Expected: `OK` (3 tests)

- [ ] **Step 5: Run the full test suite and verify compilation**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests across all test files pass)

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 6: Manual verification**

Run: `pythonw tray_app.py`. Start listening, confirm the status line ("Connecting to Deepgram...", "Waiting for a finalized sentence...", etc.) is visible in the collapsed top-center view, not just in the full setup view. Trigger a failure (e.g. temporarily clear the Anthropic key and say something) and confirm the error text shows up there too instead of going nowhere.

- [ ] **Step 7: Commit**

```bash
git add tray_app.py tests/test_ui_mode.py
git commit -m "Keep reply status visible in the collapsed output-only view"
```
