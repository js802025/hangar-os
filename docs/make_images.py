"""Build the screenshots used by the README and the wiki (docs/images/).

Sources are the frames video/capture.py records (out/video/) and the setup
wizard's screenshots from tests/smoke_setup.py (out/setup-*.png), so run
those first. Each screenshot is placed on the Hangar wallpaper with rounded
corners and a soft shadow, like the walkthrough video.

  QT_QPA_PLATFORM=offscreen uv run --extra apps python docs/make_images.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/images"
V = ROOT / "out/video"
SHOTS = {   # name: (source, framed on the wallpaper?)
    "hero": (V / "space/0076.png", True),
    "ask": (V / "ask/0046.png", True),
    "plan": (V / "plan/0050.png", True),
    "space": (V / "space/0076.png", True),
    "overview": (V / "overview/0018.png", True),
    "approve": (V / "approve/0010.png", True),
    "takeover": (V / "takeover/0000.png", True),
    "review": (V / "review/0000.png", True),
    "answer": (V / "answer/0046.png", True),
    "notes": (V / "notes/0000.png", True),
    "setup-welcome": (ROOT / "out/setup-0-welcome.png", False),
    "setup-accounts": (ROOT / "out/setup-2-accounts.png", False),
    "setup-models": (ROOT / "out/setup-3-models.png", False),
    "setup-pilot": (ROOT / "out/setup-4-pilot.png", False),
    "setup-done": (ROOT / "out/setup-6-done.png", False),
}


def framed(img: QImage, width: int = 1600, floating: bool = False) -> QImage:
    pad = 0.06 if not floating else 0.12
    s = width * (1 - 2 * pad) / img.width()
    w, h = img.width() * s, img.height() * s
    H = int(h + width * pad * 2)
    out = QImage(width, H, QImage.Format_RGB32)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    wall = QImage(str(ROOT / "os/files/usr/share/wallpapers/Hangar/contents/images/3840x2160.png"))
    p.drawImage(0, 0, wall.scaled(width, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
    shade = QLinearGradient(0, 0, 0, H)
    shade.setColorAt(0, QColor(0, 0, 0, 30))
    shade.setColorAt(1, QColor(0, 0, 0, 90))
    p.fillRect(0, 0, width, H, shade)
    x, y, r = (width - w) / 2, (H - h) / 2, 16
    if floating:
        p.drawImage(QRectF(x, y, w, h), img)
    else:
        for i in range(14, 0, -2):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 12))
            p.drawRoundedRect(QRectF(x - i, y - i + 10, w + 2 * i, h + 2 * i), r + i, r + i)
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
        p.save()
        p.setClipPath(path)
        p.drawImage(QRectF(x, y, w, h), img)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), r, r)
    p.end()
    return out


def main() -> None:
    QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (src, frame) in SHOTS.items():
        img = QImage(str(src))
        if img.isNull():
            print(f"missing {src}, skipped")
            continue
        if frame:
            img = framed(img, floating=frame == "float")
        elif img.width() > 1600:
            img = img.scaledToWidth(1600, Qt.SmoothTransformation)
        img.save(str(OUT / f"{name}.png"))
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name}: {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
