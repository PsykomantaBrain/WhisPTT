"""Microphone capture on SteamOS.

Prefers PipeWire (`pw-record`, SteamOS 3.5+), then PulseAudio-compat
(`parecord`), then ALSA (`arecord`). Records 16 kHz mono 16-bit WAV — the
sweet spot for Whisper (small files, no accuracy loss).

Start spawns the recorder subprocess; stop sends SIGINT so the tool finalizes
the WAV header before exiting.
"""

import os
import shutil
import signal
import subprocess
import tempfile

# name -> argv builder (device may be "" / None for the default source)
_TOOLS = {
    "pw-record": lambda dev, out: (
        ["pw-record", "--rate=16000", "--channels=1", "--format=s16"]
        + (["--target", dev] if dev else [])
        + [out]
    ),
    "parecord": lambda dev, out: (
        ["parecord", "--rate=16000", "--channels=1",
         "--format=s16le", "--file-format=wav"]
        + (["-d", dev] if dev else [])
        + [out]
    ),
    "arecord": lambda dev, out: (
        ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav"]
        + (["-D", dev] if dev else [])
        + [out]
    ),
}
_PREFERENCE = ["pw-record", "parecord", "arecord"]


class Recorder:
    def __init__(self, device=None):
        self.device = device or None
        self.proc = None
        self.out_path = None
        self.tool = self._pick_tool()

    @staticmethod
    def _pick_tool():
        for name in _PREFERENCE:
            if shutil.which(name):
                return name
        return None

    def available(self):
        return self.tool is not None

    def start(self):
        if self.proc is not None:
            return
        if not self.tool:
            raise RuntimeError("no recording tool found (pw-record/parecord/arecord)")
        fd, path = tempfile.mkstemp(prefix="whisptt_", suffix=".wav")
        os.close(fd)
        self.out_path = path
        cmd = _TOOLS[self.tool](self.device, path)
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def stop(self):
        """Stop recording and return the WAV path (or None)."""
        if self.proc is None:
            return None
        try:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        except ProcessLookupError:
            pass
        self.proc = None
        path, self.out_path = self.out_path, None
        return path
