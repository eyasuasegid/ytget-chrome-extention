#!/usr/bin/env python3
"""Generate crisp, high-resolution PNG icons for the ytget Chrome extension."""

from pathlib import Path
from PIL import Image, ImageDraw

ICONS_DIR = Path(__file__).resolve().parent / "extension" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)


def draw_icon(size: int) -> Image.Image:
    # 4x supersampling for ultra smooth antialiasing
    scale = 4
    dim = size * scale
    img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Red YouTube-style rounded rectangle
    margin = int(dim * 0.05)
    radius = int(dim * 0.22)
    draw.rounded_rectangle(
        [margin, margin, dim - margin, dim - margin],
        radius=radius,
        fill=(220, 38, 38, 255),  # Vibrant modern red
    )

    # White downward download arrow & tray
    arrow_color = (255, 255, 255, 255)
    cx = dim // 2
    top_y = int(dim * 0.22)
    stem_bot_y = int(dim * 0.52)
    stem_w = max(scale * 2, int(dim * 0.14))

    # Arrow stem
    draw.rectangle(
        [cx - stem_w // 2, top_y, cx + stem_w // 2, stem_bot_y],
        fill=arrow_color,
    )

    # Arrow head
    head_y = int(dim * 0.68)
    head_w = int(dim * 0.32)
    draw.polygon(
        [
            (cx, head_y),
            (cx - head_w, stem_bot_y),
            (cx + head_w, stem_bot_y),
        ],
        fill=arrow_color,
    )

    # Bottom tray/bracket
    tray_top = int(dim * 0.74)
    tray_bot = int(dim * 0.82)
    tray_left = int(dim * 0.24)
    tray_right = int(dim * 0.76)
    tray_thick = max(scale * 2, int(dim * 0.08))

    # Tray bottom
    draw.rectangle(
        [tray_left, tray_bot - tray_thick, tray_right, tray_bot],
        fill=arrow_color,
    )
    # Tray left lip
    draw.rectangle(
        [tray_left, tray_top, tray_left + tray_thick, tray_bot],
        fill=arrow_color,
    )
    # Tray right lip
    draw.rectangle(
        [tray_right - tray_thick, tray_top, tray_right, tray_bot],
        fill=arrow_color,
    )

    # Downsample with Lanczos filter for crisp edges
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main():
    for size in (16, 48, 128):
        icon = draw_icon(size)
        out_path = ICONS_DIR / f"icon{size}.png"
        icon.save(out_path, format="PNG")
        print(f"Generated {out_path.name} ({size}x{size})")


if __name__ == "__main__":
    main()
