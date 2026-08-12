import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class IconAssetTests(unittest.TestCase):
    def test_icon_assets_exist_and_render(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)

        root = Path(__file__).resolve().parent.parent
        png_path = root / "assets" / "screengaurd.png"
        ico_path = root / "assets" / "screengaurd.ico"

        self.assertTrue(png_path.exists(), "PNG asset should exist")
        self.assertTrue(ico_path.exists(), "ICO asset should exist")

        image = app.create_icon_image()
        self.assertEqual(image.size, (64, 64))
        self.assertIsNotNone(image.getbbox())

    def test_fallback_icon_is_used_when_assets_are_missing(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_script = Path(temp_dir) / "tray_app.py"
            fake_script.write_text("", encoding="utf-8")
            with mock.patch.object(tray_app, "__file__", str(fake_script)):
                image = app.create_icon_image()

        self.assertEqual(image.size, (64, 64))
        self.assertIsNotNone(image.getbbox())


if __name__ == "__main__":
    unittest.main()
