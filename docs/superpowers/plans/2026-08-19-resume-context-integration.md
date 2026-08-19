# Resume Context Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user load a resume/CV (PDF or TXT) once so that suggested transcript replies — for whichever provider (Claude/Gemini/GPT) is selected — can answer background/experience questions grounded in the user's real history.

**Architecture:** Extend the existing single-file `tray_app.py`. A "Resume" section is added to the existing API Settings dialog with Load/Clear buttons that extract text (via `pypdf` for PDF, direct read for TXT), cap it at a safety limit, persist it to `resume_context.txt` next to `tray_app.py`, and hold it in `self.resume_context`. A new `_build_response_system_prompt()` method returns `RESPONSE_SYSTEM_PROMPT` plus an appended resume block when one is loaded; the three existing reply-generation code paths (`_generate_response_streaming` for Claude, and the Gemini/GPT branches of `_generate_response`) call this instead of referencing the raw constant, so resume grounding applies regardless of the Response provider dropdown.

**Tech Stack:** Python, Tkinter (`ttk`, `tkinter.filedialog`), `pypdf` (new dependency, pure Python PDF text extraction), stdlib `pathlib`/`unittest.mock` for tests.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-19-resume-context-integration-design.md`
- Supported formats: PDF and TXT only (no DOCX) — per spec "Out of scope".
- Resume text always included in the system prompt when loaded (no keyword-based conditional inclusion) — per spec.
- Applies to whichever provider is selected in the Response provider dropdown, not Claude-only — per spec (unlike screen analysis).
- Extracted text capped at `RESUME_MAX_CHARS = 6000` characters, with a truncation note appended if longer — per spec.
- Persisted to a local file (`resume_context.txt` next to `tray_app.py`), not Windows Credential Manager/`keyring` (size limits too small) — per spec.
- No capture-exclusion handling added for the native file-picker dialog — per spec "Out of scope" (deliberate scope cut, not to be "fixed" as a bug).
- Follow existing test convention: `unittest` (not pytest), tests in `tests/`, run via `python -m unittest discover -s tests -v`. Construct app instances via `HelloWorldApp.__new__(HelloWorldApp)` to test methods without spinning up the Tk thread (see `tests/test_screen_analysis.py` for the established pattern of faking `self.root = mock.Mock()` with `root.after.side_effect = lambda delay, fn: fn()`).

---

### Task 1: Constants + pure extraction/capping helpers

**Files:**
- Modify: `tray_app.py:4` (requirements comment), `tray_app.py:19-21` (imports), `tray_app.py:83-88` (constants area, after `SCREEN_ANALYSIS_SYSTEM_PROMPT`)
- Test: `tests/test_resume_context.py` (new)

**Interfaces:**
- Produces: `RESUME_MAX_CHARS` (int constant, `6000`), module-level function `_cap_resume_text(text: str, max_chars: int = RESUME_MAX_CHARS) -> str`, module-level function `_extract_resume_text(file_path: pathlib.Path) -> str` (raises `ValueError` for unsupported extensions, propagates underlying read/parse errors otherwise).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_context.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_resume_context -v`
Expected: FAIL — `AttributeError: module 'tray_app' has no attribute '_cap_resume_text'`

- [ ] **Step 3: Add the pypdf dependency, import, and requirements line**

In `tray_app.py`, change the requirements comment (line 4) from:

```python
#     pip install pystray pillow keyboard sounddevice soundcard keyring websockets numpy anthropic openai google-genai sv_ttk
```

to:

```python
#     pip install pystray pillow keyboard sounddevice soundcard keyring websockets numpy anthropic openai google-genai sv_ttk pypdf
```

Add `filedialog` to the existing `tkinter` import (line 21) — change:

```python
from tkinter import scrolledtext, ttk
```

to:

```python
from tkinter import filedialog, scrolledtext, ttk
```

Add `import pypdf` alongside the other third-party imports, right after `import sv_ttk`:

```python
import sv_ttk  # pip install sv_ttk (modern Windows-11-style ttk theme)
import pypdf  # pip install pypdf (PDF text extraction for resume context)
```

