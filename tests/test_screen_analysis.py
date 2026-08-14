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


if __name__ == "__main__":
    unittest.main()
