import importlib
import tkinter as tk
import unittest
from tkinter import ttk


class ApplyUiModeTests(unittest.TestCase):
    """Exercises the real _enter_output_only_view/_exit_output_only_view
    methods against a hand-built stand-in for the widget tree _run_tk
    creates, without going through _run_tk itself (which also registers
    global hotkeys and starts background threads — not safe to do in a
    test run)."""

    def _build_app(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.root = tk.Tk()
        app.root.geometry("780x520+100+100")
        app._expanded_geometry = "780x520"
        app.is_transcribing = False

        app._status_label = ttk.Label(app.root, text="status")
        app._status_label.pack(pady=(10, 5))

        app._opacity_frame = ttk.Frame(app.root)
        app._opacity_frame.pack(fill="x", padx=10, pady=10)

        app._speech_pane = ttk.Panedwindow(app.root, orient="horizontal")
        app._speech_pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        app._speech_frame = ttk.LabelFrame(app._speech_pane, text="Live transcript")
        app._response_frame = ttk.LabelFrame(app._speech_pane, text="Suggested English reply")
        app._speech_pane.add(app._speech_frame, weight=1)
        app._speech_pane.add(app._response_frame, weight=1)

        app._response_provider_frame = ttk.Frame(app._response_frame)
        app._response_provider_frame.pack(fill="x", padx=8, pady=(6, 2))
        app._response_status_label = ttk.Label(app._response_frame, text="status")
        app._response_status_label.pack(fill="x", padx=8, pady=(8, 4))
        app._response_toolbar_frame = ttk.Frame(app._response_frame)
        app._response_toolbar_frame.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Button(app._response_toolbar_frame, text="Clear chat").pack(side="right")
        app._analyze_screen_button = ttk.Button(app._response_toolbar_frame, text="Analyze screen")
        app._analyze_screen_button.pack(side="left")
        app.transcribe_button = ttk.Button(app._response_toolbar_frame, text="Start listening")
        app.transcribe_button.pack(side="left")
        app.response_box = tk.Text(app._response_frame, height=12)
        app.response_box.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        app.root.update_idletasks()
        return app

    def test_enter_output_only_view_hides_setup_widgets_and_repositions(self):
        app = self._build_app()
        try:
            app._enter_output_only_view()
            app.root.update_idletasks()

            self.assertFalse(app._status_label.winfo_ismapped())
            self.assertFalse(app._opacity_frame.winfo_ismapped())
            self.assertFalse(app._speech_frame.winfo_ismapped())
            self.assertFalse(app._response_provider_frame.winfo_ismapped())
            self.assertFalse(app._response_status_label.winfo_ismapped())
            self.assertTrue(app._response_toolbar_frame.winfo_ismapped())
            self.assertTrue(app.response_box.winfo_ismapped())
            self.assertTrue(app.transcribe_button.winfo_ismapped())
            self.assertTrue(app._analyze_screen_button.winfo_ismapped())

            size_part = app.root.geometry().split("+")[0]
            self.assertEqual(size_part, "420x260")
        finally:
            app.root.destroy()

    def test_exit_output_only_view_restores_setup_widgets_and_prior_geometry(self):
        app = self._build_app()
        try:
            app._enter_output_only_view()
            app.root.update_idletasks()

            app._exit_output_only_view()
            app.root.update_idletasks()

            self.assertTrue(app._status_label.winfo_ismapped())
            self.assertTrue(app._opacity_frame.winfo_ismapped())
            self.assertTrue(app._speech_frame.winfo_ismapped())
            self.assertTrue(app._response_provider_frame.winfo_ismapped())
            self.assertTrue(app._analyze_screen_button.winfo_ismapped())
            self.assertTrue(app._response_status_label.winfo_ismapped())

            size_part = app.root.geometry().split("+")[0]
            self.assertEqual(size_part, "780x520")
        finally:
            app.root.destroy()

    def test_apply_ui_mode_dispatches_on_is_transcribing(self):
        app = self._build_app()
        try:
            app.is_transcribing = True
            app._apply_ui_mode()
            app.root.update_idletasks()
            self.assertFalse(app._status_label.winfo_ismapped())

            app.is_transcribing = False
            app._apply_ui_mode()
            app.root.update_idletasks()
            self.assertTrue(app._status_label.winfo_ismapped())
        finally:
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
