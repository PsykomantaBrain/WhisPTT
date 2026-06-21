import {
  ButtonItem,
  Dropdown,
  Field,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useState } from "react";
import { FaMicrophone } from "react-icons/fa";

// ---- backend bridge ----
interface BackendSettings {
  enabled: boolean;
  model: string;
  language: string;
  ptt_keycode: number | null;
  output_mode: "type" | "clipboard";
  mic_device: string;
  prompt: string;
  inject_status: boolean;
  ptt_gamepad_button: number | null;
  has_api_key: boolean;
}
interface Status {
  status: string;
  last_text: string;
  enabled: boolean;
  ptt_keycode: number | null;
  rec_tool: string | null;
  busy: boolean;
}

const getSettings = callable<[], BackendSettings>("get_settings");
const setSetting = callable<[string, unknown], boolean>("set_setting");
const setEnabled = callable<[boolean], boolean>("set_enabled");
const beginCapturePtt = callable<[number?], number | null>("begin_capture_ptt");
const getStatus = callable<[], Status>("get_status");
const startDictation = callable<[], boolean>("start_dictation");
const stopDictation = callable<[], boolean>("stop_dictation");

// Friendly names for known Steam controller button ids (others show as #N).
const GAMEPAD_NAMES: Record<number, string> = { 44: "L5" };
function padLabel(btn: number | null): string {
  if (btn === null || btn === undefined) return "not set";
  return GAMEPAD_NAMES[btn] ?? `button #${btn}`;
}

// Controller PTT via Steam's own input API. Steam Input button presses never
// reach the kernel evdev layer (Gamescope handles them above it), so we listen
// through SteamClient.Input instead. Registered at PLUGIN scope (see
// definePlugin) so it keeps listening while the panel is closed / in-game.
class ControllerPTT {
  registered = false;
  private reg: { unregister?: () => void } | null = null;
  button: number | null = null;   // configured PTT button id
  enabled = false;
  private held = false;
  capturing = false;
  onCapture: ((btn: number) => void) | null = null;

  register() {
    if (this.registered) return;
    try {
      const input = (window as unknown as { SteamClient?: any }).SteamClient?.Input;
      if (!input?.RegisterForControllerInputMessages) {
        console.error("[WhisPTT] SteamClient.Input.RegisterForControllerInputMessages not found");
        return;
      }
      this.reg = input.RegisterForControllerInputMessages(
        (_idx: number, button: number, pressed: boolean) => this.onButton(button, pressed),
      );
      this.registered = true;
      console.log("[WhisPTT] controller input registered");
    } catch (e) {
      console.error("[WhisPTT] controller register failed", e);
    }
  }

  unregister() {
    try {
      this.reg?.unregister?.();
    } catch {
      /* ignore */
    }
    this.reg = null;
    this.registered = false;
  }

  private onButton(button: number, pressed: boolean) {
    if (this.capturing) {
      if (pressed) {
        this.capturing = false;
        this.button = button;
        this.onCapture?.(button);
      }
      return;
    }
    if (!this.enabled || this.button === null || button !== this.button) return;
    if (pressed && !this.held) {
      this.held = true;
      startDictation();
    } else if (!pressed && this.held) {
      this.held = false;
      stopDictation();
    }
  }
}
const controller = new ControllerPTT();

const MODELS = [
  { label: "GPT-4o mini transcribe (fast, cheap)", data: "gpt-4o-mini-transcribe" },
  { label: "GPT-4o transcribe (best accuracy)", data: "gpt-4o-transcribe" },
  { label: "Whisper-1 (legacy)", data: "whisper-1" },
];
const OUTPUT_MODES = [
  { label: "Type into focused field", data: "type" },
  { label: "Copy to clipboard", data: "clipboard" },
];

// A few friendly names for common Linux keycodes; fall back to the number.
const KEYNAMES: Record<number, string> = {
  29: "L-Ctrl", 97: "R-Ctrl", 42: "L-Shift", 54: "R-Shift",
  56: "L-Alt", 100: "R-Alt", 125: "L-Meta", 57: "Space",
  15: "Tab", 58: "CapsLock", 119: "Pause", 87: "F11", 88: "F12",
};
function keyLabel(code: number | null): string {
  if (code === null || code === undefined) return "not set";
  return KEYNAMES[code] ?? `key #${code}`;
}

