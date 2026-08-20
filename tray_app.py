# Background tray overlay with click-through, capture exclusion, and speech to text.
#
# Requirements:
#     pip install pystray pillow keyboard sounddevice soundcard keyring websockets numpy anthropic openai google-genai sv_ttk pypdf
#
# Run:
#     pythonw tray_app.py

import threading
import ctypes
import asyncio
import base64
import io
import json
import contextlib
import queue
import time
import warnings
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk
from urllib.parse import urlencode
from PIL import Image, ImageDraw, ImageGrab
import pystray
import keyboard  # pip install keyboard (used for the global hotkey listener)
import numpy as np
import sounddevice as sd
import soundcard as sc  # WASAPI loopback capture (sounddevice/PortAudio has no loopback support)
import pythoncom  # COM init required per-thread for soundcard's WASAPI calls
import keyring
import websockets
import wave
from openai import OpenAI
from google import genai
import anthropic
import sv_ttk  # pip install sv_ttk (modern Windows-11-style ttk theme)
import pypdf  # pip install pypdf (PDF text extraction for resume context)

# --- Light modern theme palette (Sun Valley "light") ---
APP_BG = "#fafafa"
CARD_BG = "#ffffff"
TEXT_COLOR = "#1a1a1a"
MUTED_TEXT_COLOR = "#636363"
BORDER_COLOR = "#e0e0e0"

# --- Win32 constants for capture exclusion and click-through ---
WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
GA_ROOT = 2
SW_RESTORE = 9
ERROR_ALREADY_EXISTS = 183

WINDOW_TITLE = "College Demo App"
SINGLE_INSTANCE_MUTEX = "CollegeDemoApp_SingleInstance"

CLICK_THROUGH_HOTKEY = "ctrl+alt+x"
SHOW_HIDE_HOTKEY = "ctrl+alt+h"
TASKBAR_TOGGLE_HOTKEY = "ctrl+alt+t"
PRIVACY_HOTKEY = "ctrl+alt+p"
SPEECH_TO_TEXT_HOTKEY = "ctrl+alt+m"
SCREEN_ANALYSIS_HOTKEY = "ctrl+alt+s"
SPEECH_SAMPLE_RATE = 16_000
CREDENTIAL_SERVICE = "College Demo App"
DEEPGRAM_KEY_NAME = "deepgram_api_key"
ANTHROPIC_KEY_NAME = "anthropic_api_key"
OPENAI_KEY_NAME = "openai_api_key"
GEMINI_KEY_NAME = "gemini_api_key"
RESPONSE_SYSTEM_PROMPT = (
    "You are a real-time conversation assistant. Preserve the intended meaning, silently "
    "translate non-English input into English, and propose a polite, useful reply. "
    "Never claim the user said something that is not in the transcript. Reply in clear, "
    "natural English with only one or two concise sentences and no preamble."
)
SCREEN_ANALYSIS_MAX_EDGE = 1568  # Anthropic's documented recommended max long edge for image inputs
SCREEN_ANALYSIS_SYSTEM_PROMPT = (
    "You are looking at a screenshot of the user's screen. Describe what's on screen "
    "and, if there is a visible question or problem, answer it concisely."
)
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