- [ ] **Step 4: Add the constant and pure helper functions**

In `tray_app.py`, right after the `SCREEN_ANALYSIS_SYSTEM_PROMPT` constant's closing `)` (after line 88, before the blank lines leading into `def _prepare_vision_image`), add:

```python
RESUME_MAX_CHARS = 6000  # keeps one oversized resume from inflating every reply's token cost


def _cap_resume_text(text, max_chars=RESUME_MAX_CHARS):
    """Truncates text to at most max_chars, noting the original length if
    it was cut, so a huge document can't silently balloon every request."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[resume truncated — original was {len(text)} characters]"


def _extract_resume_text(file_path):
    """Reads a .pdf or .txt resume file and returns its (capped) text.
    Raises ValueError for unsupported extensions; propagates whatever
    error the underlying reader raises for a corrupt/unreadable file."""
    suffix = file_path.suffix.lower()
    if suffix == ".txt":
        text = file_path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        reader = pypdf.PdfReader(str(file_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        raise ValueError(f"Unsupported resume file type: {suffix or '(no extension)'}")
    return _cap_resume_text(text.strip())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_resume_context -v`
Expected: `OK` (4 tests)

- [ ] **Step 6: Verify the app still imports cleanly**

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 7: Commit**

```bash
git add tray_app.py tests/test_resume_context.py
git commit -m "Add pypdf dependency and pure resume text extraction/capping helpers"
```

---

### Task 2: Resume state, persistence, and system-prompt wiring

**Files:**
- Modify: `tray_app.py:200-204` (`__init__`, add `resume_context` state + load-from-disk call), `tray_app.py` near `_get_hwnd`/other path-resolving helpers (add `_resume_context_path`), `tray_app.py:824-841` (`_generate_response_streaming`), `tray_app.py:892-916` (`_generate_response`)
- Test: `tests/test_resume_context.py` (extend)

**Interfaces:**
- Consumes: `_extract_resume_text`/`_cap_resume_text` (Task 1).
- Produces: `HelloWorldApp.resume_context` (str instance attribute, `""` when none loaded). `HelloWorldApp._resume_context_path(self) -> pathlib.Path` (resolves to `resume_context.txt` next to `tray_app.py`, mirroring the existing `Path(__file__).resolve().parent` pattern used by `create_icon_image`). `HelloWorldApp._load_resume_context_from_disk(self)` (sets `self.resume_context` from the file if it exists, else `""`). `HelloWorldApp._apply_loaded_resume(self, text: str, source_name: str) -> str` (persists + sets state, returns a status message). `HelloWorldApp._clear_resume_context(self) -> str` (clears state + deletes the file if present, returns a status message). `HelloWorldApp._build_response_system_prompt(self) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_context.py` (add `from unittest import mock` is already imported above; no new imports needed):

```python
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
            self.assertIn("17 characters", message)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_resume_context -v`
Expected: FAIL — `AttributeError: 'HelloWorldApp' object has no attribute '_resume_context_path'` (and similarly for the other new methods)

- [ ] **Step 3: Add resume state to `__init__` and the path/persistence helpers**

In `tray_app.py`, in `__init__` (around line 200-204), change:

```python
        self.response_queue = queue.Queue()
        self.response_history = []
        self._protected_hwnds = set()  # combobox popdowns / dialogs that need their own capture exclusion
        self._anthropic_client = None
        self._anthropic_client_key = None
```

to:

```python
        self.response_queue = queue.Queue()
        self.response_history = []
        self._protected_hwnds = set()  # combobox popdowns / dialogs that need their own capture exclusion
        self._anthropic_client = None
        self._anthropic_client_key = None
        self.resume_context = ""
        self._load_resume_context_from_disk()
```

Add the following methods right after `_get_hwnd` (after its `return hwnd or raw` line, before `_set_capture_protection`):

