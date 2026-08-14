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


if __name__ == "__main__":
    unittest.main()