function Content() {
  const [s, setS] = useState<BackendSettings | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [capturingPad, setCapturingPad] = useState(false);

  const refresh = async () => {
    const next = await getSettings();
    // Keep the plugin-scope controller listener in sync with settings.
    controller.enabled = next.enabled;
    controller.button = next.ptt_gamepad_button;
    setS(next);
  };

  useEffect(() => {
    refresh();
    const t = setInterval(async () => setStatus(await getStatus()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!s) {
    return (
      <PanelSection title="WhisPTT">
        <PanelSectionRow>Loading…</PanelSectionRow>
      </PanelSection>
    );
  }

  const update = async (key: string, value: unknown) => {
    await setSetting(key, value);
    await refresh();
  };

  const onSetPtt = async () => {
    setCapturing(true);
    try {
      await beginCapturePtt(10);
    } finally {
      setCapturing(false);
      await refresh();
    }
  };

  const onSetPad = () => {
    setCapturingPad(true);
    controller.capturing = true;
    controller.onCapture = (btn) => {
      controller.onCapture = null;
      setCapturingPad(false);
      void update("ptt_gamepad_button", btn);
    };
    // Give up if no button is pressed within 10s.
    setTimeout(() => {
      if (controller.capturing) {
        controller.capturing = false;
        controller.onCapture = null;
        setCapturingPad(false);
      }
    }, 10000);
  };

  return (
    <>
      <PanelSection title="Dictation">
        <PanelSectionRow>
          <ToggleField
            label="Enabled"
            description={
              s.ptt_keycode === null && s.ptt_gamepad_button === null
                ? "Set a PTT key or controller button first"
                : "Hold your PTT trigger to dictate"
            }
            checked={s.enabled}
            disabled={s.ptt_keycode === null && s.ptt_gamepad_button === null}
            onChange={async (v) => {
              await setEnabled(v);
              await refresh();
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={onSetPtt}
            disabled={capturing}
          >
            {capturing
              ? "Press your PTT key now…"
              : `PTT key (keyboard/touch): ${keyLabel(s.ptt_keycode)}  (tap to change)`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={onSetPad}
            disabled={capturingPad}
          >
            {capturingPad
              ? "Press a controller button now…"
              : `PTT controller button: ${padLabel(s.ptt_gamepad_button)}  (tap to set)`}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Status">
        <PanelSectionRow>
          <Field label="State" focusable={false}>
            {status?.status ?? "—"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Mic" focusable={false}>
            {status?.rec_tool ?? "none found"}
          </Field>
        </PanelSectionRow>
        {status?.last_text ? (
          <PanelSectionRow>
            <Field label="Last" focusable={false}>
              {status.last_text}
            </Field>
          </PanelSectionRow>
        ) : null}
      </PanelSection>

      <PanelSection title="Settings">
        <PanelSectionRow>
          <Field label="OpenAI API key" focusable={false}>
            {s.has_api_key ? "saved ✓" : "not set"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <TextField
            label="Set / replace API key"
            bIsPassword
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!apiKey}
            onClick={async () => {
              await update("api_key", apiKey);
              setApiKey("");
            }}
          >
            Save API key
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Model" focusable={false}>
            <Dropdown
              rgOptions={MODELS}
              selectedOption={s.model}
              onChange={(o) => update("model", o.data)}
            />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Output" focusable={false}>
            <Dropdown
              rgOptions={OUTPUT_MODES}
              selectedOption={s.output_mode}
              onChange={(o) => update("output_mode", o.data)}
            />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Type status caption"
            description="Shows [recording...] in the field, then replaces it with the transcription (type mode only)"
            checked={s.inject_status}
            disabled={s.output_mode !== "type"}
            onChange={(v) => update("inject_status", v)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <TextField
            label="Language (ISO code, blank = auto)"
            value={s.language}
            onChange={(e) => update("language", e.target.value)}
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  // Register at plugin scope so controller PTT works with the panel closed.
  controller.register();
  getSettings()
    .then((s) => {
      controller.enabled = s.enabled;
      controller.button = s.ptt_gamepad_button;
    })
    .catch(() => {});

  return {
    name: "WhisPTT",
    titleView: <div className={staticClasses.Title}>WhisPTT</div>,
    content: <Content />,
    icon: <FaMicrophone />,
    onDismount() {
      controller.unregister();
    },
  };
});
