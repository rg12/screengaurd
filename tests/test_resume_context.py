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


class ResumeContextPathTests(unittest.TestCase):
    def _make_app(self, tmp_path):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        target = tmp_path / "resume_context.txt"
        app._resume_context_path = lambda: target
        return app, target

    def test_apply_loaded_resume_persists_to_disk_and_sets_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, target = self._make_app(Path(tmp))

            message = app._apply_loaded_resume("Some resume text", "resume.pdf")

            self.assertEqual(app.resume_context, "Some resume text")
            self.assertEqual(target.read_text(encoding="utf-8"), "Some resume text")
            self.assertIn("16 characters", message)
            self.assertIn("resume.pdf", message)

    def test_clear_resume_context_removes_file_and_resets_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, target = self._make_app(Path(tmp))
            target.write_text("stale", encoding="utf-8")
            app.resume_context = "stale"

            message = app._clear_resume_context()

            self.assertEqual(app.resume_context, "")
            self.assertFalse(target.exists())
            self.assertEqual(message, "No resume loaded")

    def test_clear_resume_context_is_safe_when_no_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, target = self._make_app(Path(tmp))
            app.resume_context = ""

            message = app._clear_resume_context()

            self.assertEqual(message, "No resume loaded")

    def test_load_resume_context_from_disk_reads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, target = self._make_app(Path(tmp))
            target.write_text("Persisted resume text", encoding="utf-8")

            app._load_resume_context_from_disk()

            self.assertEqual(app.resume_context, "Persisted resume text")

    def test_load_resume_context_from_disk_defaults_empty_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, target = self._make_app(Path(tmp))

            app._load_resume_context_from_disk()

            self.assertEqual(app.resume_context, "")


class BuildResponseSystemPromptTests(unittest.TestCase):
    def test_returns_base_prompt_when_no_resume(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.resume_context = ""

        self.assertEqual(app._build_response_system_prompt(), tray_app.RESPONSE_SYSTEM_PROMPT)

    def test_appends_resume_block_when_resume_loaded(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.resume_context = "Built X at Y for 3 years."

        result = app._build_response_system_prompt()

        self.assertTrue(result.startswith(tray_app.RESPONSE_SYSTEM_PROMPT))
        self.assertIn("Built X at Y for 3 years.", result)


if __name__ == "__main__":
    unittest.main()
