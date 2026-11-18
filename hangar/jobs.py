"""Background commands the UI can watch and type into: vendor sign-ins
(`claude auth login` wants a code pasted back) and model downloads.

Each job runs in a pseudo-terminal, because the vendor CLIs need one. The UI
polls its output and can send a line of input. Jobs live in the daemon's
memory only.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import secrets
import struct
import subprocess
import termios
import threading
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]")
URL = re.compile(r"https://[^\s\"'<>]+")
MAX = 64_000

jobs: dict[str, "Job"] = {}


class Job:
    def __init__(self, title: str, argv: list[str], kind: str = "") -> None:
        self.id = secrets.token_hex(6)
        self.title, self.kind = title, kind
        self.raw = ""
        self.code: int | None = None
        self.started = time.time()
        master, slave = pty.openpty()
        # Very wide, so long sign-in URLs don't wrap.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                     start_new_session=True, close_fds=True)
        os.close(slave)
        self.fd = master
        threading.Thread(target=self._pump, daemon=True).start()
        jobs[self.id] = self

    def _pump(self) -> None:
        while True:
            try:
                data = os.read(self.fd, 4096)
            except OSError:
                break
            if not data:
                break
            self.raw = (self.raw + ANSI.sub("", data.decode(errors="replace")))[-MAX:]
        self.code = self.proc.wait()
        os.close(self.fd)

    @property
    def done(self) -> bool:
        return self.code is not None

    def send(self, text: str) -> None:
        if self.done:
            raise RuntimeError("that job has finished")
        os.write(self.fd, text.encode() + b"\r")

    def cancel(self) -> None:
        if not self.done:
            self.proc.terminate()

    def lines(self, n: int = 60) -> list[str]:
        """Terminal-ish rendering: a carriage return overwrites the line
        (progress bars), blank runs collapse."""
        out: list[str] = []
        for line in self.raw.replace("\r\n", "\n").split("\n"):
            line = line.rstrip("\r").rsplit("\r", 1)[-1].rstrip()
            if line or (out and out[-1]):
                out.append(line)
        return out[-n:]

    def to_dict(self) -> dict:
        urls = list(dict.fromkeys(URL.findall(self.raw)))
        return {"id": self.id, "title": self.title, "kind": self.kind, "done": self.done, "code": self.code,
                "lines": self.lines(), "urls": urls[-3:], "started": self.started}


def get(job_id: str) -> Job | None:
    return jobs.get(job_id)
