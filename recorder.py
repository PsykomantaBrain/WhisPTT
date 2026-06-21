"""Microphone capture on SteamOS.

Prefers PipeWire (`pw-record`, SteamOS 3.5+), then PulseAudio-compat
(`parecord`), then ALSA (`arecord`). Records 16 kHz mono 16-bit WAV — the
sweet spot for Whisper (small files, no accuracy loss).

Start spawns the recorder subprocess; stop sends SIGINT so the tool finalizes
the WAV header before exiting.
"""

import glob
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


def _audio_env():
    """Env for the recorder subprocess.

    The backend runs as root, but PipeWire/PulseAudio live in the user's login
    session. Point the tools at the user's runtime dir so they find the socket
    (root can open it — the socket is world-accessible). We locate it by
    scanning /run/user/<uid> for a live pipewire-0 socket rather than assuming
    uid 1000.
    """
    env = os.environ.copy()
    runtime = None
    for d in sorted(glob.glob("/run/user/*")):
        if os.path.exists(os.path.join(d, "pipewire-0")):
            runtime = d
            break
    if runtime is None:
        runtime = "/run/user/1000"  # SteamOS 'deck' user default
    env["XDG_RUNTIME_DIR"] = runtime
    env.setdefault("PULSE_RUNTIME_PATH", os.path.join(runtime, "pulse"))
    return env


class Recorder:
    def __init__(self, device=None):
        self.device = device or None
        self.proc = None
        self.out_path = None
        self.last_error = ""
        self._err_path = None
        self._err_fd = None
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
        self.last_error = ""
        # Capture the tool's stderr to a file so failures surface in the log
        # instead of vanishing.
        self._err_path = path + ".err"
        self._err_fd = open(self._err_path, "wb")
        cmd = _TOOLS[self.tool](self.device, path)
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=self._err_fd, env=_audio_env()
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
        # Drain captured stderr for diagnostics, then clean it up.
        if self._err_fd is not None:
            try:
                self._err_fd.close()
            except OSError:
                pass
            self._err_fd = None
        if self._err_path is not None:
            try:
                with open(self._err_path, "r", encoding="utf-8", errors="replace") as ef:
                    self.last_error = ef.read().strip()
            except OSError:
                self.last_error = ""
            try:
                os.remove(self._err_path)
            except OSError:
                pass
            self._err_path = None
        path, self.out_path = self.out_path, None
        return path
