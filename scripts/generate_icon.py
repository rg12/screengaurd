"""Generate the Screengaurd launch icon assets.

Draws a shield-over-screen mark (dark monitor silhouette with a teal shield
and privacy slash inside) matching docs/superpowers/specs/2026-08-07-launch-icon-design.md,
then exports assets/screengaurd.png and a multi-size assets/screengaurd.ico.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"

SCREEN_FILL = (34, 40, 49, 255)
SCREEN_OUTLINE = (70, 82, 96, 255)
SHIELD_FILL = (24, 167, 181, 255)
STAND_FILL = (70, 82, 96, 255)


def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    outer_margin = max(6, int(size * 0.10))
    screen_x0 = outer_margin
    screen_y0 = outer_margin + int(size * 0.03)
    screen_x1 = size - outer_margin
    screen_y1 = size - outer_margin - int(size * 0.12)
    draw.rounded_rectangle(
        (screen_x0, screen_y0, screen_x1, screen_y1),
        radius=max(6, int(size * 0.12)),
        fill=SCREEN_FILL,
    )
    draw.rounded_rectangle(
        (screen_x0 + max(2, int(size * 0.03)), screen_y0 + max(2, int(size * 0.03)),
         screen_x1 - max(2, int(size * 0.03)), screen_y1 - max(2, int(size * 0.03))),
        radius=max(4, int(size * 0.08)),
        outline=SCREEN_OUTLINE,
        width=max(1, int(size * 0.025)),
    )

    stand_y = size - outer_margin + 1
    stand_half = max(6, int(size * 0.12))
    draw.rectangle(
        (size // 2 - stand_half, stand_y - max(2, int(size * 0.05)),
         size // 2 + stand_half, stand_y + int(size * 0.03)),
        fill=STAND_FILL,
    )

    shield_center = size // 2
    shield_width = max(18, int(size * 0.36))
    shield_height = max(22, int(size * 0.46))
    shield_top = size // 2 - shield_height // 2 + 1
    shield_bottom = shield_top + shield_height
    shield_left = shield_center - shield_width // 2
    shield_right = shield_center + shield_width // 2
    notch = max(4, int(size * 0.05))
    draw.polygon(
        [
            (shield_center, shield_top),
            (shield_right, shield_top + notch),
            (shield_right, shield_bottom - notch + 1),
            (shield_center, shield_bottom),
            (shield_left, shield_bottom - notch + 1),
            (shield_left, shield_top + notch),
        ],
        fill=SHIELD_FILL,
    )

    slash_inset_x = max(4, int(size * 0.06))
    slash_inset_y = max(4, int(size * 0.07))
    draw.line(
        (shield_center - slash_inset_x, shield_top + slash_inset_y,
         shield_center + slash_inset_x, shield_bottom - slash_inset_y),
        fill="white",
        width=max(2, int(size * 0.055)),
    )
    draw.line(
        (shield_center - slash_inset_x + 1, shield_top + slash_inset_y + 1,
         shield_center + slash_inset_x - 1, shield_bottom - slash_inset_y - 1),
        fill=SHIELD_FILL,
        width=max(1, int(size * 0.025)),
    )

    return img


def main():
    ASSETS_DIR.mkdir(exist_ok=True)

    png_size = 512
    master = draw_icon(png_size)
    png_path = ASSETS_DIR / "screengaurd.png"
    master.save(png_path)

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_path = ASSETS_DIR / "screengaurd.ico"
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    frames = [master.resize((s, s), resample) for s in ico_sizes]
    frames[0].save(ico_path, format="ICO", sizes=[(s, s) for s in ico_sizes], append_images=frames[1:])

    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()
