"""Turn the captured frames into the walkthrough video (1080p, 30 fps, with music).

Each scene sits on the Hangar wallpaper with rounded corners and a soft shadow,
drifts in slowly, crossfades into the next, and has a caption. Frames are drawn
with Qt and piped straight into ffmpeg.

  QT_QPA_PLATFORM=offscreen uv run --extra apps python video/render.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "out" / "video"
OUT = ROOT / "out" / "hangar-walkthrough.mp4"
W, H, FPS = 1920, 1080, 30
FADE = 0.6

# (scene, seconds, caption title, caption detail, capture fps)
SCENES = [
    ("title", 4.0, "", "", 0),
    ("setup", 8.0, "Ready in minutes", "Connect the AI you already pay for, get local models sized for your PC, "
                                       "and meet your pilot.", 0),
    ("chat", 11.0, "Just ask", "Tell your pilot what you need. It plans the work, puts a crew of agents on it, "
                               "and keeps you posted in one conversation.", 12),
    ("space", 9.0, "Watch them work", "Every agent has a desktop of its own. A crew can share one, each with its "
                                      "own cursor.", 12),
    ("takeover", 4.5, "Step in anytime", "Take the wheel on any desktop, then hand it back.", 0),
    ("review", 6.0, "Review every change", "See exactly what your crew changed. Undo any file, or open it in "
                                           "your editor.", 0),
    ("overview", 4.5, "Everything at a glance", "Every space, live, one key away.", 6),
    ("notes", 6.0, "Notes you share with your crew", "Agents keep what they find in your notes, in plain "
                                                     "Markdown that works with Obsidian too.", 0),
    ("passwords", 6.0, "Your sign-ins stay yours", "Agents ask to sign in. You approve, and Hangar fills it "
                                                   "in. Agents never see a password.", 2),
    ("ask", 6.0, "Ask from anywhere", "Ask about whatever is on your screen, by typing or by voice.", 12),
    ("outro", 5.0, "", "", 0),
]
SETUP_STEPS = ["setup-0-welcome", "setup-2-accounts", "setup-3-models", "setup-4-pilot", "setup-5-notify",
               "setup-6-done"]


class Scene:
    def __init__(self, name: str, seconds: float, title: str, detail: str, cap_fps: int) -> None:
        self.name, self.seconds, self.title, self.detail, self.cap_fps = name, seconds, title, detail, cap_fps
        if name == "setup":
            self.images = [QImage(str(ROOT / "out" / f"{s}.png")) for s in SETUP_STEPS]
        elif name in ("title", "outro"):
            self.images = []
        else:
            self.images = [QImage(str(p)) for p in sorted((FRAMES / name).glob("*.png"))]

    def image_at(self, t: float) -> QImage | None:
        if not self.images:
            return None
        if self.name == "setup":           # step through the wizard
            return self.images[min(int(t / (self.seconds / len(self.images))), len(self.images) - 1)]
        if self.cap_fps:
            return self.images[min(int(t * self.cap_fps), len(self.images) - 1)]
        return self.images[0]


def backdrop() -> QImage:
    img = QImage(str(ROOT / "os/files/usr/share/wallpapers/Hangar/contents/images/3840x2160.png"))
    return img.scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def draw_window(p: QPainter, img: QImage, t: float, dur: float, transparent: bool = False) -> None:
    """The captured window, fitted into the frame, with a slow drift-in zoom."""
    area_w, area_h = W * 0.80, H * 0.72
    s = min(area_w / img.width(), area_h / img.height())
    s *= 0.97 + 0.03 * ease(t / dur)          # gentle push-in
    w, h = img.width() * s, img.height() * s
    x, y = (W - w) / 2, (H - h) / 2 - 50
    r = 18
    if not transparent:
        for i in range(14, 0, -2):              # soft shadow
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawRoundedRect(QRectF(x - i, y - i + 12, w + 2 * i, h + 2 * i), r + i, r + i)
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
        p.save()
        p.setClipPath(path)
        p.drawImage(QRectF(x, y, w, h), img)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), r, r)
    else:
        p.drawImage(QRectF(x, y, w, h), img)


def draw_caption(p: QPainter, title: str, detail: str, alpha: float) -> None:
    if not title:
        return
    p.save()
    p.setOpacity(alpha)
    tf = QFont("Inter")
    tf.setPixelSize(34)
    tf.setWeight(QFont.DemiBold)
    df = QFont("Inter")
    df.setPixelSize(24)
    p.setFont(df)
    maxw = 1300
    wrapped = p.fontMetrics().boundingRect(QRectF(0, 0, maxw, 400).toRect(), Qt.AlignHCenter | Qt.TextWordWrap, detail)
    p.setFont(tf)
    tw = p.fontMetrics().horizontalAdvance(title)
    box_w = max(tw, wrapped.width()) + 72
    text_h = wrapped.height()
    box_h = 62 + text_h + 22
    x, y = (W - box_w) / 2, H - box_h - 54
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(12, 15, 22, 215))
    p.drawRoundedRect(QRectF(x, y, box_w, box_h), 22, 22)
    p.setPen(QColor("#a7bbff"))
    p.drawText(QRectF(x, y + 14, box_w, 40), Qt.AlignHCenter | Qt.AlignTop, title)
    p.setFont(df)
    p.setPen(QColor("#e8eaf0"))
    p.drawText(QRectF(x + 36, y + 58, box_w - 72, text_h + 6), Qt.AlignHCenter | Qt.TextWordWrap, detail)
    p.restore()


def draw_card(p: QPainter, kind: str, t: float, dur: float) -> None:
    """Title and outro: the Hangar mark and a line."""
    a = ease(t / 0.9) * (1 - ease((t - (dur - 0.6)) / 0.6) if kind == "outro" else 1)
    p.save()
    p.setOpacity(a)
    glow = QRadialGradient(QPointF(W / 2, H / 2 - 40), 420)
    glow.setColorAt(0, QColor(124, 156, 255, 60))
    glow.setColorAt(1, QColor(124, 156, 255, 0))
    p.fillRect(0, 0, W, H, glow)
    cx, cy, size = W / 2, H / 2 - 110 + 10 * (1 - ease(t / 1.2)), 210
    pen = QPen(QColor("#e8eaf0"), size * 0.09)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(cx - size * 0.42, cy + size * 0.18))
    path.lineTo(cx, cy - size * 0.30)
    path.lineTo(cx + size * 0.42, cy + size * 0.18)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#7c9cff"))
    p.drawRoundedRect(QRectF(cx - size * 0.16, cy + size * 0.02, size * 0.32, size * 0.24), 6, 6)
    f = QFont("Inter")
    f.setPixelSize(78)
    f.setWeight(QFont.Bold)
    f.setLetterSpacing(QFont.PercentageSpacing, 104)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, H / 2 + 20, W, 100), Qt.AlignCenter, "Hangar")
    f2 = QFont("Inter")
    f2.setPixelSize(30)
    p.setFont(f2)
    p.setPen(QColor("#a9b4cc"))
    line = ("An operating system for AI agents" if kind == "title"
            else "A crew of AI agents, working for you.")
    p.drawText(QRectF(0, H / 2 + 120, W, 50), Qt.AlignCenter, line)
    p.restore()


def main() -> None:
    QGuiApplication(sys.argv)
    scenes = [Scene(*s) for s in SCENES]
    total = sum(s.seconds for s in scenes) - FADE * (len(scenes) - 1)
    bg = backdrop()
    music = ROOT / "out" / "video" / "music.wav"
    if not music.exists():
        subprocess.run([sys.executable, str(ROOT / "video/music.py"), f"{total + 1:.1f}", str(music)], check=True)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb32", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-i", str(music), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT)],
        stdin=subprocess.PIPE)
    # Scene start times (overlapping by FADE for crossfades).
    starts, t0 = [], 0.0
    for s in scenes:
        starts.append(t0)
        t0 += s.seconds - FADE
    nframes = int(total * FPS)
    for f in range(nframes):
        t = f / FPS
        frame = QImage(W, H, QImage.Format_RGB32)
        p = QPainter(frame)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(0, 0, bg)
        shade = QLinearGradient(0, 0, 0, H)
        shade.setColorAt(0, QColor(0, 0, 0, 40))
        shade.setColorAt(1, QColor(0, 0, 0, 110))
        p.fillRect(0, 0, W, H, shade)
        for s, st in zip(scenes, starts):
            lt = t - st
            if lt < 0 or lt > s.seconds:
                continue
            alpha = min(1.0, ease(lt / FADE) if st > 0 else 1.0, ease((s.seconds - lt) / FADE) if s is not scenes[-1] else 1.0)
            p.save()
            p.setOpacity(alpha)
            if s.name in ("title", "outro"):
                draw_card(p, s.name, lt, s.seconds)
            else:
                img = s.image_at(lt)
                if img is not None and not img.isNull():
                    draw_window(p, img, lt, s.seconds, transparent=s.name == "ask")
            p.restore()
            draw_caption(p, s.title, s.detail, alpha * ease((lt - 0.3) / 0.5))
        p.end()
        ff.stdin.write(frame.constBits().tobytes())
        if f % (FPS * 5) == 0:
            print(f"  {t:5.1f}s / {total:.1f}s", flush=True)
    ff.stdin.close()
    ff.wait()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
