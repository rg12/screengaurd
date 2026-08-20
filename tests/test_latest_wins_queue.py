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
