"""Pure-Python virtual keyboard via /dev/uinput.

No third-party deps (no python-evdev, no ydotool) so nothing has to be compiled
for SteamOS. We talk to the kernel uinput driver directly with struct + ioctl.
Requires root (the plugin declares `flags: ["root"]`).
"""

import fcntl
import os
import struct
import time

UINPUT_PATHS = ["/dev/uinput", "/dev/input/uinput"]

# --- ioctl number encoding (asm-generic/ioctl.h) ---
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS       # 8
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS   # 16
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS    # 30
_IOC_WRITE = 1


def _IOC(direction, typ, nr, size):
    return ((direction << _IOC_DIRSHIFT) | (typ << _IOC_TYPESHIFT)
            | (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT))


def _IOW(typ, nr, size):
    return _IOC(_IOC_WRITE, typ, nr, size)


def _IO(typ, nr):
    return _IOC(0, typ, nr, 0)


_UINPUT_BASE = ord("U")
UI_SET_EVBIT = _IOW(_UINPUT_BASE, 100, 4)   # arg: int
UI_SET_KEYBIT = _IOW(_UINPUT_BASE, 101, 4)  # arg: int
UI_DEV_CREATE = _IO(_UINPUT_BASE, 1)
UI_DEV_DESTROY = _IO(_UINPUT_BASE, 2)

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0
KEY_LEFTSHIFT = 42

BUS_USB = 0x03

# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
# On x86_64 Linux, timeval is two 8-byte longs.
_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)  # 24

UINPUT_MAX_NAME_SIZE = 80
ABS_CNT = 64


def _make_uinput_user_dev(name, vendor=0x1234, product=0x5678, version=1):
    """Pack struct uinput_user_dev (legacy create path)."""
    name_b = name.encode("utf-8")[: UINPUT_MAX_NAME_SIZE - 1]
    # name[80], input_id{bustype,vendor,product,version} (4x u16),
    # ff_effects_max (u32), then absmax/min/fuzz/flat[ABS_CNT] (s32 each).
    fmt = "@%dsHHHHI%di" % (UINPUT_MAX_NAME_SIZE, 4 * ABS_CNT)
    return struct.pack(
        fmt, name_b, BUS_USB, vendor, product, version, 0,
        *([0] * (4 * ABS_CNT)),
    )


# US-layout char -> (keycode, needs_shift). Covers printable ASCII.
_LETTER_CODES = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
}
_DIGIT_CODES = {"1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
                "6": 7, "7": 8, "8": 9, "9": 10, "0": 11}
_DIGIT_SHIFT = {"1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
                "6": "^", "7": "&", "8": "*", "9": "(", "0": ")"}
_PUNCT = {
    "-": (12, False), "_": (12, True),
    "=": (13, False), "+": (13, True),
    "[": (26, False), "{": (26, True),
    "]": (27, False), "}": (27, True),
    "\\": (43, False), "|": (43, True),
    ";": (39, False), ":": (39, True),
    "'": (40, False), '"': (40, True),
    "`": (41, False), "~": (41, True),
    ",": (51, False), "<": (51, True),
    ".": (52, False), ">": (52, True),
    "/": (53, False), "?": (53, True),
}
KEY_SPACE = 57
KEY_ENTER = 28
KEY_TAB = 15

CHAR_MAP = {}
for _c, _code in _LETTER_CODES.items():
    CHAR_MAP[_c] = (_code, False)
    CHAR_MAP[_c.upper()] = (_code, True)
for _d, _code in _DIGIT_CODES.items():
    CHAR_MAP[_d] = (_code, False)
    CHAR_MAP[_DIGIT_SHIFT[_d]] = (_code, True)
CHAR_MAP.update(_PUNCT)
CHAR_MAP[" "] = (KEY_SPACE, False)
CHAR_MAP["\n"] = (KEY_ENTER, False)
CHAR_MAP["\t"] = (KEY_TAB, False)


class UInputKeyboard:
    """A virtual keyboard. Open once, reuse, close on unload."""

    def __init__(self, name="WhisPTT Virtual Keyboard"):
        self.fd = None
        self._open(name)

    def _open(self, name):
        fd = None
        last_err = None
        for path in UINPUT_PATHS:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as e:
                last_err = e
                continue
        if fd is None:
            raise RuntimeError(
                "could not open uinput (need /dev/uinput and root): %s" % last_err
            )
        self.fd = fd
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        # Enable every keycode we might emit (our map + shift all live < 256).
        for kc in range(256):
            fcntl.ioctl(fd, UI_SET_KEYBIT, kc)
        os.write(fd, _make_uinput_user_dev(name))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        # Give udev a beat to materialize the node before we type into it.
        time.sleep(0.2)

    def _emit(self, etype, code, value):
        os.write(self.fd, struct.pack(_EVENT_FMT, 0, 0, etype, code, value))

    def _syn(self):
        self._emit(EV_SYN, SYN_REPORT, 0)

    def _tap(self, keycode, shift=False):
        if shift:
            self._emit(EV_KEY, KEY_LEFTSHIFT, 1)
            self._syn()
        self._emit(EV_KEY, keycode, 1)
        self._syn()
        self._emit(EV_KEY, keycode, 0)
        self._syn()
        if shift:
            self._emit(EV_KEY, KEY_LEFTSHIFT, 0)
            self._syn()

    def type_text(self, text, delay=0.004):
        """Type a string as US-layout keystrokes.

        Characters outside printable ASCII are skipped (uinput is keycode-based,
        so true Unicode would need a different injection path). Good enough for
        English dictation into game chat / search fields.
        """
        skipped = 0
        for ch in text:
            mapping = CHAR_MAP.get(ch)
            if mapping is None:
                skipped += 1
                continue
            keycode, shift = mapping
            self._tap(keycode, shift)
            if delay:
                time.sleep(delay)
        return skipped

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
