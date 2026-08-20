# Visible Status in Collapsed View

## Goal

Show reply-generation status ("Generating a concise reply...", "Analyzing screen...", "Ready for the next sentence.", "Response error: ...") in the collapsed output-only view, so there's a visible signal of "thinking" vs "stuck" vs "failed" during actual use — the label currently only shows in the setup view, which the user isn't looking at while actively listening.

## Context

`tray_app.py`'s `_response_status_label` (bound to `self.response_status_var`) already receives the right text at the right times via existing `_set_response_status` calls in `_response_worker`, `_handle_screen_analysis_job`, and `_capture_and_queue_screen`. The only problem is visibility: `_enter_output_only_view` calls `self._response_status_label.pack_forget()`, hiding it exactly when the user is in the state where they'd want to see it. `_exit_output_only_view` already re-shows it when returning to the setup view.

Privacy-mode capture exclusion (`SetWindowDisplayAffinity`) is applied per-window (the whole HWND), not per-widget, so this label is automatically hidden from screen-share exactly like the rest of the window already is whenever privacy mode is on — no separate capture-exclusion work is needed here.

## Change

In `_enter_output_only_view`, remove the `self._response_status_label.pack_forget()` line. Nothing else changes: no new widgets, no new state, no changes to `_set_response_status`'s callers, no changes to `_exit_output_only_view` (it already packs the label back — that becomes a no-op re-pack when the label was never hidden in the first place, which is harmless).

The output box's available vertical space shrinks slightly to make room for the status line, matching how it already looks in the full setup view.

## Testing

`tests/test_ui_mode.py`'s `test_enter_output_only_view_hides_setup_widgets_and_repositions` currently asserts `_response_status_label` is unmapped after collapsing — that assertion flips to asserting it stays mapped. No other test changes needed.

## Out of scope

- No new indicator styling (spinner, color change, icon) — reuses the existing plain-text label as-is.
- No change to what text appears or when — purely a visibility fix.
