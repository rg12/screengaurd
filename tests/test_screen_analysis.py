import base64
import importlib
import io
import queue
import unittest
from unittest import mock

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


class ClearChatTests(unittest.TestCase):
    def test_clear_chat_empties_boxes_and_resets_history(self):
        import tkinter as tk
        from tkinter import scrolledtext

        tray_app = importlib.import_module("tray_app")
        root = tk.Tk()
        try:
            app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
            app.transcript_box = scrolledtext.ScrolledText(root)
            app.response_box = scrolledtext.ScrolledText(root)
            for box in (app.transcript_box, app.response_box):
                box.configure(state="normal")
                box.insert("end", "some previous chat content\n")
                box.configure(state="disabled")
            app.response_history = [{"role": "user", "content": "hi"}]

            app.clear_chat()

            self.assertEqual(app.transcript_box.get("1.0", "end").strip(), "")
            self.assertEqual(app.response_box.get("1.0", "end").strip(), "")
            self.assertEqual(app.response_history, [])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
