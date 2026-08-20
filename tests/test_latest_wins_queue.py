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


if __name__ == "__main__":
    unittest.main()
