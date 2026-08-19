# Building and launching Screengaurd

This covers pulling the latest changes and rebuilding the packaged app so the
"Launch Screengaurd" shortcut picks up the new code, the tray/title-bar icon,
and the resume-context feature.

## 1. Get the latest code

```bash
git checkout main
git pull origin main
```

Everything lands directly on `main` — there is no separate feature branch to
track. `git pull origin main` always gets the latest.

## 2. Install dependencies

Dependencies are listed in the `pip install` line at the top of `tray_app.py`.
As of this patch:

```bash
pip install pystray pillow keyboard sounddevice soundcard keyring websockets numpy anthropic openai google-genai sv_ttk pypdf
```

(`sv_ttk` is the modern light theme; `pypdf` is new in this patch, for resume
PDF text extraction.)

## 3. Close the running app before rebuilding

`dist/tray_app.exe` is locked while running, so the build will fail with a
`PermissionError` if it's still open. Close it first:

```bash
taskkill /IM tray_app.exe /F
```

(Ignore "not found" if it wasn't running.)

## 4. Rebuild the packaged exe

```bash
pyinstaller --noconfirm tray_app.spec
```

Use the `.spec` file (not a fresh `pyinstaller tray_app.py` command) — it's
what wires in the launch/tray icon (`assets/screengaurd.ico`) and bundles
`sv_ttk`'s theme resource files, both of which a default build would miss.

This produces `dist/tray_app.exe`.

## 5. Shortcut

The "Launch Screengaurd" shortcut (project folder + desktop) already points
at `dist/tray_app.exe`, so rebuilding in place is enough — no need to
recreate it. If it's ever missing, regenerate it with:

```bash
python create_shortcut.py
```

## 6. Verify

Run the exe directly to confirm it starts without error:

```bash
dist/tray_app.exe
```

Then open the tray icon menu → "API Settings..." and confirm the "Resume"
section (Load resume.../Clear resume) is present.
