"""Pure-Python global key listener via /dev/input/event*.

Reads raw kernel input events (below Gamescope) so we can detect the PTT key's
press/release no matter what's focused. Reading is passive: it does not consume
the event, so the game / Steam still see the key too. Requires root.
"""

import fcntl
import glob
import os
import select
import struct
import threading

EV_KEY = 0x01
EV_ABS = 0x03

# Same input_event layout as uinput_kbd; x86_64 Linux (timeval = 2x long).
_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)  # 24

# Our own injected keyboard (uinput_kbd's device name) — skip it when scanning,
# or we'd read the keystrokes we type and false-trigger.
OWN_DEVICE_PREFIX = "WhisPTT"
_NAME_LEN = 256


def _eviocgname(length):
    # EVIOCGNAME(len) = _IOR('E', 0x06, len)
    return (2 << 30) | (length << 16) | (ord("E") << 8) | 0x06


def _device_name(fd):
    try:
        buf = bytearray(_NAME_LEN)
        fcntl.ioctl(fd, _eviocgname(_NAME_LEN), buf)
        return bytes(buf).split(b"\x00", 1)[0].decode("utf-8", "replace")
    except (OSError, OverflowError, ValueError):
        return ""


class EvdevListener:
    """Watches all input devices for a single target keycode.

    Callbacks fire on the listener thread — keep them quick or hand off to a
    worker thread for anything heavy (recording stop, network, injection).
    """

    def __init__(self, on_press=None, on_release=None, on_capture=None):
        self.on_press = on_press
        self.on_release = on_release
        self.on_capture = on_capture        # callback(keycode), one-shot
        self.target_code = None             # single keycode (keyboard/touch)
        self.capture_mode = False
        self.combo = []                     # gamepad chord components
        self._combo_state = []              # held-state per component
        self._combo_active = False
        self._combo_has_abs = False
        self._stop = threading.Event()
        self._thread = None
        self._fds = {}                      # fd -> path
        self._bufs = {}                     # fd -> leftover bytes

    def set_target(self, code):
        self.target_code = int(code) if code is not None else None

    def set_combo(self, components):
        """Configure a gamepad chord; PTT fires when ALL components are held.

        components: list of dicts, each either
          {"type": "key", "code": N}                      (a button)
          {"type": "abs", "code": N, "min": v}            (trigger/stick: held
          {"type": "abs", "code": N, "max": v}             when value >=min / <=max)
        Matched by code across every device (only the gamepad emits these), so
        it's independent of which /dev/input/eventN the pad happens to be.
        """
        self.combo = list(components or [])
        self._combo_state = [False] * len(self.combo)
        self._combo_active = False
        self._combo_has_abs = any(c.get("type") == "abs" for c in self.combo)

    def start_capture(self):
        self.capture_mode = True

    def stop_capture(self):
        self.capture_mode = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="whisptt-evdev", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._close()

    def _close(self):
        for fd in list(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = {}
        self._bufs = {}

    def _drop_fd(self, fd):
        try:
            os.close(fd)
        except OSError:
            pass
        self._fds.pop(fd, None)
        self._bufs.pop(fd, None)

    def _rescan(self):
        """(Re)open input devices.

        Steam Input creates (and tears down / recreates) its virtual gamepad
        lazily, around game sessions — long after the plugin loads at Decky
        boot. So we can't enumerate just once; we keep picking up devices that
        appear at runtime. Our own injected keyboard is skipped by name to
        avoid reading the keystrokes we type.
        """
        current = set(glob.glob("/dev/input/event*"))
        open_paths = set(self._fds.values())
        for path in sorted(current - open_paths):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue  # busy / no perms; retry next scan
            if _device_name(fd).startswith(OWN_DEVICE_PREFIX):
                os.close(fd)
                continue
            self._fds[fd] = path
            self._bufs[fd] = b""
        # Drop descriptors whose device path has disappeared.
        for fd, path in list(self._fds.items()):
            if path not in current:
                self._drop_fd(fd)

    def _run(self):
        self._fds = {}
        self._bufs = {}
        cycles = 0
        while not self._stop.is_set():
            if cycles % 5 == 0:          # rescan ~1/s (0.2s select * 5)
                self._rescan()
            cycles += 1
            fds = list(self._fds)
            if not fds:
                if self._stop.wait(0.2):
                    break
                continue
            try:
                ready, _, _ = select.select(fds, [], [], 0.2)
            except (OSError, ValueError):
                self._rescan()           # a fd went bad; let rescan clean up
                continue
            for fd in ready:
                try:
                    chunk = os.read(fd, _EVENT_SIZE * 64)
                except OSError:
                    self._drop_fd(fd)    # device vanished; reopened next scan
                    continue
                if not chunk:
                    continue
                data = self._bufs.get(fd, b"") + chunk
                n = len(data) - (len(data) % _EVENT_SIZE)
                for i in range(0, n, _EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack(
                        _EVENT_FMT, data[i:i + _EVENT_SIZE]
                    )
                    if etype == EV_KEY:
                        self._handle_key(code, value)
                        self._handle_combo(EV_KEY, code, value)
                    elif etype == EV_ABS and self._combo_has_abs:
                        self._handle_combo(EV_ABS, code, value)
                self._bufs[fd] = data[n:]
        self._close()

    def _handle_key(self, code, value):
        # Single keycode PTT (keyboard/touch) + capture. value: 1=press 0=release
        if self.capture_mode:
            if value == 1:
                self.capture_mode = False
                if self.on_capture:
                    self.on_capture(code)
            return
        if self.target_code is None or code != self.target_code:
            return
        if value == 1 and self.on_press:
            self.on_press()
        elif value == 0 and self.on_release:
            self.on_release()

    def _handle_combo(self, etype, code, value):
        # Gamepad chord: fire on_press when every component is held, on_release
        # when any lifts. Capture mode is keyboard-only, so skip combos then.
        if not self.combo or self.capture_mode:
            return
        changed = False
        for i, comp in enumerate(self.combo):
            if comp.get("type") == "key" and etype == EV_KEY and code == comp.get("code"):
                self._combo_state[i] = value != 0   # 1 press, 2 autorepeat, 0 up
                changed = True
            elif comp.get("type") == "abs" and etype == EV_ABS and code == comp.get("code"):
                pressed = True
                if "min" in comp and value < comp["min"]:
                    pressed = False
                if "max" in comp and value > comp["max"]:
                    pressed = False
                self._combo_state[i] = pressed
                changed = True
        if not changed:
            return
        all_pressed = all(self._combo_state)
        if all_pressed and not self._combo_active:
            self._combo_active = True
            if self.on_press:
                self.on_press()
        elif not all_pressed and self._combo_active:
            self._combo_active = False
            if self.on_release:
                self.on_release()
