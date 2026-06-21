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
  ptt_gamepad_combo: string[];
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

const MODELS = [
  { label: "GPT-4o mini transcribe (fast, cheap)", data: "gpt-4o-mini-transcribe" },
  { label: "GPT-4o transcribe (best accuracy)", data: "gpt-4o-transcribe" },
  { label: "Whisper-1 (legacy)", data: "whisper-1" },
];
const OUTPUT_MODES = [
  { label: "Type into focused field", data: "type" },
  { label: "Copy to clipboard", data: "clipboard" },
];

// Gamepad inputs available for the PTT combo (names match the backend map).
// The chord is read off Steam's virtual X-Box 360 pad while in-game.
const GAMEPAD_BUTTONS = [
  "SELECT", "START", "L1", "R1", "L2", "R2", "L3", "R3", "A", "B", "X", "Y",
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

  const refresh = async () => setS(await getSettings());

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

  const combo = s.ptt_gamepad_combo ?? [];
  const noTrigger = s.ptt_keycode === null && combo.length === 0;

  const toggleComboButton = async (btn: string, on: boolean) => {
    const next = on ? [...combo, btn] : combo.filter((b) => b !== btn);
    await update("ptt_gamepad_combo", next);
  };

  return (
    <>
      <PanelSection title="Dictation">
        <PanelSectionRow>
          <ToggleField
            label="Enabled"
            description={
              noTrigger
                ? "Set a PTT key or controller combo first"
                : "Hold your PTT trigger to dictate"
            }
            checked={s.enabled}
            disabled={noTrigger}
            onChange={async (v) => {
              await setEnabled(v);
              await refresh();
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onSetPtt} disabled={capturing}>
            {capturing
              ? "Press your PTT key now…"
              : `PTT key (keyboard/touch): ${keyLabel(s.ptt_keycode)}  (tap to change)`}
          </ButtonItem>
        </PanelSectionRow>
        {s.ptt_keycode !== null && !capturing ? (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => update("ptt_keycode", null)}>
              Clear PTT key
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          <Field label="Controller combo" focusable={false}>
            {combo.length ? combo.join(" + ") : "none"}
          </Field>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Controller combo (hold all to talk)">
        <PanelSectionRow>
          <div style={{ fontSize: "12px", opacity: 0.7 }}>
            Read off the virtual gamepad in-game. Map one physical button in
            Steam Input to emit this exact combo (e.g. a back grip → Select+R3);
            pick a combo no game uses so it stays conflict-free.
          </div>
        </PanelSectionRow>
        {GAMEPAD_BUTTONS.map((b) => (
          <PanelSectionRow key={b}>
            <ToggleField
              label={b}
              checked={combo.includes(b)}
              onChange={(v) => toggleComboButton(b, v)}
            />
          </PanelSectionRow>
        ))}
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

export default definePlugin(() => ({
  name: "WhisPTT",
  titleView: <div className={staticClasses.Title}>WhisPTT</div>,
  content: <Content />,
  icon: <FaMicrophone />,
}));