def _prepare_vision_image(image, max_edge=SCREEN_ANALYSIS_MAX_EDGE):
    """Downscale (never upscale) so the long edge is at most max_edge, then
    encode as base64 PNG. Keeps screen-analysis requests cheap and fast
    without losing legibility for typical UI/text content."""
    width, height = image.size
    longest = max(width, height)
    if longest > max_edge:
        scale = max_edge / longest
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@contextlib.contextmanager
def _com_initialized():
    """soundcard reaches WASAPI through COM, and COM has to be initialized on
    every thread that touches it — only the main thread gets that for free."""
    try:
        pythoncom.CoInitialize()
    except pythoncom.com_error:
        yield  # already initialized on this thread under another apartment model
        return
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _acquire_single_instance_lock():
    """Claim a named mutex for this app. Returns the handle, or None when another
    instance already owns it — each extra copy would otherwise add its own tray
    icon and fight over the same global hotkeys."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def _focus_existing_instance():
    """Bring the already-running copy's window to the front instead of starting a
    second one, so clicking the launcher again behaves the way people expect."""
    hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    return True


class _LoopbackAudioStream:
    """Captures WASAPI loopback audio (whatever is currently playing, e.g. the
    other person's voice on a call) via the `soundcard` library. sounddevice's
    WasapiSettings has no `loopback` option, so it can only ever record the
    microphone, not system playback."""

    def __init__(self, audio_chunks, samplerate, channels, blocksize=4096):
        self._audio_chunks = audio_chunks
        self._samplerate = samplerate
        self._channels = channels
        self._blocksize = blocksize
        self._stop_event = threading.Event()
        self._thread = None

    def __enter__(self):
        with _com_initialized():
            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default playback device found for system audio capture.")
            mic = sc.get_microphone(id=speaker.id, include_loopback=True)
        self._thread = threading.Thread(target=self._capture_loop, args=(mic,), daemon=True)
        self._thread.start()
        return self

    def _capture_loop(self, mic):
        with _com_initialized(), warnings.catch_warnings():
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            with mic.recorder(
                samplerate=self._samplerate, channels=self._channels, blocksize=self._blocksize
            ) as recorder:
                while not self._stop_event.is_set():
                    data = recorder.record(numframes=self._blocksize)
                    pcm16 = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                    try:
                        self._audio_chunks.put_nowait(pcm16)
                    except queue.Full:
                        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)


class HelloWorldApp:
    def __init__(self):
        self.window = None
        self.visible = False
        self.icon = None
        self.show_in_taskbar = False  # keep the visible overlay out of the taskbar by default
        self.privacy_mode = True  # hidden from screen capture while remaining visible locally
        self.click_through = False  # when True, mouse clicks pass through to whatever's behind
        self.is_transcribing = False
        self.audio_source = "System audio (online call)"
        self.transcription_provider = "Deepgram"
        self.response_provider = "Claude"
        self.stop_transcription_event = None
        self.response_queue = queue.Queue()
        self.screen_analysis_queue = queue.Queue()
        self.response_history = []
        self._protected_hwnds = set()  # combobox popdowns / dialogs that need their own capture exclusion
        self._anthropic_client = None
        self._anthropic_client_key = None
        self.resume_context = ""
        self._load_resume_context_from_disk()

        # tkinter has to be created and driven from its own thread's mainloop,
        # so we build the (initially hidden) window on the tkinter thread.
        self.tk_thread = threading.Thread(target=self._run_tk, daemon=True)
        self.tk_thread.start()

    # ---------- tkinter side ----------

    def _run_tk(self):
        self.root = tk.Tk()
        self.root.title("College Demo App")
        self.root.geometry("780x520")
        self.root.configure(bg=APP_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)  # X button hides, doesn't quit
        self._apply_window_icon()
        self._expanded_geometry = "780x520"

        sv_ttk.set_theme("light")

        self.status_var = tk.StringVar(
            value=(
                f"Interactive  (show/hide: {SHOW_HIDE_HOTKEY}; click-through: {CLICK_THROUGH_HOTKEY}; "
                f"analyze screen: {SCREEN_ANALYSIS_HOTKEY})"
            )
        )
        self._status_label = ttk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Segoe UI", 8),
            foreground=MUTED_TEXT_COLOR,
            wraplength=740,
            justify="center",
        )
        self._status_label.pack(pady=(10, 5))

        # Opacity slider: 100 = fully opaque, lower = more see-through
        self.opacity_var = tk.IntVar(value=100)
        self._opacity_frame = ttk.Frame(self.root)
        self._opacity_frame.pack(fill="x", padx=10, pady=10)

        ttk.Label(self._opacity_frame, text="Opacity").pack(side="left")
        slider = ttk.Scale(
            self._opacity_frame,
            from_=20,        # don't allow it to go fully invisible (min 20%)
            to=100,
            orient="horizontal",
            variable=self.opacity_var,
            command=self._on_opacity_change,
        )
        slider.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self._speech_pane = ttk.Panedwindow(self.root, orient="horizontal")
        self._speech_pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._speech_frame = ttk.LabelFrame(self._speech_pane, text="Live transcript")
        self._response_frame = ttk.LabelFrame(self._speech_pane, text="Suggested English reply")
        speech_frame = self._speech_frame
        response_frame = self._response_frame
        self._speech_pane.add(speech_frame, weight=1)
        self._speech_pane.add(response_frame, weight=1)

        self.speech_status_var = tk.StringVar(
            value=f"Choose an audio source, then press {SPEECH_TO_TEXT_HOTKEY} or Start listening."
        )
        ttk.Label(speech_frame, textvariable=self.speech_status_var, anchor="w", justify="left", wraplength=340).pack(
            fill="x", padx=8, pady=(6, 2)
        )

        source_frame = ttk.Frame(speech_frame)
        source_frame.pack(fill="x", padx=8, pady=(2, 4))
        ttk.Label(source_frame, text="Audio source").pack(side="left")
        self.audio_source_var = tk.StringVar(value=self.audio_source)
        self.audio_source_var.trace_add("write", self._on_audio_source_changed)
        audio_source_combo = ttk.Combobox(
            source_frame,
            textvariable=self.audio_source_var,
            values=("System audio (online call)", "Microphone"),
            state="readonly",
            width=25,
        )
        audio_source_combo.pack(side="right")
        self._protect_combobox_popdown(audio_source_combo)

        transcription_frame = ttk.Frame(speech_frame)
        transcription_frame.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(transcription_frame, text="Transcription").pack(side="left")
        self.transcription_provider_var = tk.StringVar(value=self.transcription_provider)
        self.transcription_provider_var.trace_add("write", self._on_transcription_provider_changed)
        transcription_combo = ttk.Combobox(
            transcription_frame,
            textvariable=self.transcription_provider_var,
            values=("Deepgram", "GPT (5-second chunks)"),
            state="readonly",
            width=25,
        )
        transcription_combo.pack(side="right")
        self._protect_combobox_popdown(transcription_combo)

        ttk.Button(speech_frame, text="API settings", command=self._open_settings_dialog).pack(padx=8, pady=(0, 4))

        self.live_caption_var = tk.StringVar(value="Live caption will appear here.")
        ttk.Label(speech_frame, textvariable=self.live_caption_var, anchor="w", justify="left", wraplength=375).pack(
            fill="x", padx=8, pady=(2, 4)
        )

        self.transcript_box = scrolledtext.ScrolledText(
            speech_frame,
            height=7,
            wrap="word",
            state="disabled",
            bg=CARD_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            font=("Segoe UI", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        self.transcript_box.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        self.response_status_var = tk.StringVar(value="Waiting for a finalized sentence...")
        self._response_provider_frame = ttk.Frame(response_frame)
        self._response_provider_frame.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(self._response_provider_frame, text="Response provider").pack(side="left")
        self.response_provider_var = tk.StringVar(value=self.response_provider)
        self.response_provider_var.trace_add("write", self._on_response_provider_changed)
        response_provider_combo = ttk.Combobox(
            self._response_provider_frame,
            textvariable=self.response_provider_var,
            values=("Claude", "Gemini", "GPT"),
            state="readonly",
            width=20,
        )
        response_provider_combo.pack(side="right")
        self._protect_combobox_popdown(response_provider_combo)
        self._response_status_label = ttk.Label(
            response_frame,
            textvariable=self.response_status_var,
            anchor="w",
            justify="left",
            wraplength=340,
        )
        self._response_status_label.pack(fill="x", padx=8, pady=(8, 4))

        # Always visible, in both setup and output-only view — sits directly
        # above the output box so Start/Stop, Analyze screen, and Clear chat
        # all stay reachable when everything else is collapsed away.
        self._response_toolbar_frame = ttk.Frame(response_frame)
        self._response_toolbar_frame.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Button(self._response_toolbar_frame, text="Clear chat", command=self.clear_chat).pack(side="right")
        self._analyze_screen_button = ttk.Button(
            self._response_toolbar_frame, text="Analyze screen", command=self.trigger_screen_analysis
        )
        self._analyze_screen_button.pack(side="left")
        self.transcribe_button = ttk.Button(
            self._response_toolbar_frame,
            text="Start listening",
            command=self.start_speech_to_text,
            style="Accent.TButton",
        )
        self.transcribe_button.pack(side="left", padx=(0, 6))

        self.response_box = scrolledtext.ScrolledText(
            response_frame,
            height=12,
            wrap="word",
            state="disabled",
            bg=CARD_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            font=("Segoe UI", 11),
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        self.response_box.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        # Start visible so launching the app actually shows something; the tray
        # icon and the show/hide hotkey still hide it again afterwards.
        self._set_capture_protection(False)
        self._show_window()

        self._start_hotkey_listener()
        threading.Thread(target=self._response_worker, daemon=True).start()
        threading.Thread(target=self._screen_analysis_worker, daemon=True).start()
        self.root.mainloop()

    def _get_hwnd(self):
        """Resolve the actual top-level window handle for the tk root."""
        raw = self.root.winfo_id()
        hwnd = ctypes.windll.user32.GetAncestor(raw, GA_ROOT)
        return hwnd or raw

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

    def _set_capture_protection(self, enabled=True, hwnd=None):
        """Excludes a window from screen capture/sharing APIs. Defaults to the
        main window, but accepts other top-level HWNDs (dropdown popdowns,
        dialogs) since display affinity is per-window, not inherited."""
        try:
            target_hwnd = hwnd if hwnd is not None else self._get_hwnd()
            affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
            ctypes.windll.user32.SetWindowDisplayAffinity(target_hwnd, affinity)
        except Exception:
            pass

    def trigger_screen_analysis(self, icon=None, item=None):
        """Entry point for the hotkey, the button, and (potentially) the tray
        menu — marshals onto the Tk thread before touching Tk/Win32 state,
        matching the pattern used by toggle_privacy_mode."""
        self.root.after(0, self._capture_and_queue_screen)

    def _capture_and_queue_screen(self):
        """Capture + downscale + encode happens synchronously here since it's
        fast (<100ms); the slow network call happens later on the existing
        response-worker thread once the job comes off the queue."""
        try:
            screenshot = ImageGrab.grab()
            image_b64 = _prepare_vision_image(screenshot)
        except Exception as error:
            self._set_response_status(f"Screen capture error: {error}")
            return
        self._enqueue_latest(self.screen_analysis_queue, {"type": "image", "data": image_b64})
        self._set_response_status("Analyzing screen...")

    def _hwnd_from_widget(self, widget):
        raw = widget.winfo_id()
        hwnd = ctypes.windll.user32.GetAncestor(raw, GA_ROOT)
        return hwnd or raw

    def _protect_extra_window(self, hwnd):
        """Track a non-main top-level window (dialog, combobox popdown) so
        privacy mode toggles keep it in sync with the main window."""
        self._protected_hwnds.add(hwnd)
        self._set_capture_protection(self.privacy_mode, hwnd=hwnd)

    def _protect_combobox_popdown(self, combobox):
        """A ttk.Combobox's dropdown list renders in its own top-level Tk
        window (not a child of the main window), so it needs its own capture
        exclusion or it shows up in a screen share even with privacy mode on."""
        def _apply():
            try:
                popdown = combobox.tk.call("ttk::combobox::PopdownWindow", combobox)
                raw_id = int(str(combobox.tk.call("winfo", "id", popdown)), 0)
                hwnd = ctypes.windll.user32.GetAncestor(raw_id, GA_ROOT) or raw_id
                self._protect_extra_window(hwnd)
            except Exception:
                pass

        self.root.after(0, _apply)

    def _apply_click_through(self):
        hwnd = self._get_hwnd()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if self.click_through:
            style |= (WS_EX_LAYERED | WS_EX_TRANSPARENT)
            # Keep the visible demo over the application receiving the clicks.
            self.root.attributes("-topmost", True)
            self.status_var.set(f"Click-through (background is interactive) — {CLICK_THROUGH_HOTKEY}")
        else:
            style &= ~WS_EX_TRANSPARENT
            self.root.attributes("-topmost", False)
            self.status_var.set(
                f"Interactive  (show/hide: {SHOW_HIDE_HOTKEY}; click-through: {CLICK_THROUGH_HOTKEY})"
            )
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def toggle_click_through(self):
        # Only meaningful while the window is actually shown.
        if not self.visible:
            return
        self.click_through = not self.click_through
        self.root.after(0, self._apply_click_through)

    def _start_hotkey_listener(self):
        # Runs in the background; lets you toggle click-through even when
        # the window doesn't have focus (e.g. you're clicked into another app).
        keyboard.add_hotkey(CLICK_THROUGH_HOTKEY, self.toggle_click_through)
        keyboard.add_hotkey(SHOW_HIDE_HOTKEY, self.toggle_window)
        keyboard.add_hotkey(TASKBAR_TOGGLE_HOTKEY, self.toggle_taskbar_setting)
        keyboard.add_hotkey(PRIVACY_HOTKEY, self.toggle_privacy_mode)
        keyboard.add_hotkey(SPEECH_TO_TEXT_HOTKEY, self.start_speech_to_text)
        keyboard.add_hotkey(SCREEN_ANALYSIS_HOTKEY, self.trigger_screen_analysis)

    def _on_opacity_change(self, value):
        self.root.attributes("-alpha", int(float(value)) / 100)

    def _on_audio_source_changed(self, *_):
        self.audio_source = self.audio_source_var.get()

    def _on_transcription_provider_changed(self, *_):
        self.transcription_provider = self.transcription_provider_var.get()

    def _on_response_provider_changed(self, *_):
        self.response_provider = self.response_provider_var.get()

    def start_speech_to_text(self):
        """Start or stop Deepgram live transcription."""
        if self.is_transcribing:
            self.stop_speech_to_text()
            return

        provider = self.transcription_provider
        key_name = DEEPGRAM_KEY_NAME if provider == "Deepgram" else OPENAI_KEY_NAME
        api_key = keyring.get_password(CREDENTIAL_SERVICE, key_name)
        if not api_key:
            self.root.after(
                0,
                lambda name=provider: self._set_speech_status(
                    f"Add your {name} key in API settings first."
                ),
            )
            return

        self.is_transcribing = True
        self.stop_transcription_event = threading.Event()
        audio_chunks = queue.Queue(maxsize=64)
        # Explicit "enter" rather than a live re-check of is_transcribing:
        # if the connection fails fast (e.g. the audio device is still busy
        # releasing from a just-stopped session), the background thread can
        # flip is_transcribing back to False before this callback even runs,
        # which would make a state-based check resolve to "exit" and never
        # visibly collapse at all.
        self.root.after(0, self._enter_output_only_view)
        self.root.after(0, lambda: self._set_speech_status(f"Connecting to {provider}..."))
        self.root.after(0, lambda: self.live_caption_var.set("Waiting for speech..."))
        target = self._run_live_transcription if provider == "Deepgram" else self._run_openai_transcription
        threading.Thread(
            target=target,
            args=(api_key, self.audio_source, self.stop_transcription_event, audio_chunks),
            daemon=True,
        ).start()

    def stop_speech_to_text(self):
        if self.stop_transcription_event:
            self.stop_transcription_event.set()
            self.root.after(0, lambda: self._set_speech_status("Stopping live transcription..."))

    def _run_live_transcription(self, deepgram_key, audio_source, stop_event, audio_chunks):
        failure_message = None
        try:
            stream, sample_rate, channels = self._create_audio_stream(audio_source, audio_chunks)
            with stream:
                asyncio.run(
                    self._stream_to_deepgram(
                        deepgram_key, sample_rate, channels, stop_event, audio_chunks
                    )
                )
        except Exception as error:
            failure_message = f"Live transcription error: {error}"
        finally:
            self.is_transcribing = False
            self.root.after(0, lambda: self._finish_transcription(failure_message))

    def _run_openai_transcription(self, openai_key, audio_source, stop_event, audio_chunks):
        failure_message = None
        try:
            stream, sample_rate, channels = self._create_audio_stream(audio_source, audio_chunks)
            client = OpenAI(api_key=openai_key)
            with stream:
                self.root.after(0, lambda: self._set_speech_status("Listening with GPT (5-second chunks)..."))
                while not stop_event.is_set():
                    chunks = []
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and not stop_event.is_set():
                        try:
                            chunks.append(audio_chunks.get(timeout=0.25))
                        except queue.Empty:
                            continue
                    if not chunks or stop_event.is_set():
                        continue

                    self.root.after(0, lambda: self._set_speech_status("Transcribing the latest GPT chunk..."))
                    wav_data = self._pcm_to_wav(b"".join(chunks), sample_rate, channels)
                    result = client.audio.transcriptions.create(
                        model="gpt-4o-mini-transcribe",
                        file=("conversation.wav", wav_data, "audio/wav"),
                    )
                    transcript = (result.text or "").strip()
                    if transcript:
                        self.root.after(0, lambda text=transcript: self._display_caption(text, True))
        except Exception as error:
            failure_message = f"GPT transcription error: {error}"
        finally:
            self.is_transcribing = False
            self.root.after(0, lambda: self._finish_transcription(failure_message))

    @staticmethod
    def _pcm_to_wav(pcm_data, sample_rate, channels):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return buffer.getvalue()

    def _create_audio_stream(self, audio_source, audio_chunks):
        def callback(indata, frames, time, status):
            if status:
                return
            try:
                audio_chunks.put_nowait(bytes(indata))
            except queue.Full:
                pass

        if audio_source == "System audio (online call)":
            with _com_initialized():
                speaker = sc.default_speaker()
                if speaker is None:
                    raise RuntimeError("No default playback device was found for system audio capture.")
                channels = min(2, int(speaker.channels))
            if channels < 1:
                raise RuntimeError("Your default playback device has no audio channels.")
            sample_rate = 48_000
            stream = _LoopbackAudioStream(audio_chunks, sample_rate, channels)
            return stream, sample_rate, channels

        input_device = sd.default.device[0]
        stream = sd.RawInputStream(
            device=input_device,
            samplerate=SPEECH_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            callback=callback,
        )
        return stream, SPEECH_SAMPLE_RATE, 1

    async def _stream_to_deepgram(self, deepgram_key, sample_rate, channels, stop_event, audio_chunks):
        query = urlencode(
            {
                "model": "nova-3",
                "language": "multi",
                "encoding": "linear16",
                "sample_rate": sample_rate,
                "channels": channels,
                "interim_results": "true",
                "smart_format": "true",
                "punctuate": "true",
                "diarize": "true",
                "endpointing": 300,
                "utterance_end_ms": 1000,
            }
        )
        url = f"wss://api.deepgram.com/v1/listen?{query}"
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {deepgram_key}"},
            ping_interval=20,
        ) as websocket:
            self.root.after(0, lambda: self._set_speech_status("Listening for live captions..."))

            async def send_audio():
                while not stop_event.is_set():
                    try:
                        chunk = await asyncio.to_thread(audio_chunks.get, True, 0.25)
                    except queue.Empty:
                        continue
                    await websocket.send(chunk)

            async def receive_captions():
                async for message in websocket:
                    payload = json.loads(message)
                    if payload.get("type") != "Results":
                        continue
                    alternatives = payload.get("channel", {}).get("alternatives", [])
                    transcript = alternatives[0].get("transcript", "").strip() if alternatives else ""
                    if transcript:
                        is_final = payload.get("is_final", False)
                        self.root.after(0, lambda text=transcript, final=is_final: self._display_caption(text, final))

            async def wait_for_stop():
                await asyncio.to_thread(stop_event.wait)
                await websocket.close()

            tasks = [
                asyncio.create_task(send_audio()),
                asyncio.create_task(receive_captions()),
                asyncio.create_task(wait_for_stop()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            stop_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()

    def _display_caption(self, transcript, is_final):
        if is_final:
            self._add_transcript(transcript)
            self.live_caption_var.set("Listening for the next sentence...")
        else:
            self.live_caption_var.set(transcript)

    def _finish_transcription(self, failure_message):
        self.stop_transcription_event = None
        self.live_caption_var.set("Live caption will appear here.")
        self._set_speech_status(failure_message or "Live transcription stopped.")
        self._exit_output_only_view()

    def _enter_output_only_view(self):
        self._pre_collapse_geometry = self.root.geometry()
        self._status_label.pack_forget()
        self._opacity_frame.pack_forget()
        self._speech_pane.forget(self._speech_frame)
        self._response_provider_frame.pack_forget()

        width, height = 420, 260
        x = (self.root.winfo_screenwidth() - width) // 2
        self.root.geometry(f"{width}x{height}+{x}+0")

    def _exit_output_only_view(self):
        self._status_label.pack(pady=(10, 5), before=self._speech_pane)
        self._opacity_frame.pack(fill="x", padx=10, pady=10, before=self._speech_pane)
        self._speech_pane.insert(0, self._speech_frame, weight=1)
        self._response_provider_frame.pack(fill="x", padx=8, pady=(6, 2), before=self._response_toolbar_frame)
        self._response_status_label.pack(fill="x", padx=8, pady=(8, 4), before=self._response_toolbar_frame)
        self.root.geometry(getattr(self, "_pre_collapse_geometry", None) or self._expanded_geometry)

    def _set_speech_status(self, message):
        self.speech_status_var.set(message)
        self.transcribe_button.configure(
            text="Stop listening" if self.is_transcribing else "Start listening",
            state="normal",
        )

    def clear_chat(self, icon=None, item=None):
        """Wipe both scroll boxes and reset the AI's short-term reply memory
        so old exchanges neither clutter the screen nor influence future
        replies."""
        self.transcript_box.configure(state="normal")
        self.transcript_box.delete("1.0", "end")
        self.transcript_box.configure(state="disabled")

        self.response_box.configure(state="normal")
        self.response_box.delete("1.0", "end")
        self.response_box.configure(state="disabled")

        self.response_history = []

    def _enqueue_latest(self, q, job):
        """Drops any not-yet-started items before adding the new one, so a
        worker pulling from this queue always answers the most recently
        enqueued job instead of working through a stale backlog. Only
        affects items still waiting — anything a worker has already
        dequeued (in flight) is untouched."""
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        q.put(job)

    def _add_transcript(self, transcript):
        self.transcript_box.configure(state="normal")
        self.transcript_box.insert("end", transcript + "\n")
        self.transcript_box.see("end")
        self.transcript_box.configure(state="disabled")
        self._enqueue_latest(self.response_queue, transcript)

    def _response_worker(self):
        """Turn each finalized utterance into a concise English suggested reply."""
        while True:
            transcript = self.response_queue.get()
            if transcript is None:
                return

            provider = self.response_provider
            key_name = {
                "Claude": ANTHROPIC_KEY_NAME,
                "Gemini": GEMINI_KEY_NAME,
                "GPT": OPENAI_KEY_NAME,
            }[provider]
            api_key = keyring.get_password(CREDENTIAL_SERVICE, key_name)
            if not api_key:
                self.root.after(
                    0,
                    lambda name=provider: self._set_response_status(
                        f"Add your {name} key in API settings."
                    ),
                )
                continue

            try:
                self.root.after(0, lambda: self._set_response_status("Generating a concise reply..."))
                if provider == "Claude":
                    reply = self._generate_response_streaming(api_key, transcript)
                else:
                    reply = self._generate_response(provider, api_key, transcript)
                if not reply:
                    raise RuntimeError(f"{provider} returned an empty response.")

                self.response_history.extend(
                    [
                        {"role": "user", "content": transcript},
                        {"role": "assistant", "content": reply},
                    ]
                )
                self.response_history = self.response_history[-8:]
                if provider != "Claude":
                    self.root.after(0, lambda text=reply: self._add_response(text))
                self.root.after(0, lambda: self._set_response_status("Ready for the next sentence."))
            except Exception as error:
                self.root.after(0, lambda message=str(error): self._set_response_status(f"Response error: {message}"))

    def _get_anthropic_client(self, api_key):
        """Reuse one client (and its HTTP connection pool) instead of paying
        TLS/connection setup cost on every reply."""
        if self._anthropic_client is None or self._anthropic_client_key != api_key:
            self._anthropic_client = anthropic.Anthropic(api_key=api_key)
            self._anthropic_client_key = api_key
        return self._anthropic_client

    def _build_conversation(self, transcript):
        conversation = list(self.response_history)
        conversation.append(
            {
                "role": "user",
                "content": f"The other person just said:\n{transcript}",
            }
        )
        return conversation

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

    def _generate_response_streaming(self, api_key, transcript):
        """Stream Claude's reply so words appear as they're generated instead of
        waiting for the full ~160-token response, cutting perceived latency."""
        client = self._get_anthropic_client(api_key)
        conversation = self._build_conversation(transcript)
        text_parts = []
        with client.messages.stream(
            model="claude-sonnet-5",
            max_tokens=160,
            system=self._build_response_system_prompt(),
            messages=conversation,
        ) as stream:
            for delta in stream.text_stream:
                text_parts.append(delta)
                self.root.after(0, lambda chunk=delta: self._append_response_chunk(chunk))
        reply = "".join(text_parts).strip()
        self.root.after(0, self._end_response_chunk_stream)
        return reply

    def _generate_screen_analysis(self, api_key, image_b64):
        """Standalone vision request (no response_history attached) — always
        Claude, streamed the same way _generate_response_streaming is."""
        client = self._get_anthropic_client(api_key)
        text_parts = []
        with client.messages.stream(
            model="claude-sonnet-5",
            max_tokens=400,
            system=SCREEN_ANALYSIS_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        }
                    ],
                }
            ],
        ) as stream:
            for delta in stream.text_stream:
                text_parts.append(delta)
                self.root.after(0, lambda chunk=delta: self._append_response_chunk(chunk))
        reply = "".join(text_parts).strip()
        self.root.after(0, self._end_response_chunk_stream)
        return reply

    def _handle_screen_analysis_job(self, image_b64):
        api_key = keyring.get_password(CREDENTIAL_SERVICE, ANTHROPIC_KEY_NAME)
        if not api_key:
            self.root.after(
                0, lambda: self._set_response_status("Add your Anthropic key in API settings.")
            )
            return

        try:
            self.root.after(0, lambda: self._append_response_chunk("[Screen] "))
            reply = self._generate_screen_analysis(api_key, image_b64)
            if not reply:
                raise RuntimeError("Claude returned an empty response.")
            self.root.after(0, lambda: self._set_response_status("Ready for the next sentence."))
        except Exception as error:
            self.root.after(0, lambda message=str(error): self._set_response_status(f"Response error: {message}"))

    def _screen_analysis_worker(self):
        """Mirrors _response_worker's shape but for screen-analysis jobs,
        on its own queue/thread so it's never delayed by (or delays)
        pending transcript replies."""
        while True:
            job = self.screen_analysis_queue.get()
            if job is None:
                return
            self._handle_screen_analysis_job(job["data"])

    def _generate_response(self, provider, api_key, transcript):
        conversation = self._build_conversation(transcript)
        conversation_text = "\n".join(
            f"{message['role'].capitalize()}: {message['content']}" for message in conversation
        )
        prompt = f"{self._build_response_system_prompt()}\n\nRecent conversation:\n{conversation_text}"

        if provider == "Gemini":
            gemini_client = genai.Client(api_key=api_key)
            try:
                result = gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                )
                return (result.text or "").strip()
            finally:
                gemini_client.close()

        result = OpenAI(api_key=api_key).responses.create(
            model="gpt-5-mini",
            instructions=self._build_response_system_prompt(),
            input=conversation_text,
            max_output_tokens=160,
        )
        return (result.output_text or "").strip()

    def _set_response_status(self, message):
        self.response_status_var.set(message)

    def _add_response(self, response):
        self.response_box.configure(state="normal")
        self.response_box.insert("end", response + "\n\n")
        self.response_box.see("end")
        self.response_box.configure(state="disabled")

    def _append_response_chunk(self, chunk):
        self.response_box.configure(state="normal")
        self.response_box.insert("end", chunk)
        self.response_box.see("end")
        self.response_box.configure(state="disabled")

    def _end_response_chunk_stream(self):
        self.response_box.configure(state="normal")
        self.response_box.insert("end", "\n\n")
        self.response_box.see("end")
        self.response_box.configure(state="disabled")

    def _apply_taskbar_setting(self):
        """Change only the taskbar style without changing visibility or capture settings."""
        hwnd = self._get_hwnd()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if self.show_in_taskbar:
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        else:
            style = (style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def _show_window(self):
        def _do():
            self._apply_taskbar_setting()
            self.root.deiconify()
            self._set_capture_protection(self.privacy_mode)

        self.root.after(0, _do)
        self.visible = True

    def _hide_window(self):
        def _do():
            self._set_capture_protection(False)
            self.root.withdraw()

        self.root.after(0, _do)
        self.visible = False

        if self.click_through:
            self.click_through = False
            self.root.after(0, self._apply_click_through)

    def toggle_window(self, icon=None, item=None):
        if self.visible:
            self._hide_window()
        else:
            self._show_window()

    def toggle_taskbar_setting(self, icon=None, item=None):
        self.show_in_taskbar = not self.show_in_taskbar
        if self.visible:
            # Re-apply immediately if the window is currently shown.
            self.root.after(0, self._apply_taskbar_setting)

    def toggle_privacy_mode(self, icon=None, item=None):
        self.privacy_mode = not self.privacy_mode
        if self.visible:
            self.root.after(0, self._apply_privacy_mode)

    def _apply_privacy_mode(self):
        """Keep taskbar and screen-capture privacy independent from window visibility."""
        self.show_in_taskbar = not self.privacy_mode
        self._apply_taskbar_setting()
        self._set_capture_protection(self.privacy_mode)
        for hwnd in list(self._protected_hwnds):
            self._set_capture_protection(self.privacy_mode, hwnd=hwnd)

    def open_settings(self, icon=None, item=None):
        """Open a dialog for securely storing API keys in Windows Credential Manager."""
        self.root.after(0, self._open_settings_dialog)

    def _open_settings_dialog(self):
        if getattr(self, "settings_window", None) and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return

        dialog = tk.Toplevel(self.root)
        self.settings_window = dialog
        dialog.title("API Settings")
        dialog.geometry("420x350")
        dialog.configure(bg=APP_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.update_idletasks()
        self._protect_extra_window(self._hwnd_from_widget(dialog))

        ttk.Label(
            dialog,
            text="Keys are stored in Windows Credential Manager, not in this project.",
            justify="left",
            wraplength=390,
        ).pack(anchor="w", padx=15, pady=(15, 10))

        form = ttk.Frame(dialog)
        form.pack(fill="x", padx=15)
        ttk.Label(form, text="Deepgram API key").grid(row=0, column=0, sticky="w", pady=5)
        deepgram_var = tk.StringVar()
        ttk.Entry(form, textvariable=deepgram_var, show="*", width=38).grid(row=0, column=1, padx=(10, 0), pady=5)

        ttk.Label(form, text="Anthropic API key").grid(row=1, column=0, sticky="w", pady=5)
        anthropic_var = tk.StringVar()
        ttk.Entry(form, textvariable=anthropic_var, show="*", width=38).grid(row=1, column=1, padx=(10, 0), pady=5)

        ttk.Label(form, text="OpenAI API key").grid(row=2, column=0, sticky="w", pady=5)
        openai_var = tk.StringVar()
        ttk.Entry(form, textvariable=openai_var, show="*", width=38).grid(row=2, column=1, padx=(10, 0), pady=5)

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
        ttk.Label(dialog, textvariable=status_var, foreground=MUTED_TEXT_COLOR, wraplength=390, justify="left").pack(
            anchor="w", padx=15, pady=(12, 4)
        )

        self.settings_save_button = ttk.Button(
            dialog,
            text="Save securely",
            style="Accent.TButton",
            command=lambda: self._validate_and_save_api_keys(
                deepgram_var, anthropic_var, openai_var, gemini_var, status_var
            ),
        )
        self.settings_save_button.pack(pady=8)

    def _validate_and_save_api_keys(self, deepgram_var, anthropic_var, openai_var, gemini_var, status_var):
        keys = {
            "Deepgram": (DEEPGRAM_KEY_NAME, deepgram_var.get().strip()),
            "Anthropic": (ANTHROPIC_KEY_NAME, anthropic_var.get().strip()),
            "OpenAI": (OPENAI_KEY_NAME, openai_var.get().strip()),
            "Gemini": (GEMINI_KEY_NAME, gemini_var.get().strip()),
        }
        keys_to_validate = {name: value for name, value in keys.items() if value[1]}
        if not keys_to_validate:
            status_var.set("Enter a new key to validate, or leave fields blank to keep saved keys unchanged.")
            return

        self.settings_save_button.configure(state="disabled")
        status_var.set("Validating key(s) with the provider... No key will be saved until validation passes.")
        threading.Thread(
            target=self._validate_keys_worker,
            args=(keys_to_validate, deepgram_var, anthropic_var, openai_var, gemini_var, status_var),
            daemon=True,
        ).start()

    def _validate_keys_worker(self, keys_to_validate, deepgram_var, anthropic_var, openai_var, gemini_var, status_var):
        failures = []
        for provider, (_, key) in keys_to_validate.items():
            try:
                if provider == "Deepgram":
                    asyncio.run(self._validate_deepgram_key(key))
                elif provider == "Anthropic":
                    anthropic.Anthropic(api_key=key).models.list(limit=1)
                elif provider == "OpenAI":
                    OpenAI(api_key=key).models.list()
                else:
                    gemini_client = genai.Client(api_key=key)
                    try:
                        next(iter(gemini_client.models.list()), None)
                    finally:
                        gemini_client.close()
            except Exception as error:
                failures.append(f"{provider}: {error}")

        self.root.after(
            0,
            lambda: self._finish_key_validation(
                keys_to_validate,
                failures,
                deepgram_var,
                anthropic_var,
                openai_var,
                gemini_var,
                status_var,
            ),
        )

    async def _validate_deepgram_key(self, key):
        url = (
            "wss://api.deepgram.com/v1/listen?model=nova-3&language=multi&encoding=linear16"
            "&sample_rate=16000&channels=1"
        )
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {key}"},
            open_timeout=10,
        ) as websocket:
            await websocket.close()

    def _finish_key_validation(
        self,
        keys_to_validate,
        failures,
        deepgram_var,
        anthropic_var,
        openai_var,
        gemini_var,
        status_var,
    ):
        try:
            if failures:
                status_var.set("Validation failed; no keys were saved. " + " | ".join(failures))
                return

            saved = []
            for provider, (key_name, key) in keys_to_validate.items():
                keyring.set_password(CREDENTIAL_SERVICE, key_name, key)
                saved.append(provider)

            if "Deepgram" in keys_to_validate:
                deepgram_var.set("")
            if "Anthropic" in keys_to_validate:
                anthropic_var.set("")
            if "OpenAI" in keys_to_validate:
                openai_var.set("")
            if "Gemini" in keys_to_validate:
                gemini_var.set("")

            status_var.set(f"Validated and saved: {', '.join(saved)}.")
        except Exception as error:
            status_var.set(f"Could not save validated key(s): {error}")
        finally:
            self.settings_save_button.configure(state="normal")

    # ---------- tray side ----------

    def _draw_generated_icon(self, size=64):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Outer screen context.
        outer_margin = max(6, int(size * 0.10))
        screen_x0 = outer_margin
        screen_y0 = outer_margin + 2
        screen_x1 = size - outer_margin
        screen_y1 = size - outer_margin - 8
        draw.rounded_rectangle((screen_x0, screen_y0, screen_x1, screen_y1), radius=max(6, int(size * 0.12)), fill=(34, 40, 49, 255))
        draw.rounded_rectangle((screen_x0 + 3, screen_y0 + 3, screen_x1 - 3, screen_y1 - 3), radius=max(4, int(size * 0.08)), outline=(70, 82, 96, 255), width=max(1, int(size * 0.04)))

        # Base stand.
        stand_y = size - outer_margin + 1
        draw.rectangle((size // 2 - 8, stand_y - 4, size // 2 + 8, stand_y + 2), fill=(70, 82, 96, 255))

        # Center shield.
        shield_center = size // 2
        shield_width = max(18, int(size * 0.36))
        shield_height = max(22, int(size * 0.46))
        shield_top = size // 2 - shield_height // 2 + 1
        shield_bottom = shield_top + shield_height
        shield_left = shield_center - shield_width // 2
        shield_right = shield_center + shield_width // 2
        draw.polygon([
            (shield_center, shield_top),
            (shield_right, shield_top + 5),
            (shield_right, shield_bottom - 4),
            (shield_center, shield_bottom),
            (shield_left, shield_bottom - 4),
            (shield_left, shield_top + 5),
        ], fill=(24, 167, 181, 255))

        # Privacy cue: diagonal slash to imply hidden content.
        draw.line((shield_center - 6, shield_top + 7, shield_center + 6, shield_bottom - 7), fill="white", width=max(2, int(size * 0.06)))
        draw.line((shield_center - 5, shield_top + 8, shield_center + 5, shield_bottom - 6), fill=(24, 167, 181, 255), width=max(1, int(size * 0.03)))

        return img

    def _apply_window_icon(self):
        """Set the title-bar/taskbar icon to match the tray icon. Tk defaults
        to its own icon if this is never called, which is what makes a
        freshly built app look like it's still running an old build."""
        app_dir = Path(__file__).resolve().parent
        ico_path = app_dir / "assets" / "screengaurd.ico"
        png_path = app_dir / "assets" / "screengaurd.png"
        try:
            if ico_path.exists():
                self.root.iconbitmap(str(ico_path))
                return
        except Exception:
            pass
        try:
            if png_path.exists():
                # Keep a reference on self — Tk drops the image if it's GC'd.
                self._window_icon_photo = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self._window_icon_photo)
        except Exception:
            pass

    def create_icon_image(self):
        size = 64
        app_dir = Path(__file__).resolve().parent
        asset_candidates = [
            app_dir / "assets" / "screengaurd.ico",
            app_dir / "assets" / "screengaurd.png",
        ]

        for asset_path in asset_candidates:
            if not asset_path.exists():
                continue
            try:
                with Image.open(asset_path) as icon:
                    icon = icon.convert("RGBA")
                    return icon.resize((size, size), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
            except Exception:
                continue

        return self._draw_generated_icon(size)

    def on_quit(self, icon, item):
        icon.stop()
        self.root.after(0, self.root.destroy)

    def run_tray(self):
        image = self.create_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("Show/Hide", self.toggle_window, default=True),
            pystray.MenuItem("API Settings...", self.open_settings),
            pystray.MenuItem(
                "Show in Taskbar",
                self.toggle_taskbar_setting,
                checked=lambda item: self.show_in_taskbar,
            ),
            pystray.MenuItem(
                "Privacy mode (capture hidden)",
                self.toggle_privacy_mode,
                checked=lambda item: self.privacy_mode,
            ),
            pystray.MenuItem("Quit", self.on_quit),
        )

        self.icon = pystray.Icon("college_demo_app", image, "College Demo Tray App", menu)
        self.icon.run()  # blocks here; only the tray icon is visible to the user


def main():
    lock = _acquire_single_instance_lock()
    if lock is None:
        _focus_existing_instance()
        return

    app = HelloWorldApp()
    app.run_tray()


if __name__ == "__main__":
    main()
