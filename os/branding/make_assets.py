"""Generate Hangar's branding images (run once; the outputs are committed).

  QT_QPA_PLATFORM=offscreen uv run --extra apps python os/branding/make_assets.py

Writes into os/files/usr/share/:
  icons/hicolor/256x256/apps/dev.hangar.<app>.png   app icons (launchers, taskbar)
  wallpapers/Hangar/contents/images/3840x2160.png   desktop + login wallpaper
  plymouth/themes/hangar/watermark.png               boot splash logo
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, \
    QRadialGradient

ROOT = Path(__file__).resolve().parents[1] / "files" / "usr" / "share"
ACCENT = QColor("#7c9cff")
APPS = {"spaces": ("H", "#7c9cff"), "passwords": ("P", "#fbbf24"), "accounts": ("A", "#4ade80"),
        "models": ("M", "#e879f9"), "settings": ("S", "#38bdf8"), "setup": ("✦", "#a7bbff")}


def hangar_mark(p: QPainter, cx: float, cy: float, size: float, color: QColor) -> None:
    """The Hangar mark: a roof line over a hangar door."""
    pen = QPen(color, size * 0.09)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(cx - size * 0.42, cy + size * 0.18))
    path.lineTo(cx, cy - size * 0.30)
    path.lineTo(cx + size * 0.42, cy + size * 0.18)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(cx - size * 0.16, cy + size * 0.02, size * 0.32, size * 0.24), size * 0.03, size * 0.03)


def app_icon(letter: str, color: str, px: int = 256) -> QImage:
    img = QImage(px, px, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, px)
    g.setColorAt(0, QColor("#1c2233"))
    g.setColorAt(1, QColor("#0e1118"))
    p.setBrush(g)
    p.setPen(QPen(QColor("#2b3346"), px * 0.012))
    m = px * 0.06
    p.drawRoundedRect(QRectF(m, m, px - 2 * m, px - 2 * m), px * 0.22, px * 0.22)
    if letter == "H":
        hangar_mark(p, px / 2, px / 2 + px * 0.03, px * 0.62, QColor(color))
    else:
        p.setPen(QColor(color))
        f = QFont()
        f.setPixelSize(int(px * 0.5))
        f.setBold(True)
        p.setFont(f)
        p.drawText(img.rect(), Qt.AlignCenter, letter)
    p.end()
    return img


def wallpaper(w: int = 3840, h: int = 2160) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#121a36"))
    g.setColorAt(0.5, QColor("#0b0d12"))
    g.setColorAt(1, QColor("#1b1233"))
    p.fillRect(img.rect(), g)
    glow = QRadialGradient(QPointF(w * 0.5, h * 1.05), h * 0.9)
    glow.setColorAt(0, QColor(124, 156, 255, 70))
    glow.setColorAt(1, QColor(124, 156, 255, 0))
    p.fillRect(img.rect(), glow)
    # Nested hangar arches, receding.
    for i in range(7):
        s = h * (1.5 - i * 0.17)
        alpha = 26 + i * 9
        pen = QPen(QColor(124, 156, 255, alpha), h * 0.0025)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(w / 2 - s / 2, h * 1.02 - s * 0.62, s, s * 1.24), 0, 180 * 16)
    p.end()
    return img


def splash_logo(px: int = 360) -> QImage:
    img = QImage(px, px, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    hangar_mark(p, px / 2, px / 2 - px * 0.06, px * 0.62, QColor("#e8eaf0"))
    p.setPen(QColor("#e8eaf0"))
    f = QFont()
    f.setPixelSize(int(px * 0.11))
    f.setBold(True)
    f.setLetterSpacing(QFont.PercentageSpacing, 112)
    p.setFont(f)
    p.drawText(QRectF(0, px * 0.72, px, px * 0.2), Qt.AlignCenter, "HANGAR")
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    icons = ROOT / "icons/hicolor/256x256/apps"
    icons.mkdir(parents=True, exist_ok=True)
    for name, (letter, color) in APPS.items():
        app_icon(letter, color).save(str(icons / f"dev.hangar.{name}.png"))
    wp = ROOT / "wallpapers/Hangar/contents/images"
    wp.mkdir(parents=True, exist_ok=True)
    wallpaper().save(str(wp / "3840x2160.png"))
    wallpaper(1920, 1080).save(str(ROOT / "wallpapers/Hangar/contents/screenshot.png"))
    (ROOT / "wallpapers/Hangar/metadata.json").write_text(
        '{"KPlugin": {"Id": "Hangar", "Name": "Hangar", "License": "MIT", '
        '"Authors": [{"Name": "Hangar"}]}}\n')
    ply = ROOT / "plymouth/themes/hangar"
    ply.mkdir(parents=True, exist_ok=True)
    splash_logo().save(str(ply / "watermark.png"))
    print("assets written under", ROOT)


if __name__ == "__main__":
    main()
