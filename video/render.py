"""Turn the captured frames into the demo video (1080p, 30 fps, with music).

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
    ("why", 5.0, "Most of your to-do list happens on a computer.",
     "Hangar hands it to a crew of AI agents that use one, just like you do.", 0),
    ("ask", 10.5, "Hand it off the moment you think of it",
     "One shortcut, from anywhere on your computer. Type it or say it.", 12),
    ("plan", 9.0, "A crew takes it from there",
     "Your pilot splits the job and matches each part to the most efficient model that can handle it.", 6.56),
    ("space", 12.5, "They work the way you do",
     "Real websites, real apps, real files. If you can do it on a computer, so can they.", 6.31),
    ("overview", 5.0, "Many jobs at once",
     "Shopping, email, code, travel, paperwork: all moving while you get on with your day.", 4.20),
    ("approve", 4.5, "Your accounts stay yours",
     "Agents ask before they sign in anywhere, and never see a password.", 4.29),
    ("takeover", 4.0, "You're always in charge", "Watch any agent work, step in, and hand it back.", 0),
    ("review", 5.0, "Nothing happens behind your back", "See every change, and undo anything you don't like.", 0),
    ("answer", 8.0, "Come back to finished work", "One answer, with the work already done.", 6.53),
    ("notes", 5.0, "It all adds up", "What your crew finds is kept in your notes, for you and for them.", 0),
    ("private", 5.0, "It lives on your computer.",
     "Agents see only the folders you share, and can run on AI models on your own machine.", 0),
    ("outro", 5.0, "", "", 0),
]
CARDS = {"why", "private"}
SAME_WINDOW = {"plan", "space", "overview", "approve", "takeover", "review", "answer"}
# The pilot hands each agent its own job as the space scene opens (seconds into the scene).
ASSIGN = {"space": (0.8, 4.4, [("#7c9cff", "bikes", "Search PedalSwap for 56 cm road bikes under $500",
                                 "Qwen3.5 9B · on this PC"),
                                ("#f472b6", "wren", "Read reviews of the most promising models", "Claude")])}
SETUP_STEPS = ["setup-0-welcome", "setup-2-accounts", "setup-3-models", "setup-4-pilot", "setup-5-notify",
               "setup-6-done"]


class Scene:
    def __init__(self, name: str, seconds: float, title: str, detail: str, cap_fps: int) -> None:
        self.name, self.seconds, self.title, self.detail, self.cap_fps = name, seconds, title, detail, cap_fps
        if name == "setup":
            self.images = [QImage(str(ROOT / "out" / f"{s}.png")) for s in SETUP_STEPS]
        elif name in ("title", "outro") or name in CARDS:
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
    # No zoom: consecutive scenes of the same window line up exactly, so a crossfade only changes its content.
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
    p.setOpacity(p.opacity() * a)
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
    line = ("The operating system with a crew of AI agents built in" if kind == "title"
            else "Your computer, with a crew.")
    p.drawText(QRectF(0, H / 2 + 120, W, 50), Qt.AlignCenter, line)
    p.restore()


def text_alpha(t: float, dur: float, first: bool, last: bool) -> float:
    """Text fades in only after the crossfade into this scene is over, and is gone
    before the crossfade out starts, so two scenes' words never overlap."""
    a_in = 1.0 if first else ease((t - FADE) / 0.4)
    a_out = 1.0 if last else ease((dur - FADE - t) / 0.3)
    return max(0.0, min(a_in, a_out))


KEYS = {"ask": (0.35, 1.45, ["Meta", "Space"])}


def draw_keys(p: QPainter, keys, a: float) -> None:
    """Keycaps for the shortcut being pressed."""
    if a <= 0:
        return
    p.save()
    p.setOpacity(a)
    f = QFont("Inter")
    f.setPixelSize(30)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    widths = [max(110, p.fontMetrics().horizontalAdvance(k) + 64) for k in keys]
    plus = 54
    total = sum(widths) + plus * (len(keys) - 1)
    x, y = (W - total) / 2, H / 2 - 80 - 10 * (1 - a)
    for i, (k, kw) in enumerate(zip(keys, widths)):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(QRectF(x, y + 8, kw, 84), 16, 16)
        p.setBrush(QColor(28, 33, 45, 245))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1.4))
        p.drawRoundedRect(QRectF(x, y, kw, 84), 16, 16)
        p.setPen(QColor("#eceef4"))
        p.drawText(QRectF(x, y, kw, 84), Qt.AlignCenter, k)
        x += kw
        if i < len(keys) - 1:
            p.setPen(QColor("#a9b4cc"))
            p.drawText(QRectF(x, y, plus, 84), Qt.AlignCenter, "+")
            x += plus
    p.restore()