```python
    def _resume_context_path(self):
        """Where the extracted resume text is cached between restarts —
        not Windows Credential Manager, whose per-secret size limits are
        too small for a full resume."""
        return Path(__file__).resolve().parent / "resume_context.txt"

    def _load_resume_context_from_disk(self):
        path = self._resume_context_path()
        if path.exists():
            try:
                self.resume_context = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                self.resume_context = ""
        else:
            self.resume_context = ""

    def _apply_loaded_resume(self, text, source_name):
        self.resume_context = text
        self._resume_context_path().write_text(text, encoding="utf-8")
        return f"Resume loaded: {len(text)} characters from {source_name}"

    def _clear_resume_context(self):
        self.resume_context = ""
        path = self._resume_context_path()
        if path.exists():
            path.unlink()
        return "No resume loaded"
```

- [ ] **Step 4: Add `_build_response_system_prompt` and wire it into both reply paths**

Add this method right after `_build_conversation` (after its `return conversation` line, before `_generate_response_streaming`):

```python
    def _build_response_system_prompt(self):
        if not self.resume_context:
            return RESPONSE_SYSTEM_PROMPT
        return (
            f"{RESPONSE_SYSTEM_PROMPT}\n\n"
            "The user's resume/CV is below. Draw on it naturally, in first person "
            "(e.g. \"In my last role I...\"), only when the other person's question "
            "is about the user's background, experience, or skills (e.g. \"tell me "
            "about yourself\", \"what have you worked on\"). Never invent anything "
            "not present in it, and don't force it into replies where it isn't "
            "relevant.\n\n"
            f"Resume:\n{self.resume_context}"
        )
```

In `_generate_response_streaming`, change:

```python
            system=RESPONSE_SYSTEM_PROMPT,
```

to:

```python
            system=self._build_response_system_prompt(),
```

In `_generate_response`, change:

```python
        prompt = f"{RESPONSE_SYSTEM_PROMPT}\n\nRecent conversation:\n{conversation_text}"
```

to:

```python
        prompt = f"{self._build_response_system_prompt()}\n\nRecent conversation:\n{conversation_text}"
```

and change:

```python
            instructions=RESPONSE_SYSTEM_PROMPT,
```

to:

```python
            instructions=self._build_response_system_prompt(),
```

