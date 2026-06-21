# WhisPTT

Push-to-talk voice dictation for the Steam Deck. Hold a button, speak, release —
your words are transcribed by the OpenAI Whisper API and typed straight into the
focused field.

## How it works

```
PTT key down ──► record mic (pw-record → 16kHz mono WAV)
PTT key up   ──► stop ──► POST to OpenAI /v1/audio/transcriptions ──► type keystrokes
```

- **Frontend** (`src/index.tsx`, `@decky/ui` + React): Quick Access panel —
  enable toggle, PTT-key capture, model / output / language settings, live status.
- **Backend** (`main.py` + sibling modules, runs as **root**):
  - `evdev_listener.py` — watches `/dev/input/event*` globally for the PTT key
    (press/release), below Gamescope, no matter what's focused. Pure Python.
  - `recorder.py` — `pw-record` → `parecord` → `arecord` fallback chain.
  - `transcriber.py` — hand-rolled multipart POST to OpenAI (stdlib only).
  - `uinput_kbd.py` — virtual keyboard via `/dev/uinput` to inject keystrokes.
    Pure Python (struct + ioctl) — **nothing to compile for SteamOS**.

The PTT key is whatever you bind in **Steam Input** (e.g. a controller chord →
a keyboard key). WhisPTT just watches that one keycode. Opening chat / submitting
text stays per-game in your Steam Input layout — WhisPTT only produces the text.

## Build

```bash
pnpm install
pnpm run build      # emits dist/index.js
```

## Deploy to the Deck

Copy the plugin folder to `~/homebrew/plugins/WhisPTT/` on the Deck (it needs
`plugin.json`, `dist/`, `main.py`, and the `*.py` modules), then restart Decky
(or use the Decky CLI / VS Code "deploy" task). The backend runs as root because
`plugin.json` declares `"flags": ["root"]` — required for `/dev/uinput` and
`/dev/input/event*`.

## First run

1. Open the WhisPTT panel in the Quick Access Menu.
2. **Save API key** — your OpenAI key (stored in the plugin's settings dir, never
   committed). Get one at platform.openai.com.
3. **PTT key → tap to change**, then press the key you bound in Steam Input.
4. Flip **Enabled** on. Hold the key, speak, release.

## ⚠️ Validate on hardware — the risky bits

Two things only real Deck hardware can confirm (everything else is deterministic):

1. **Keystroke injection vs Steam Input.** Injected uinput keys land in games
   (SDL/evdev read them fine). Steam's own CEF search boxes can be flaky, and
   Steam Input *might* try to remap our virtual keyboard. If typing misbehaves,
   switch **Output → Copy to clipboard** as a fallback (set in the panel).
2. **PTT detection latency / double-fire.** Reading is passive, so the game still
   sees the key — dedicate a binding you don't otherwise use in-game.

Watch the backend log while testing:

```bash
tail -f ~/homebrew/logs/WhisPTT/plugin.log   # path may vary by Decky version
```

## Notes / limitations

- Injection is US-layout, printable ASCII. Non-ASCII characters are skipped
  (uinput is keycode-based). Fine for English; clipboard mode preserves anything.
- The listener reads all input devices while loaded (inherent to a global PTT).
- Needs network + an OpenAI key. `gpt-4o-mini-transcribe` is the cheap default.
