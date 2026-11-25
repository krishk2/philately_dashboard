"""Generate PNG/ICO favicons matching app/static/img/favicon.svg (Pillow can't render SVG).

Run from the repo root:  uv run python scripts/make_favicons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "img"
NAVY, TEAL, WHITE = (11, 53, 88), (15, 179, 154), (255, 255, 255)


def draw(size: int) -> Image.Image:
    s = 8  # draw at 8x and downsample for smooth edges
    big = size * s
    unit = big / 64
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def u(v):
        return v * unit

    d.rectangle([0, 0, big, big], fill=WHITE)
    # perforations: punch transparent holes along the edges
    for c in range(0, 65, 10):
        for x, y in ((c + 4, 0), (c + 4, 64), (0, c + 4), (64, c + 4)):
            d.ellipse([u(x - 3), u(y - 3), u(x + 3), u(y + 3)], fill=(0, 0, 0, 0))
    d.rounded_rectangle([u(7), u(7), u(57), u(57)], radius=u(3), fill=NAVY)
    d.rectangle([u(7), u(40), u(57), u(57)], fill=TEAL)
    d.rounded_rectangle([u(17), u(17), u(47), u(38)], radius=u(2.5), fill=WHITE)
    d.line(
        [u(17.8), u(18.5), u(32), u(29.5), u(46.2), u(18.5)],
        fill=NAVY,
        width=int(u(2.6)),
        joint="curve",
    )
    d.ellipse([u(19.8), u(46.3), u(24.2), u(50.7)], fill=WHITE)
    d.ellipse([u(26.8), u(46.3), u(31.2), u(50.7)], fill=(255, 255, 255, 180))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    draw(180).convert("RGB").save(OUT / "apple-touch-icon.png")
    draw(192).save(OUT / "icon-192.png")
    draw(512).save(OUT / "icon-512.png")
    draw(64).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("favicons written to", OUT)


if __name__ == "__main__":
    main()