def draw_assign(p: QPainter, rows, a: float) -> None:
    """A small 'the pilot gave out these tasks' card over the scene."""
    if a <= 0:
        return
    p.save()
    p.setOpacity(a)
    mono = QFont("Menlo")
    mono.setStyleHint(QFont.Monospace)
    mono.setPixelSize(22)
    head = QFont("Inter")
    head.setPixelSize(17)
    head.setWeight(QFont.DemiBold)
    p.setFont(mono)
    small = QFont("Inter")
    small.setPixelSize(17)
    tag_w = 0
    p.setFont(small)
    tag_w = max(p.fontMetrics().horizontalAdvance(m) for *_x, m in rows) + 40
    p.setFont(mono)
    w = max(p.fontMetrics().horizontalAdvance(f"{n}  {t}") for _c, n, t, _m in rows) + 110 + tag_w
    h = 64 + 46 * len(rows)
    x, y = (W - w) / 2, 250 - 16 * (1 - a)
    p.setPen(Qt.NoPen)
    for i in range(12, 0, -3):
        p.setBrush(QColor(0, 0, 0, 14))
        p.drawRoundedRect(QRectF(x - i, y - i + 8, w + 2 * i, h + 2 * i), 20 + i, 20 + i)
    p.setBrush(QColor(13, 16, 23, 240))
    p.setPen(QPen(QColor(124, 156, 255, 90), 1.4))
    p.drawRoundedRect(QRectF(x, y, w, h), 18, 18)
    p.setFont(head)
    p.setPen(QColor("#a7bbff"))
    p.drawText(QRectF(x + 28, y + 16, w - 56, 26), Qt.AlignLeft | Qt.AlignVCenter, "✦  Pilot → crew")
    for i, (color, name, task, model) in enumerate(rows):
        ry = y + 54 + 46 * i
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(x + 30, ry + 12, 14, 14))
        p.setFont(mono)
        p.setPen(QColor(color))
        nw = p.fontMetrics().horizontalAdvance(name + "  ")
        p.drawText(QRectF(x + 56, ry, nw + 10, 38), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setPen(QColor("#e8eaf0"))
        p.drawText(QRectF(x + 56 + nw, ry, w - nw - 80 - tag_w, 38), Qt.AlignLeft | Qt.AlignVCenter, task)
        p.setFont(small)
        tw = p.fontMetrics().horizontalAdvance(model) + 22
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(QRectF(x + w - 28 - tw, ry + 6, tw, 26), 13, 13)
        p.setPen(QColor("#a9b4cc"))
        p.drawText(QRectF(x + w - 28 - tw, ry + 6, tw, 26), Qt.AlignCenter, model)
    p.restore()


def draw_statement(p: QPainter, line: str, sub: str, t: float, dur: float, ta: float = 1.0) -> None:
    """A full-frame statement between the screen scenes."""
    p.save()
    base = p.opacity()
    p.fillRect(0, 0, W, H, QColor(6, 8, 12, 150))
    glow = QRadialGradient(QPointF(W / 2, H / 2), 700)
    glow.setColorAt(0, QColor(124, 156, 255, 45))
    glow.setColorAt(1, QColor(124, 156, 255, 0))
    p.fillRect(0, 0, W, H, glow)
    p.setOpacity(base * ta)
    rise = 14 * (1 - ease((t - FADE) / 1.0))
    f = QFont("Inter")
    f.setPixelSize(60)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(160, H / 2 - 120 + rise, W - 320, 150), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, line)
    p.setOpacity(base * ta * ease((t - FADE - 0.4) / 0.8))
    f2 = QFont("Inter")
    f2.setPixelSize(32)
    p.setFont(f2)
    p.setPen(QColor("#a9b4cc"))
    p.drawText(QRectF(260, H / 2 + 50 + rise, W - 520, 120), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, sub)
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
            i = scenes.index(s)
            prev = scenes[i - 1] if i else None
            nxt = scenes[i + 1] if i + 1 < len(scenes) else None
            # Between two shots of the same Hangar window, cut quickly instead of a slow
            # crossfade, so its layout never shows twice at once.
            quick_in = prev is not None and prev.name in SAME_WINDOW and s.name in SAME_WINDOW
            quick_out = nxt is not None and nxt.name in SAME_WINDOW and s.name in SAME_WINDOW
            a_in = 1.0 if st == 0 else (ease((lt - FADE / 2 + 0.06) / 0.12) if quick_in else ease(lt / FADE))
            a_out = 1.0 if nxt is None else (ease((s.seconds - lt - FADE / 2 + 0.06) / 0.12) if quick_out
                                             else ease((s.seconds - lt) / FADE))
            alpha = min(1.0, a_in, a_out)
            p.save()
            p.setOpacity(alpha)
            ta = text_alpha(lt, s.seconds, s is scenes[0], s is scenes[-1])
            if s.name in ("title", "outro"):
                p.setOpacity(alpha * ta)
                draw_card(p, s.name, lt, s.seconds)
            elif s.name in CARDS:
                draw_statement(p, s.title, s.detail, lt, s.seconds, ta)
            else:
                img = s.image_at(lt)
                if img is not None and not img.isNull():
                    draw_window(p, img, lt, s.seconds)
            p.restore()
            if s.name in KEYS:
                k0, k1, keys = KEYS[s.name]
                draw_keys(p, keys, min(ease((lt - k0) / 0.25), ease((k1 - lt) / 0.25)))
            if s.name in ASSIGN:
                a0, a1, rows = ASSIGN[s.name]
                draw_assign(p, rows, min(ease((lt - a0) / 0.35), ease((a1 - lt) / 0.35)))
            if s.name not in CARDS:
                draw_caption(p, s.title, s.detail, ta)
        p.end()
        ff.stdin.write(frame.constBits().tobytes())
        if f % (FPS * 5) == 0:
            print(f"  {t:5.1f}s / {total:.1f}s", flush=True)
    ff.stdin.close()
    ff.wait()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
