# Publishing WhisPTT to the Decky store

Checklist and notes for submitting to the Decky plugin store.

## Pre-submission checklist

- [x] `LICENSE` (BSD-3-Clause)
- [x] End-user `README.md`
- [x] `plugin.json` with `name`, `author`, `flags`, and `publish.{tags,description,image}`
- [x] ASCII-typing limitation documented (README → Limitations)
- [x] **Thumbnail** — `assets/thumbnail.svg` (source) + `assets/thumbnail.png`
      (1360x766) added; `plugin.json` `publish.image` points at it. NOTE: 16:9
      was a sensible default — confirm the current Decky image spec and
      re-render if needed (`thumbnail.svg` re-exports to any size).
- [x] **Clean-install test** — effectively covered: after a cold boot the
      plugin loads fresh from the installed files and works (controller hotplug
      path verified from a clean device state).
- [ ] Bump `version` in `package.json` for the release tag if desired.

## Submission process

> ⚠️ Verify the current flow before submitting — the Decky team has changed it
> over time, and this was written without live access to their docs.

The store is backed by the **`SteamDeckHomebrew/decky-plugin-database`** repo:

1. Fork it.
2. Add an entry for WhisPTT following their template / `README` (it references
   this repo + release).
3. Open a PR; their CI builds the plugin and the team reviews it.

Cross-check against the official Decky docs/wiki and the SteamDeckHomebrew
Discord for the up-to-date submission steps and review requirements.

## Review notes (things a reviewer may ask about)

- **Runs as root** (`flags: ["root"]`). Required for `/dev/uinput` (keystroke
  injection) and `/dev/input/event*` (PTT detection). Be ready to justify it.
- **No bundled binaries or native Python deps** — the backend is pure stdlib,
  which should simplify review.
- **Network + third-party API** — audio is sent to OpenAI; the README documents
  this and the API-key handling (stored `0600`, sent only as the auth header).
