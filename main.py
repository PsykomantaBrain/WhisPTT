"""WhisPTT backend — push-to-talk dictation orchestrator.

Flow:
  PTT key down  -> start recording the mic
  PTT key up    -> stop recording, transcribe via OpenAI, type the result
                   into the focused field (or copy to clipboard).

Heavy work on key-up (network + injection) runs on a worker thread so the
evdev listener thread stays responsive.
"""

import os
import shutil
import subprocess
import sys
import threading

import decky

# Our sibling modules ship next to main.py in the plugin dir; make sure they
# import regardless of how decky-loader sets the working dir / sys.path.
PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from evdev_listener import EvdevListener  # noqa: E402
from recorder import Recorder  # noqa: E402
from settings_store import Settings  # noqa: E402
from transcriber import transcribe  # noqa: E402
from uinput_kbd import UInputKeyboard  # noqa: E402

DEFAULTS = {
    "enabled": False,
    "api_key": "",
    "model": "gpt-4o-mini-transcribe",
    "language": "",            # "" = auto-detect
    "ptt_keycode": None,
    "output_mode": "type",     # "type" | "clipboard"
    "mic_device": "",          # "" = default source
    "prompt": "",              # optional vocabulary bias
    "inject_status": True,     # type a placeholder caption while recording
    "ptt_gamepad_button": None,  # Steam controller button id (matched in frontend)
}

# Shown in the focused field while recording, then backspaced away and
# replaced by the transcription. Must be ASCII (so nothing is silently
# skipped, which would throw off the backspace count) and contain no newline
# (our typer maps "\n" to Enter, which would submit it).
STATUS_CAPTION = "[recording...]"


