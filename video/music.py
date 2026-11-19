"""An original soundtrack for the walkthrough, synthesized from scratch (no
samples, nothing licensed): calm ambient electronica in A minor, 92 BPM.

  uv run --extra voice python video/music.py 70 out/video/music.wav
"""

from __future__ import annotations

import sys
import wave

import numpy as np

SR = 44_100
BPM = 92
BEAT = 60 / BPM
# Am – F – C – G, two bars each (MIDI notes)
CHORDS = [[57, 60, 64, 69], [53, 57, 60, 65], [48, 55, 60, 64], [55, 59, 62, 67]]
ARP = [0, 2, 1, 3, 2, 1, 3, 2]


def hz(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(0, len(x), 4096):          # chunked one-pole filter (fast enough in numpy)
        seg = x[i:i + 4096]
        out = np.empty_like(seg)
        for j, v in enumerate(seg):
            acc = (1 - a) * v + a * acc
            out[j] = acc
        y[i:i + 4096] = out
    return y


def env(n: int, attack: float, release: float) -> np.ndarray:
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / max(release, 1e-4))
    return e


def pad(chord: list[int], dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for note in chord:
        for detune in (-0.12, 0.0, 0.12):     # a little chorus
            f = hz(note + detune)
            out += 0.5 * np.sin(2 * np.pi * f * t) + 0.18 * np.sin(2 * np.pi * 2 * f * t + 0.3)
    shape = np.minimum(1, t / 1.2) * np.minimum(1, (dur - t) / 0.8).clip(0)
    return out * shape / (len(chord) * 3)


def pluck(note: int, dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(note)
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
    return tone * env(n, 0.004, 0.32) * 0.22


def kick(dur: float = 0.4) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 110 * np.exp(-t * 22) + 42
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.001, 0.16) * 0.55


def hat(dur: float = 0.08, rng=np.random.default_rng(7)) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n)
    noise = noise - lowpass(noise, 6000)          # keep the hiss, drop the thud
    return noise * env(n, 0.0005, 0.02) * 0.05


def place(track: np.ndarray, sound: np.ndarray, at: float) -> None:
    i = int(at * SR)
    if i >= len(track):
        return
    j = min(len(track), i + len(sound))
    track[i:j] += sound[: j - i]


def song(seconds: float) -> np.ndarray:
    n = int(seconds * SR)
    pads, bass, keys, drums = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    bar = 4 * BEAT
    t, k = 0.0, 0
    while t < seconds:
        chord = CHORDS[k % len(CHORDS)]
        place(pads, pad(chord, 2 * bar + 0.6), t)
        for b in range(8):                         # bass: root on each beat, two bars
            place(bass, np.sin(2 * np.pi * hz(chord[0] - 12) * np.arange(int(BEAT * SR)) / SR)
                  * env(int(BEAT * SR), 0.01, 0.35) * 0.3, t + b * BEAT)
        if t >= 2 * bar:                           # arpeggio comes in after the intro
            for s in range(16):
                note = chord[ARP[s % len(ARP)]] + 12
                place(keys, pluck(note, 0.6), t + s * BEAT / 2)
        if 4 * bar <= t < seconds - 2 * bar:       # light drums in the middle section
            for b in range(8):
                place(drums, kick(), t + b * BEAT) if b % 2 == 0 else None
                place(drums, hat(), t + b * BEAT + BEAT / 2)
        t += 2 * bar
        k += 1
    # A simple stereo echo on the keys for space.
    delay = int(BEAT * 0.75 * SR)
    echo = np.zeros(n)
    echo[delay:] = keys[:-delay] * 0.35
    left = pads * 0.9 + bass + keys + echo * 0.6 + drums
    right = pads * 0.9 + bass + keys * 0.85 + echo + drums
    stereo = np.stack([left, right], axis=1)
    fade = np.ones(n)
    fi, fo = int(2.5 * SR), int(4 * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[-fo:] = np.linspace(1, 0, fo)
    stereo *= fade[:, None]
    stereo /= np.abs(stereo).max() / 0.8
    return stereo


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 70
    out = sys.argv[2] if len(sys.argv) > 2 else "out/video/music.wav"
    data = (song(seconds) * 32767).astype("<i2")
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print(f"wrote {out} ({seconds:.0f}s)")


if __name__ == "__main__":
    main()