(Leave `_generate_screen_analysis`/`SCREEN_ANALYSIS_SYSTEM_PROMPT` untouched — screen analysis is unrelated per the spec.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_resume_context -v`
Expected: `OK` (11 tests)

- [ ] **Step 6: Run the full test suite and verify compilation**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests across all test files pass)

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 7: Commit**

```bash
git add tray_app.py tests/test_resume_context.py
git commit -m "Add resume context state, persistence, and system-prompt wiring"
```

---

### Task 3: Wire the three reply-generation call sites' provider coverage + Settings dialog UI

**Files:**
- Modify: `tray_app.py:1030-1031` area (`_open_settings_dialog`, insert Resume section between the API-keys `form` and the shared `status_var` label)
- Test: `tests/test_resume_context.py` (extend, for the provider-coverage wiring only — the Tk file-dialog glue is exercised manually, consistent with how `_open_settings_dialog`'s other UI glue is untested elsewhere in this codebase)

**Interfaces:**
- Consumes: `_build_response_system_prompt` (Task 2), `_apply_loaded_resume`/`_clear_resume_context` (Task 2), `_extract_resume_text` (Task 1).
- Produces: `HelloWorldApp._load_resume_file(self, resume_status_var: tk.StringVar)` (opens a file picker, extracts text, calls `_apply_loaded_resume`, updates `resume_status_var`; on extraction failure, sets an inline error instead). `HelloWorldApp._clear_resume_file(self, resume_status_var: tk.StringVar)` (calls `_clear_resume_context`, updates `resume_status_var`).

- [ ] **Step 1: Write the failing tests (provider-coverage wiring)**

Append to `tests/test_resume_context.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_resume_context -v`
Expected: FAIL only if Task 2's wiring is somehow incomplete — since Task 2 already wired `_build_response_system_prompt()` into all three call sites, these should actually **pass already**. Run them to confirm coverage; if any fails, it means Task 2's Step 4 edit was missed for that call site — go back and apply it.

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m unittest tests.test_resume_context -v`
Expected: `OK` (14 tests)

- [ ] **Step 4: Add the Resume section to the API Settings dialog**

In `tray_app.py`, in `_open_settings_dialog`, insert a new section between the API-keys `form` block and the shared key-validation `status_var` label. Change:

```python
        ttk.Label(form, text="Gemini API key").grid(row=3, column=0, sticky="w", pady=5)
        gemini_var = tk.StringVar()
        ttk.Entry(form, textvariable=gemini_var, show="*", width=38).grid(row=3, column=1, padx=(10, 0), pady=5)

        status_var = tk.StringVar(value="Leave a field blank to keep its saved key unchanged.")
```

to:

```python
        ttk.Label(form, text="Gemini API key").grid(row=3, column=0, sticky="w", pady=5)
        gemini_var = tk.StringVar()
        ttk.Entry(form, textvariable=gemini_var, show="*", width=38).grid(row=3, column=1, padx=(10, 0), pady=5)

        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=15, pady=(12, 8))

        resume_frame = ttk.Frame(dialog)
        resume_frame.pack(fill="x", padx=15)
        resume_status_var = tk.StringVar(
            value=(
                f"Resume loaded: {len(self.resume_context)} characters"
                if self.resume_context
                else "No resume loaded"
            )
        )
        ttk.Label(
            resume_frame, textvariable=resume_status_var, foreground=MUTED_TEXT_COLOR, wraplength=390, justify="left"
        ).pack(anchor="w")
        resume_buttons_frame = ttk.Frame(resume_frame)
        resume_buttons_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(
            resume_buttons_frame,
            text="Load resume...",
            command=lambda: self._load_resume_file(resume_status_var),
        ).pack(side="left")
        ttk.Button(
            resume_buttons_frame,
            text="Clear resume",
            command=lambda: self._clear_resume_file(resume_status_var),
        ).pack(side="left", padx=(6, 0))

        status_var = tk.StringVar(value="Leave a field blank to keep its saved key unchanged.")
```

- [ ] **Step 5: Add the `_load_resume_file`/`_clear_resume_file` glue methods**

Add these right after `_clear_resume_context` (Task 2's last method in that group):

```python
    def _load_resume_file(self, resume_status_var):
        file_path = filedialog.askopenfilename(
            title="Select resume",
            filetypes=[("Resume files", "*.pdf *.txt")],
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            text = _extract_resume_text(path)
        except Exception as error:
            resume_status_var.set(f"Couldn't read resume: {error}")
            return
        resume_status_var.set(self._apply_loaded_resume(text, path.name))

    def _clear_resume_file(self, resume_status_var):
        resume_status_var.set(self._clear_resume_context())
```

- [ ] **Step 6: Run the full test suite and verify compilation**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests across all test files pass)

Run: `python -m py_compile tray_app.py`
Expected: no output, exit code 0

- [ ] **Step 7: Manual end-to-end verification**

Run: `pythonw tray_app.py`, open API Settings:
1. Confirm the "Resume" section shows "No resume loaded" initially (unless a `resume_context.txt` already exists from prior testing — delete it first if so).
2. Click "Load resume...", pick a real PDF resume. Confirm the status line updates to a plausible character count and filename, and that `resume_context.txt` now exists with readable text.
3. Start live transcription (or otherwise get a transcript through the pipeline) and say/simulate something like "tell me about yourself" — confirm the suggested reply (Claude, the default provider) references real resume content instead of generic text.
4. Switch the Response provider dropdown to Gemini, repeat step 3, confirm resume-grounded replies still work. Repeat for GPT.
5. Click "Clear resume", confirm the status line reverts to "No resume loaded", `resume_context.txt` is deleted, and a subsequent reply no longer references resume content.
6. Load a resume again, fully quit and relaunch the app (`pythonw tray_app.py`), open API Settings, confirm the resume is still shown as loaded (persistence).
7. Try loading a corrupt/invalid PDF (e.g. a renamed `.txt` file with `.pdf` extension containing non-PDF bytes) and confirm an inline "Couldn't read resume: ..." error appears instead of a crash.

- [ ] **Step 8: Commit**

```bash
git add tray_app.py tests/test_resume_context.py
git commit -m "Add resume load/clear UI to API Settings dialog"
```