class Plugin:
    # ---------------- lifecycle ----------------
    async def _main(self):
        import asyncio
        decky.logger.info("WhisPTT starting up")
        self.loop = asyncio.get_event_loop()
        self.settings = Settings(
            os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "whisptt.json")
        )
        for k, v in DEFAULTS.items():
            if self.settings.get(k) is None:
                self.settings.set(k, v)

        self.recorder = None
        self.kbd = None
        self.status = "idle"
        self.last_text = ""
        self.busy = False
        self._capture_future = None
        self._caption_chars = 0    # chars of STATUS_CAPTION currently in the field

        self.listener = EvdevListener(
            on_press=self._on_ptt_press,
            on_release=self._on_ptt_release,
            on_capture=self._on_capture,
        )
        self.listener.start()
        if self.settings.get("enabled"):
            code = self.settings.get("ptt_keycode")
            if code is not None:
                self.listener.set_target(int(code))
        decky.logger.info("WhisPTT ready (rec tool: %s)", Recorder().tool)

    async def _unload(self):
        decky.logger.info("WhisPTT unloading")
        self._teardown()

    async def _uninstall(self):
        decky.logger.info("WhisPTT uninstalling")
        self._teardown()

    def _teardown(self):
        for fn in (
            lambda: self.listener and self.listener.stop(),
            lambda: self.recorder and self.recorder.stop(),
            lambda: self.kbd and self.kbd.close(),
        ):
            try:
                fn()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                decky.logger.exception("teardown step failed")

    # ---------------- PTT handlers (listener thread) ----------------
    def _on_ptt_press(self):
        if self.busy:
            return
        try:
            self.recorder = Recorder(device=self.settings.get("mic_device") or None)
            if not self.recorder.available():
                self._set_status("error: no mic tool")
                self.recorder = None
                return
            self.recorder.start()
            self._set_status("recording")
            self._caption_chars = 0
            # Show an in-field caption so you get feedback without the QAM open.
            # Only meaningful when we're typing the result into the field.
            if (self.settings.get("inject_status")
                    and self.settings.get("output_mode") == "type"):
                try:
                    skipped = self._ensure_kbd().type_text(STATUS_CAPTION)
                    self._caption_chars = len(STATUS_CAPTION) - skipped
                except Exception:  # noqa: BLE001
                    decky.logger.exception("status caption inject failed")
                    self._caption_chars = 0
        except Exception as e:  # noqa: BLE001
            decky.logger.exception("record start failed")
            self._set_status("error: " + str(e))
            self.recorder = None

    def _on_ptt_release(self):
        if self.recorder is None:
            return
        threading.Thread(target=self._process_release, daemon=True).start()

    def _process_release(self):
        self.busy = True
        # Capture the caption length up front; clear it before typing the
        # result, and the `finally` mops it up on any error/early-return so we
        # never strand "[recording...]" in the field.
        caption_n = self._caption_chars
        self._caption_chars = 0
        try:
            self._set_status("transcribing")
            rec = self.recorder
            self.recorder = None
            path = rec.stop()
            if not path or not os.path.exists(path) or os.path.getsize(path) < 1024:
                if rec.last_error:
                    decky.logger.warning("recorder produced no audio; stderr: %s",
                                         rec.last_error)
                self._set_status("idle (nothing recorded)")
                return
            api_key = self.settings.get("api_key")
            if not api_key:
                self._set_status("error: no API key set")
                return
            try:
                text = transcribe(
                    path,
                    api_key=api_key,
                    model=self.settings.get("model"),
                    language=self.settings.get("language") or None,
                    prompt=self.settings.get("prompt") or None,
                )
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

            text = (text or "").strip()
            self.last_text = text
            if not text:
                self._set_status("idle (no speech)")
                return

            # Erase the caption, then type/copy the real text.
            if caption_n:
                self._backspace(caption_n)
                caption_n = 0
            if self.settings.get("output_mode") == "type":
                self._type_text(text)
            else:
                self._copy_clipboard(text)
            self._set_status("idle")
        except Exception as e:  # noqa: BLE001
            decky.logger.exception("release processing failed")
            self._set_status("error: " + str(e))
        finally:
            if caption_n:
                try:
                    self._backspace(caption_n)
                except Exception:  # noqa: BLE001
                    decky.logger.exception("caption cleanup failed")
            self.busy = False

    def _on_capture(self, code):
        if self._capture_future is not None and not self._capture_future.done():
            self.loop.call_soon_threadsafe(self._capture_future.set_result, code)
        self._capture_future = None

    # ---------------- output ----------------
    def _ensure_kbd(self):
        # NB: the virtual keyboard is created lazily here, which is always
        # AFTER the evdev listener has enumerated input devices (it starts in
        # _main). So the listener never watches our own device, and injected
        # keystrokes can't be misread as PTT events. Keep it that way.
        if self.kbd is None:
            self.kbd = UInputKeyboard()
        return self.kbd

    def _backspace(self, n):
        self._ensure_kbd().backspace(n)

    def _type_text(self, text):
        skipped = self._ensure_kbd().type_text(text)
        if skipped:
            decky.logger.info("typed transcript, skipped %d non-ASCII char(s)", skipped)

    def _copy_clipboard(self, text):
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
            if shutil.which(cmd[0]):
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"))
                return
        decky.logger.warning("no clipboard tool (wl-copy/xclip) found")

    def _set_status(self, status):
        self.status = status
        decky.logger.info("status: %s", status)

    # ---------------- frontend-callable API ----------------
    async def get_settings(self):
        s = dict(self.settings.data)
        s["has_api_key"] = bool(s.get("api_key"))
        s.pop("api_key", None)
        return s

    async def set_setting(self, key, value):
        self.settings.set(key, value)
        if key == "ptt_keycode" and self.settings.get("enabled"):
            self.listener.set_target(int(value) if value is not None else None)
        return True

    async def set_enabled(self, enabled):
        enabled = bool(enabled)
        self.settings.set("enabled", enabled)
        if enabled:
            code = self.settings.get("ptt_keycode")
            self.listener.set_target(int(code) if code is not None else None)
        else:
            self.listener.set_target(None)
        return True

    async def begin_capture_ptt(self, timeout=10):
        """Block until the next key press; store it as the PTT key. Returns code."""
        import asyncio
        fut = self.loop.create_future()
        self._capture_future = fut
        self.listener.start_capture()
        try:
            code = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.listener.stop_capture()
            self._capture_future = None
            return None
        self.settings.set("ptt_keycode", code)
        if self.settings.get("enabled"):
            self.listener.set_target(code)
        return code

    async def get_status(self):
        return {
            "status": self.status,
            "last_text": self.last_text,
            "enabled": bool(self.settings.get("enabled")),
            "ptt_keycode": self.settings.get("ptt_keycode"),
            "rec_tool": Recorder().tool,
            "busy": self.busy,
        }

    # Triggered from the frontend controller listener (Steam Input API), which
    # detects gamepad buttons that never reach the kernel evdev layer. Drives
    # the same record/transcribe/inject pipeline as the evdev PTT path.
    async def start_dictation(self):
        self._on_ptt_press()
        return True

    async def stop_dictation(self):
        self._on_ptt_release()
        return True
