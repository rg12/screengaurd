# Launch Icon Design

## Goal

Create a Windows-friendly launch/tray icon for the Screengaurd Python tray app. The icon should replace the current generated blue circle with "A" and communicate screen privacy/protection at small sizes.

## Chosen Direction

Use a shield over a screen:

- A dark monitor or screen silhouette as the outer context.
- A blue/teal shield centered inside the display.
- A small privacy/capture-hidden cue kept simple enough to remain legible in the tray.

This direction balances the product name and function better than a plain letter mark or an eye-slash icon alone.

## Assets

Add these assets:

- `assets/screengaurd.png`: source/preview image.
- `assets/screengaurd.ico`: Windows icon with multiple sizes for launcher and tray use.

The `.ico` should include common sizes such as 16, 24, 32, 48, 64, 128, and 256 pixels where tooling supports them.

## Code Integration

Update `tray_app.py` so `create_icon_image()` loads `assets/screengaurd.ico` or `assets/screengaurd.png` from the app directory. If the asset is missing or cannot be loaded, keep a generated Pillow fallback icon so the tray app still starts.

## Verification

Verify that:

- The assets exist.
- Pillow can load the generated icon.
- `tray_app.py` still imports successfully.
- The fallback behavior remains available if the icon file is absent.
