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
        self.last_kwargs = kwargs
        return FakeStream(self._chunks)


class FakeAnthropicClient:
    def __init__(self, chunks):
        self.messages = FakeMessages(chunks)


class ReplyPathsIncludeResumeTests(unittest.TestCase):
    def _make_app(self):
        tray_app = importlib.import_module("tray_app")
        app = tray_app.HelloWorldApp.__new__(tray_app.HelloWorldApp)
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, fn: fn()
        app.resume_context = "Resume snippet."
        app.response_history = []
        app._append_response_chunk = lambda chunk: None
        app._end_response_chunk_stream = lambda: None
        return tray_app, app

    def test_generate_response_streaming_claude_includes_resume(self):
        tray_app, app = self._make_app()
        app._anthropic_client = FakeAnthropicClient(["ok"])
        app._anthropic_client_key = "test-key"

        app._generate_response_streaming("test-key", "Tell me about yourself")

        self.assertIn("Resume snippet.", app._anthropic_client.messages.last_kwargs["system"])

    def test_generate_response_gemini_includes_resume(self):
        tray_app, app = self._make_app()
        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = mock.Mock(text="ok")
        with mock.patch.object(tray_app.genai, "Client", return_value=fake_client):
            app._generate_response("Gemini", "test-key", "Tell me about yourself")

        _, kwargs = fake_client.models.generate_content.call_args
        self.assertIn("Resume snippet.", kwargs["contents"])

    def test_generate_response_gpt_includes_resume(self):
        tray_app, app = self._make_app()
        fake_responses = mock.Mock()
        fake_responses.create.return_value = mock.Mock(output_text="ok")
        fake_client = mock.Mock(responses=fake_responses)
        with mock.patch.object(tray_app, "OpenAI", return_value=fake_client):
            app._generate_response("GPT", "test-key", "Tell me about yourself")

        _, kwargs = fake_responses.create.call_args
        self.assertIn("Resume snippet.", kwargs["instructions"])


if __name__ == "__main__":
    unittest.main()
