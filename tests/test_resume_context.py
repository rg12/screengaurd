import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CapResumeTextTests(unittest.TestCase):
    def test_returns_text_unchanged_when_under_limit(self):
        tray_app = importlib.import_module("tray_app")
        text = "short resume"

        result = tray_app._cap_resume_text(text, max_chars=6000)

        self.assertEqual(result, text)

    def test_truncates_and_appends_note_when_over_limit(self):
        tray_app = importlib.import_module("tray_app")
        text = "x" * 100

        result = tray_app._cap_resume_text(text, max_chars=50)

        self.assertTrue(result.startswith("x" * 50))
        self.assertIn("[resume truncated — original was 100 characters]", result)


class ExtractResumeTextTests(unittest.TestCase):
    def test_reads_txt_file_directly(self):
        tray_app = importlib.import_module("tray_app")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.txt"
            path.write_text("Experienced engineer.", encoding="utf-8")

            result = tray_app._extract_resume_text(path)

        self.assertEqual(result, "Experienced engineer.")

    def test_rejects_unsupported_extension(self):
        tray_app = importlib.import_module("tray_app")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.docx"
            path.write_text("irrelevant", encoding="utf-8")

            with self.assertRaises(ValueError):
                tray_app._extract_resume_text(path)

    def test_concatenates_pdf_pages_via_pypdf(self):
        tray_app = importlib.import_module("tray_app")
        fake_page_1 = mock.Mock()
        fake_page_1.extract_text.return_value = "Page one text."
        fake_page_2 = mock.Mock()
        fake_page_2.extract_text.return_value = "Page two text."
        fake_reader = mock.Mock()
        fake_reader.pages = [fake_page_1, fake_page_2]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.pdf"
            path.write_bytes(b"%PDF-1.4 fake")

            with mock.patch.object(tray_app.pypdf, "PdfReader", return_value=fake_reader) as mock_reader:
                result = tray_app._extract_resume_text(path)

        mock_reader.assert_called_once_with(str(path))
        self.assertEqual(result, "Page one text.\nPage two text.")


if __name__ == "__main__":
    unittest.main()
