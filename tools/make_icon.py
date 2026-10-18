"""生成应用图标（PNG + 多尺寸 ICO）。只在打包/美化时手动运行。"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
]


def rounded(size: int) -> Image.Image:
    s = 8  # 超采样
    n = size * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 竖直渐变
    top, bottom = (91, 140, 255), (36, 75, 184)
    grad = Image.new("RGBA", (n, n))
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y / max(1, n - 1)
        gd.line([(0, y), (n, y)], fill=(
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
            255,
        ))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    font = None
    for f in FONT_CANDIDATES:
        if pathlib.Path(f).exists():
            try:
                font = ImageFont.truetype(f, int(n * 0.62))
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()
    text = "R"
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((n - w) / 2 - box[0], (n - h) / 2 - box[1] - n * 0.02), text,
           font=font, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    big = rounded(256)
    big.save(WEB / "icon.png")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    imgs = [rounded(s[0]) for s in sizes]
    imgs[0].save(WEB / "icon.ico", format="ICO", sizes=sizes, append_images=imgs[1:])
    print("wrote", WEB / "icon.png", WEB / "icon.ico")


if __name__ == "__main__":
    main()
