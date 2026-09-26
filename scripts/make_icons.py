#!/usr/bin/env python3
"""Regenerate the site icon set from logo.png.

    python scripts/make_icons.py

The master artwork is `logo.png` at the repository root: white line art on
transparency, which is what lets the mark sit straight on the site's dark
background with no plate behind it.

Everything under `docs/assets/img/` is generated from it and is committed, so
the site has no build step for images. Re-run this after changing the master.

On size: the emblem carries a lot of fine web detail, and below about 32px it
averages into an unreadable blob. The smallest favicon emitted is therefore
32px, and the `.ico` holds 32/48/64 rather than the usual 16/32/48 — letting a
browser downscale from 32 looks better than shipping a 16px render of this.
Boosting the alpha to fight it does not help: it lifts the background web as
much as the spider, so the result is lighter rather than clearer.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "logo.png"
OUT = REPO_ROOT / "docs" / "assets" / "img"

# --void from the stylesheet. Used only where transparency is not an option.
VOID = (11, 14, 20, 255)


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit(
            "Pillow is needed to regenerate icons and is not a project dependency.\n"
            "    uv run --with pillow python scripts/make_icons.py"
        ) from None

    if not SOURCE.exists():
        raise SystemExit(f"no master artwork at {SOURCE}")

    OUT.mkdir(parents=True, exist_ok=True)
    art = Image.open(SOURCE).convert("RGBA")

    # Crop the transparent padding, then square it so nothing distorts. The
    # padding is ~4% per side, which at 32px would waste a pixel and a half.
    art = art.crop(art.getbbox())
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)

    def save(image, name: str, **kwargs) -> None:
        path = OUT / name
        image.save(path, optimize=True, **kwargs)
        print(f"  {name:<22}{image.size[0]:>4}px {path.stat().st_size / 1024:>7.1f} KB")

    def scaled(size: int):
        return square.resize((size, size), Image.LANCZOS)

    def plated(size: int, pad: float = 0.08):
        """Flatten onto the site background. iOS and Android ignore alpha."""
        canvas = Image.new("RGBA", (size, size), VOID)
        inner = int(size * (1 - pad * 2))
        mark = square.resize((inner, inner), Image.LANCZOS)
        canvas.paste(mark, ((size - inner) // 2, (size - inner) // 2), mark)
        return canvas

    print("marks (transparent, for the dark site):")
    save(scaled(512), "logo.png")
    save(scaled(192), "logo-192.png")
    save(scaled(64), "logo-64.png")

    print("favicons:")
    save(scaled(48), "favicon-48.png")
    save(scaled(32), "favicon-32.png")

    print("plated tiles:")
    save(plated(180), "apple-touch-icon.png")
    save(plated(512), "icon-512.png")

    ico = OUT / "favicon.ico"
    scaled(64).save(ico, sizes=[(32, 32), (48, 48), (64, 64)])
    print(f"  {'favicon.ico':<22} multi {ico.stat().st_size / 1024:>7.1f} KB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
