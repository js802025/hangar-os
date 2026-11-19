"""Optional voice input for "Ask Hangar": local speech-to-text with Whisper.

Nothing is downloaded until the user asks for it (Hangar Settings → Voice, or
`hangar voice download small`). Audio is recorded in memory, transcribed on
this machine, and never stored or sent anywhere: only the resulting text goes
into the Ask panel, where the user can edit it before sending.

  python -m hangar.voice download <size>   (used by Settings, shows progress)
  python -m hangar.voice test <audio file> (prints the transcript)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from hangar import auth

SIZES = {"tiny": "75 MB, fastest, rougher", "base": "145 MB, fast", "small": "465 MB, good (recommended)",
         "medium": "1.5 GB, best, slower without a GPU"}
ROOT = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "hangar" / "whisper"
RATE = 16_000


def model_dir(size: str) -> Path:
    return ROOT / size


def available() -> bool:
    """Is the speech-to-text library installed (the `voice` extra)?"""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def installed() -> list[str]:
    return [s for s in SIZES if (model_dir(s) / "model.bin").exists()]


def chosen() -> str:
    """The model the Ask panel uses, or "" when voice is off."""
    try:
        size = json.loads((auth.CONFIG_DIR / "settings.json").read_text()).get("voice", {}).get("model", "")
    except (OSError, ValueError):
        size = ""
    if size in installed():
        return size
    have = installed()
    return have[0] if have else ""


def choose(size: str) -> None:
    path = auth.CONFIG_DIR / "settings.json"
    try:
        st = json.loads(path.read_text())
    except (OSError, ValueError):
        st = {}
    st["voice"] = {"model": size}
    auth.write_private(path, json.dumps(st, indent=2))


def download(size: str) -> Path:
    if size not in SIZES:
        raise ValueError(f"size: {' | '.join(SIZES)}")
    from faster_whisper import download_model
    out = model_dir(size)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Downloading the {size} speech model ({SIZES[size].split(',')[0]})…", flush=True)
    download_model(size, output_dir=str(out))
    print("Voice input is ready.", flush=True)
    return out


def remove(size: str) -> None:
    shutil.rmtree(model_dir(size), ignore_errors=True)


_models: dict = {}


def transcribe(audio, size: str = "") -> str:
    """audio: a file path, or float32 mono samples at 16 kHz."""
    size = size or chosen()
    if not size:
        raise RuntimeError("Voice input is off: download a speech model in Hangar Settings → Voice.")
    from faster_whisper import WhisperModel
    if size not in _models:
        _models[size] = WhisperModel(str(model_dir(size)), device="auto", compute_type="int8")
    if isinstance(audio, (str, Path)):
        audio = read_wav(audio)
    segments, _info = _models[size].transcribe(audio, vad_filter=True, beam_size=1)
    return " ".join(s.text.strip() for s in segments).strip()


def read_wav(path) -> "numpy.ndarray":
    """16 kHz mono 16-bit WAV -> float32 samples (no audio-decoding library needed)."""
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM WAV")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
        if w.getframerate() != RATE:   # simple resample; fine for speech
            n = int(len(data) * RATE / w.getframerate())
            data = np.interp(np.linspace(0, len(data), n, endpoint=False), np.arange(len(data)), data).astype("float32")
    return data


class Recorder:
    """Push-to-talk recording into memory (no files)."""

    def __init__(self) -> None:
        self.frames: list = []
        self.stream = None

    def start(self) -> None:
        import sounddevice as sd
        self.frames = []
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                     callback=lambda data, *_: self.frames.append(data.copy()))
        self.stream.start()

    def stop(self):
        import numpy as np
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return np.concatenate(self.frames)[:, 0] if self.frames else np.zeros(0, dtype="float32")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "download":
        download(sys.argv[2])
        choose(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "test":
        print(transcribe(sys.argv[2]))
    else:
        sys.exit("usage: python -m hangar.voice download <tiny|base|small|medium> | test <audio file>")


if __name__ == "__main__":
    main()
