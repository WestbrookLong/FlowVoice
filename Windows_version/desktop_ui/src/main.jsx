import React from "react";
import { createPortal } from "react-dom";
import { createRoot } from "react-dom/client";
import { QRCodeSVG } from "qrcode.react";
import "./styles.css";

const fallbackState = {
  running: false,
  token: "loading",
  ip: "127.0.0.1",
  port: "8787",
  url: "",
  status: "SERVICE STOPPED",
  connectionMode: "local",
  publicConnection: {
    running: false,
    status: "stopped",
    url: "",
    error: null,
  },
  inputGate: {
    paused: false,
    label: "Alt+M",
    version: 0,
  },
  inputGateMode: "pause",
  tapVoiceActive: false,
  inputGateHotkey: {
    registered: false,
    error: null,
    label: "Alt+M",
  },
  typingStats: {
    allTime: { total: 0, mobile: 0, computer: 0 },
    today: { total: 0, mobile: 0, computer: 0 },
    week: { total: 0, mobile: 0, computer: 0 },
    month: { total: 0, mobile: 0, computer: 0 },
    history: [],
  },
};

function desktopApi() {
  return window.pywebview?.api;
}

function FlowVoiceDesktopConsole() {
  const [state, setState] = React.useState(fallbackState);
  const [message, setMessage] = React.useState("");
  const [typingStatsOpen, setTypingStatsOpen] = React.useState(false);
  const [capturingHotkey, setCapturingHotkey] = React.useState(false);
  const [singleKeyCapture, setSingleKeyCapture] = React.useState({ key: "", progress: 0 });
  const refreshInFlight = React.useRef(false);

  const ip = state.ip;
  const port = state.port;
  const token = state.token;
  const url = state.url || `http://${ip}:${port}/?token=${token}&v=voice`;
  const inputGate = state.inputGate || fallbackState.inputGate;
  const inputGateHotkey = state.inputGateHotkey || fallbackState.inputGateHotkey;
  const inputGateMode = state.inputGateMode || "pause";
  const tapVoiceActive = Boolean(state.tapVoiceActive);
  const typingStats = state.typingStats || fallbackState.typingStats;
  const publicConnection = state.publicConnection || fallbackState.publicConnection;
  const connectionMode = state.connectionMode || "local";

  const refresh = React.useCallback(async () => {
    const api = desktopApi();
    if (!api || refreshInFlight.current) {
      return;
    }
    refreshInFlight.current = true;
    try {
      const next = await api.get_state();
      setState((previous) => ({ ...previous, ...next }));
    } finally {
      refreshInFlight.current = false;
    }
  }, []);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    let started = false;
    const interval = 650;
    const poll = async () => {
      await refresh();
      if (!cancelled) {
        timer = window.setTimeout(poll, interval);
      }
    };
    const ready = () => {
      if (started) {
        return;
      }
      started = true;
      poll();
    };
    window.addEventListener("pywebviewready", ready);
    if (desktopApi()) {
      ready();
    }
    return () => {
      cancelled = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
      window.removeEventListener("pywebviewready", ready);
    };
  }, [refresh]);

  React.useEffect(() => {
    if (!capturingHotkey) {
      return undefined;
    }
    let holdTimer = null;
    let progressTimer = null;
    let candidateId = "";

    const clearSingleKeyHold = () => {
      if (holdTimer !== null) window.clearTimeout(holdTimer);
      if (progressTimer !== null) window.clearInterval(progressTimer);
      holdTimer = null;
      progressTimer = null;
      candidateId = "";
      setSingleKeyCapture({ key: "", progress: 0 });
    };

    const submitHotkey = async (event, singleKey = false) => {
      clearSingleKeyHold();
      setCapturingHotkey(false);
      await callApi("set_input_gate_hotkey", {
        key: event.key,
        code: event.code,
        ctrlKey: event.ctrlKey,
        altKey: event.altKey,
        shiftKey: event.shiftKey,
        metaKey: event.metaKey,
        singleKey,
      });
    };

    const handleKeyDown = async (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape") {
        clearSingleKeyHold();
        setCapturingHotkey(false);
        setMessage("Hotkey capture cancelled.");
        return;
      }
      const modifierOnly = ["Control", "Alt", "Shift", "Meta"].includes(event.key);
      const hasModifier = event.ctrlKey || event.altKey || event.shiftKey || event.metaKey;
      if (hasModifier && !modifierOnly) {
        await submitHotkey(event);
        return;
      }
      if (event.repeat) return;

      clearSingleKeyHold();
      candidateId = event.code || event.key;
      const capturedEvent = {
        key: event.key,
        code: event.code,
        ctrlKey: event.ctrlKey,
        altKey: event.altKey,
        shiftKey: event.shiftKey,
        metaKey: event.metaKey,
      };
      const startedAt = window.performance.now();
      setSingleKeyCapture({ key: event.key, progress: 0 });
      setMessage(`Keep holding ${event.key} for 3 seconds.`);
      progressTimer = window.setInterval(() => {
        const progress = Math.min(100, ((window.performance.now() - startedAt) / 3000) * 100);
        setSingleKeyCapture({ key: event.key, progress });
      }, 50);
      holdTimer = window.setTimeout(() => submitHotkey(capturedEvent, true), 3000);
    };

    const handleKeyUp = (event) => {
      event.preventDefault();
      event.stopPropagation();
      if ((event.code || event.key) === candidateId && holdTimer !== null) {
        clearSingleKeyHold();
        setMessage("Single key released before 3 seconds.");
      }
    };
    window.addEventListener("keydown", handleKeyDown, true);
    window.addEventListener("keyup", handleKeyUp, true);
    return () => {
      clearSingleKeyHold();
      window.removeEventListener("keydown", handleKeyDown, true);
      window.removeEventListener("keyup", handleKeyUp, true);
    };
  }, [capturingHotkey]);

  async function callApi(action, payload) {
    const api = desktopApi();
    if (!api) {
      setMessage("Desktop API is not ready.");
      return;
    }
    try {
      const method = api[action];
      const result = payload === undefined ? await method() : await method(payload);
      if (result?.state) {
        setState((previous) => ({ ...previous, ...result.state }));
      }
      setMessage(result.message || "");
    } catch (error) {
      setMessage(`Desktop API error: ${error?.message || error}`);
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#050807] text-[#DDE7DF]">
      <div className="pointer-events-none fixed inset-0">
        <div className="absolute left-[-120px] top-[-120px] h-[460px] w-[460px] rounded-full bg-[#0CFF88]/10 blur-[90px]" />
        <div className="absolute bottom-[-180px] right-[-120px] h-[560px] w-[560px] rounded-full bg-[#1FA463]/18 blur-[110px]" />
        <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(10,255,136,0.08),transparent_34%,rgba(9,21,16,0.95)_70%)]" />
        <div className="absolute inset-0 opacity-[0.045] [background-image:linear-gradient(rgba(255,255,255,.6)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.6)_1px,transparent_1px)] [background-size:36px_36px]" />
      </div>

      <div className="relative z-10 flex min-h-0 w-full flex-col">
        <CustomTitleBar
          onMinimize={() => callApi("minimize_window")}
          onMaximize={() => callApi("toggle_maximize_window")}
          onClose={() => callApi("close_window")}
        />

      <main className="relative mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col overflow-y-auto px-8 py-5">
        <header className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="grid h-11 w-11 place-items-center rounded-xl border border-[#28F58D]/25 bg-[#0B1D14] font-mono text-sm font-black text-[#80FFBA] shadow-[0_0_32px_rgba(40,245,141,0.15)]">
              <HurricaneEyeIcon />
            </div>
            <div>
              <div className="font-mono text-[11px] uppercase tracking-[0.35em] text-[#74E7A5]/70">Flow Voice</div>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[#F0FFF5]">Desktop Connection Console</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="font-mono text-xs text-[#5B7062]">{ip}:{port}</span>
            <InputGateBadge paused={inputGate.paused} label={inputGateHotkey.label || inputGate.label} />
            <ServiceBadge running={state.running} />
          </div>
        </header>

        <section className="grid min-h-0 flex-1 grid-cols-12 gap-5">
          <div className="col-span-12 rounded-[26px] border border-[#1E3B2B] bg-[#08100D]/88 p-6 shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl lg:col-span-7">
            {message && (
              <div className="mb-4 rounded-2xl border border-[#21462F] bg-[#06100B]/80 px-4 py-3 text-xs leading-5 text-[#A8F7C4]">
                {message}
              </div>
            )}

            <div className="flex h-full min-h-[520px] flex-col">
              <div className="flex items-start justify-between gap-5">
                <div>
                  <div className="font-mono text-[11px] uppercase tracking-[0.32em] text-[#74E7A5]/70">Mobile Input</div>
                  <h2 className="mt-3 text-3xl font-semibold tracking-tight text-[#F2FFF7]">Quick Connect</h2>
                  <p className="mt-2 max-w-xl text-sm leading-6 text-[#8EA99A]">
                    Scan the QR code from your phone, then use mobile voice input to write into the active cursor on this desktop.
                  </p>
                </div>
                <div className="hidden rounded-2xl border border-[#21462F] bg-[#06100B] px-4 py-3 text-right lg:block">
                  <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#5B7062]">
                    {connectionMode === "public" ? "Public" : "Local"}
                  </div>
                  <div className="mt-1 max-w-[210px] truncate font-mono text-sm font-semibold text-[#B9FFD4]">
                    {connectionMode === "public" && publicConnection.url ? publicConnection.url.replace(/^https?:\/\//, "") : `${ip}:${port}`}
                  </div>
                </div>
              </div>

              <div className="mt-8 grid flex-1 items-center gap-7 md:grid-cols-[minmax(220px,280px)_1fr]">
                <div className="mx-auto w-full max-w-[260px] rounded-[24px] border border-[#2C6241] bg-gradient-to-br from-[#153321] to-[#06100B] p-3 shadow-[0_0_58px_rgba(40,245,141,0.13)]">
                  <div className="rounded-[18px] bg-white p-3">
                    <QrCode value={url} />
                  </div>
                </div>

                <div className="min-w-0 space-y-4">
                  <div className="rounded-2xl border border-[#21462F] bg-[#06100B] p-4">
                    <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Bound Session</div>
                    <code className="block truncate font-mono text-sm text-[#B9FFD4]">{token}</code>
                    <div className="mt-3 rounded-xl border border-[#193324] bg-[#050C08]/80 px-3 py-2">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#5B7062]">
                          {connectionMode === "public" ? "Public Tunnel" : "Local Network"}
                        </span>
                        <span className={`h-2 w-2 rounded-full ${connectionMode === "public" && publicConnection.running ? "bg-[#28F58D] shadow-[0_0_12px_rgba(40,245,141,0.8)]" : "bg-[#5B7062]"}`} />
                      </div>
                      <div className="mt-1 truncate font-mono text-xs text-[#7FA98E]">
                        {connectionMode === "public"
                          ? publicConnection.url || publicConnection.status || publicConnection.error || "starting"
                          : `${ip}:${port}`}
                      </div>
                    </div>
                    <div className="mt-4 h-px bg-[#193324]" />
                    <div className="mt-4 grid grid-cols-2 gap-3">
                      {state.running ? (
                        <button onClick={() => callApi("stop_service")} className="rounded-xl border border-[#285C3B] bg-[#0C1E14] py-3 text-sm font-semibold text-[#A8F7C4] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition hover:bg-[#12301F]">
                          Stop Service
                        </button>
                      ) : (
                        <button onClick={() => callApi("start_service")} className="rounded-xl bg-[#28F58D] py-3 text-sm font-bold text-[#041008] shadow-[0_0_24px_rgba(40,245,141,0.2)] transition hover:bg-[#67FFAD]">
                          Start Service
                        </button>
                      )}
                      <button onClick={() => callApi("refresh_connection")} className="rounded-xl border border-[#2E7447] bg-[#10291B] py-3 text-sm font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
                        Refresh
                      </button>
                      {connectionMode === "public" && publicConnection.running ? (
                        <button onClick={() => callApi("stop_public_service")} className="rounded-xl border border-[#6A5A20] bg-[#211C0B] py-3 text-sm font-semibold text-[#D7C47A] transition hover:bg-[#2A230D]">
                          Stop Public
                        </button>
                      ) : (
                        <button onClick={() => callApi("start_public_service")} className="rounded-xl border border-[#2E7447] bg-[#10291B] py-3 text-sm font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
                          Public Connect
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-[#193324] bg-[#050C08]/70 px-4 py-3 text-xs leading-5 text-[#7FA98E]">
                    Token included in the QR code. Public Connect uses Cloudflare Tunnel so the phone does not need to share the same Wi-Fi.
                  </div>
                </div>
              </div>
            </div>
          </div>

          <aside className="col-span-12 flex min-h-0 flex-col gap-5 lg:col-span-5">
            <button
              type="button"
              onClick={() => setTypingStatsOpen(true)}
              className="rounded-[26px] border border-[#1E3B2B] bg-[#08100D]/88 p-6 text-left shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl transition hover:border-[#2E7447] hover:bg-[#0A1510]"
            >
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-mono text-[11px] uppercase tracking-[0.28em] text-[#74E7A5]/70">Typing Activity</div>
                  <h2 className="mt-2 text-2xl font-semibold text-[#F2FFF7]">输入统计</h2>
                  <p className="mt-1 text-sm text-[#7FA98E]">普通输入字数，不统计纪要模式</p>
                </div>
                <span className="text-3xl text-[#7FA98E]" aria-hidden="true">›</span>
              </div>
              <div className="mt-7 grid grid-cols-4 gap-4">
                <TypingStatValue label="总量" value={typingStats.allTime?.total || 0} />
                <TypingStatValue label="本日" value={typingStats.today?.total || 0} />
                <TypingStatValue label="本周" value={typingStats.week?.total || 0} />
                <TypingStatValue label="本月" value={typingStats.month?.total || 0} />
              </div>
            </button>

            <div
              role="button"
              tabIndex={0}
              onClick={() => inputGateMode === "pause" && callApi("toggle_input_pause")}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  if (inputGateMode === "pause") callApi("toggle_input_pause");
                }
              }}
              className={`rounded-[26px] border p-6 text-left shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl transition ${
                inputGate.paused
                  ? "border-[#6A5A20] bg-[#211C0B]/90 hover:bg-[#2A230D]"
                  : "border-[#1E3B2B] bg-[#08100D]/88 hover:border-[#2E7447] hover:bg-[#0A1510]"
              }`}
            >
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-mono text-[11px] uppercase tracking-[0.28em] text-[#74E7A5]/70">Input Gate</div>
                  <h2 className={`mt-2 text-2xl font-semibold ${inputGate.paused ? "text-[#D7C47A]" : "text-[#F2FFF7]"}`}>
                    {inputGateMode === "voice_hold"
                      ? "Hold Voice Ready"
                      : inputGateMode === "tap_voice"
                        ? tapVoiceActive
                          ? "Tap Voice Active"
                          : "Tap Voice Ready"
                        : inputGate.paused
                          ? "Input Paused"
                          : "Input Active"}
                  </h2>
                  <p className="mt-1 text-sm text-[#7FA98E]">Hotkey {inputGateHotkey.label || inputGate.label}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`h-4 w-4 rounded-full ${inputGate.paused ? "bg-[#D7C47A] shadow-[0_0_18px_rgba(215,196,122,0.75)]" : tapVoiceActive ? "bg-[#63D8FF] shadow-[0_0_18px_rgba(99,216,255,0.8)]" : "bg-[#28F58D] shadow-[0_0_18px_rgba(40,245,141,0.75)]"}`} />
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(event) => {
                      event.stopPropagation();
                      setCapturingHotkey(true);
                      setMessage("Press the new hotkey. Press Esc to cancel.");
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        event.stopPropagation();
                        setCapturingHotkey(true);
                        setMessage("Press the new hotkey. Press Esc to cancel.");
                      }
                    }}
                    className="rounded-xl border border-[#2E7447] bg-[#10291B] px-3 py-2 text-xs font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]"
                  >
                    Change
                  </span>
                </div>
              </div>
              <div
                className="mt-5 grid grid-cols-3 rounded-xl border border-[#234531] bg-[#050A07] p-1"
                onClick={(event) => event.stopPropagation()}
              >
                <button
                  type="button"
                  onClick={() => callApi("set_input_gate_mode", "pause")}
                  className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${
                    inputGateMode === "pause" ? "bg-[#163A26] text-[#B9FFD4]" : "text-[#6F8D79] hover:text-[#A8F7C4]"
                  }`}
                >
                  Pause Input
                </button>
                <button
                  type="button"
                  onClick={() => callApi("set_input_gate_mode", "voice_hold")}
                  className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${
                    inputGateMode === "voice_hold" ? "bg-[#163A26] text-[#B9FFD4]" : "text-[#6F8D79] hover:text-[#A8F7C4]"
                  }`}
                >
                  Hold Voice
                </button>
                <button
                  type="button"
                  onClick={() => callApi("set_input_gate_mode", "tap_voice")}
                  className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${
                    inputGateMode === "tap_voice" ? "bg-[#163A26] text-[#B9FFD4]" : "text-[#6F8D79] hover:text-[#A8F7C4]"
                  }`}
                >
                  Tap Voice
                </button>
              </div>
            </div>

            <div className="mt-auto rounded-[26px] border border-[#2F2A17] bg-[#161308]/75 px-5 py-4 text-sm leading-6 text-[#D7C47A]">
              To control elevated windows, run the client with administrator privileges.
            </div>
          </aside>
        </section>
      </main>
      {typingStatsOpen && (
        <TypingStatsPage
          stats={typingStats}
          onClose={() => setTypingStatsOpen(false)}
        />
      )}
      {capturingHotkey && (
        <HotkeyCaptureOverlay
          singleKeyCapture={singleKeyCapture}
          onCancel={() => setCapturingHotkey(false)}
        />
      )}
      </div>
    </div>
  );
}

function HotkeyCaptureOverlay({ onCancel, singleKeyCapture }) {
  return createPortal(
    <div className="fixed inset-0 z-[120] grid place-items-center bg-[#020503]/78 backdrop-blur-sm">
      <div className="w-[min(460px,calc(100vw-48px))] rounded-[26px] border border-[#2E7447] bg-[#08100D] p-7 text-center shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
        <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-[#74E7A5]/70">Input Gate Hotkey</div>
        <h2 className="mt-3 text-2xl font-semibold text-[#F2FFF7]">请按下快捷键</h2>
        <p className="mt-3 text-sm leading-6 text-[#8EA99A]">
          组合键会立即保存；单个按键需要持续按住 3 秒。若快捷键已被系统占用，会自动保留原设置。
        </p>
        {singleKeyCapture?.key && (
          <div className="mt-5">
            <div className="mb-2 font-mono text-xs text-[#B9FFD4]">
              {singleKeyCapture.key} · {Math.floor(singleKeyCapture.progress / 33.34) + 1}/3s
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-[#102019]">
              <div
                className="h-full rounded-full bg-[#28F58D] transition-[width] duration-75"
                style={{ width: `${singleKeyCapture.progress}%` }}
              />
            </div>
          </div>
        )}
        <button
          type="button"
          onClick={onCancel}
          className="mt-6 rounded-xl border border-[#285C3B] bg-[#0C1E14] px-5 py-3 text-sm font-semibold text-[#A8F7C4] transition hover:bg-[#12301F]"
        >
          Cancel
        </button>
      </div>
    </div>,
    document.body,
  );
}

function CustomTitleBar({ onMinimize, onMaximize, onClose }) {
  return (
    <div className="relative z-20 flex h-11 shrink-0 items-center border-b border-[#173524] bg-[#050807]/96 shadow-[0_1px_0_rgba(40,245,141,0.08)]">
      <div className="flex h-full items-center gap-3 px-4">
        <div className="grid h-7 w-7 place-items-center rounded-lg border border-[#28F58D]/25 bg-[#0B1D14] font-mono text-[10px] font-black text-[#80FFBA] shadow-[0_0_18px_rgba(40,245,141,0.12)]">
          <HurricaneEyeIcon compact />
        </div>
        <div className="font-mono text-[11px] uppercase tracking-[0.28em] text-[#8BFFBA]/75">Flow Voice</div>
      </div>
      <div className="pywebview-drag-region h-full min-w-0 flex-1" />
      <div className="flex h-full items-center border-l border-[#173524]">
        <button
          type="button"
          onClick={onMinimize}
          className="grid h-full w-12 place-items-center text-lg leading-none text-[#7EA88E] transition hover:bg-[#10271B] hover:text-[#B9FFD4]"
          aria-label="Minimize"
          title="Minimize"
        >
          -
        </button>
        <button
          type="button"
          onClick={onMaximize}
          className="grid h-full w-12 place-items-center text-[15px] leading-none text-[#7EA88E] transition hover:bg-[#10271B] hover:text-[#B9FFD4]"
          aria-label="Maximize"
          title="Maximize / Restore"
        >
          □
        </button>
        <button
          type="button"
          onClick={onClose}
          className="grid h-full w-12 place-items-center text-lg leading-none text-[#7EA88E] transition hover:bg-[#3A1616] hover:text-[#FFD9D9]"
          aria-label="Close"
          title="Close"
        >
          ×
        </button>
      </div>
    </div>
  );
}

function ServiceBadge({ running }) {
  return (
    <div className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2 font-mono text-xs font-semibold uppercase tracking-[0.12em] shadow-[0_0_26px_rgba(40,245,141,0.15)] ${running ? "border-[#28F58D]/35 bg-[#0D2A19] text-[#8BFFBA]" : "border-[#285C3B] bg-[#0C1E14] text-[#6C8A75]"}`}>
      <span className={`h-2 w-2 rounded-full ${running ? "bg-[#28F58D] shadow-[0_0_14px_rgba(40,245,141,0.9)]" : "bg-[#5B7062]"}`} />
      {running ? "Service Started" : "Service Stopped"}
    </div>
  );
}

function InputGateBadge({ paused, label }) {
  return (
    <div className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2 font-mono text-xs font-semibold uppercase tracking-[0.12em] ${paused ? "border-[#6A5A20] bg-[#211C0B] text-[#D7C47A]" : "border-[#285C3B] bg-[#0C1E14] text-[#6C8A75]"}`}>
      <span className={`h-2 w-2 rounded-full ${paused ? "bg-[#D7C47A] shadow-[0_0_14px_rgba(215,196,122,0.75)]" : "bg-[#28F58D]"}`} />
      {paused ? "Input Paused" : "Input Active"}
      <span className="hidden text-[#5B7062] xl:inline">{label}</span>
    </div>
  );
}

function TypingStatValue({ label, value }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[9px] uppercase tracking-[0.14em] text-[#5B7062]">{label}</div>
      <div className="mt-1 truncate font-mono text-lg font-semibold text-[#B9FFD4]">{formatStatNumber(value)}</div>
    </div>
  );
}

function TypingStatsPage({ stats, onClose }) {
  const history = Array.isArray(stats.history) ? stats.history : [];
  const recentThirtyDays = history.slice(-30);
  const recentFourteenDays = history.slice(-14).reverse();
  const maximum = Math.max(1, ...recentThirtyDays.map((item) => Number(item.total) || 0));

  return createPortal(
    <div className="fixed inset-0 z-[100] isolate overflow-hidden bg-[#050807] text-[#DDE7DF]">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute right-[-140px] top-[-160px] h-[420px] w-[420px] rounded-full bg-[#28F58D]/8 blur-[90px]" />
        <div className="absolute inset-0 opacity-[0.04] [background-image:linear-gradient(rgba(255,255,255,.6)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.6)_1px,transparent_1px)] [background-size:36px_36px]" />
      </div>

      <div className="relative mx-auto flex h-full max-w-5xl flex-col px-8 py-7">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-[0.32em] text-[#74E7A5]/70">Typing Activity</div>
            <h2 className="mt-2 text-3xl font-semibold text-[#F2FFF7]">输入统计</h2>
            <p className="mt-1 text-sm text-[#789484]">仅统计 FlowVoice 实际输入到光标的字符，不包含空格、换行和纪要模式。</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-10 w-10 place-items-center rounded-xl border border-[#285C3B] bg-[#0C1E14] text-xl text-[#A8F7C4] transition hover:bg-[#12301F]"
            aria-label="关闭输入统计"
            title="关闭"
          >
            ×
          </button>
        </div>

        <div className="grid grid-cols-4 border-y border-[#193324]">
          <TypingPeriodSummary label="总量" values={stats.allTime} />
          <TypingPeriodSummary label="本日" values={stats.today} />
          <TypingPeriodSummary label="本周" values={stats.week} />
          <TypingPeriodSummary label="本月" values={stats.month} />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pt-6">
          <section>
            <div className="mb-4 flex items-end justify-between">
              <div>
                <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[#5B7062]">Last 30 Days</div>
                <h3 className="mt-1 text-lg font-semibold text-[#E8FFF0]">每日输入趋势</h3>
              </div>
              <div className="flex items-center gap-4 text-xs text-[#789484]">
                <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#28F58D]" />手机输入</span>
                <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#D7C47A]" />电脑语音</span>
              </div>
            </div>

            <div className="grid h-48 grid-cols-[repeat(30,minmax(0,1fr))] items-end gap-1 border-b border-[#21462F] px-1 pb-1">
              {recentThirtyDays.map((item) => {
                const mobileHeight = Math.max(0, (Number(item.mobile) || 0) / maximum * 100);
                const computerHeight = Math.max(0, (Number(item.computer) || 0) / maximum * 100);
                return (
                  <div
                    key={item.date}
                    className="flex h-full min-w-0 flex-col justify-end"
                    title={`${formatStatDate(item.date)}：${formatStatNumber(item.total)} 字`}
                  >
                    <div className="w-full bg-[#D7C47A]" style={{ height: `${computerHeight}%`, minHeight: item.computer ? 2 : 0 }} />
                    <div className="w-full bg-[#28F58D]" style={{ height: `${mobileHeight}%`, minHeight: item.mobile ? 2 : 0 }} />
                  </div>
                );
              })}
            </div>
          </section>

          <section className="mt-8 pb-4">
            <div className="mb-3">
              <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[#5B7062]">Daily Detail</div>
              <h3 className="mt-1 text-lg font-semibold text-[#E8FFF0]">最近 14 天</h3>
            </div>
            <div className="divide-y divide-[#193324] border-y border-[#193324]">
              {recentFourteenDays.map((item) => (
                <div key={item.date} className="grid grid-cols-[1fr_repeat(3,100px)] items-center gap-4 py-3 text-sm">
                  <span className="text-[#A9C7B3]">{formatStatDate(item.date)}</span>
                  <StatDetailCell label="手机" value={item.mobile} />
                  <StatDetailCell label="电脑" value={item.computer} />
                  <div className="text-right">
                    <div className="font-mono text-[10px] text-[#5B7062]">合计</div>
                    <div className="mt-0.5 font-mono font-semibold text-[#DDFCE7]">{formatStatNumber(item.total)}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function TypingPeriodSummary({ label, values }) {
  const totals = values || { total: 0, mobile: 0, computer: 0 };
  return (
    <div className="px-5 py-5 first:pl-0 last:pr-0 [&+&]:border-l [&+&]:border-[#193324]">
      <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">{label}</div>
      <div className="mt-2 font-mono text-3xl font-semibold text-[#B9FFD4]">{formatStatNumber(totals.total)}</div>
      <div className="mt-2 flex gap-4 text-xs text-[#789484]">
        <span>手机 {formatStatNumber(totals.mobile)}</span>
        <span>电脑 {formatStatNumber(totals.computer)}</span>
      </div>
    </div>
  );
}

function StatDetailCell({ label, value }) {
  return (
    <div className="text-right">
      <div className="font-mono text-[10px] text-[#5B7062]">{label}</div>
      <div className="mt-0.5 font-mono text-[#91B69E]">{formatStatNumber(value)}</div>
    </div>
  );
}

function formatStatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
}

function formatStatDate(value) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric", weekday: "short" });
}

function HurricaneEyeIcon({ compact = false }) {
  const size = compact ? 18 : 24;
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      aria-hidden="true"
      className="overflow-visible"
    >
      <defs>
        <radialGradient id={`glow-${compact ? "c" : "n"}`} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="rgba(40,245,141,0.32)" />
          <stop offset="100%" stopColor="rgba(40,245,141,0)" />
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="26" fill={`url(#glow-${compact ? "c" : "n"})`} />
      <path
        d="M13 36c5-10 14-16 28-16 3 0 6 .2 10 1-5-5-12-8-20-8-13 0-23 7-28 20 3 1.2 6.4 2.2 10 3Z"
        fill="#1FA463"
        fillOpacity=".58"
      />
      <path
        d="M14 38c6-12 15-18 29-18 6 0 11 1.1 15 3.2-4.8 8.4-12.9 13.1-24.5 14.2-5.7.5-12.2.7-19.5.6Z"
        fill="none"
        stroke="#80FFBA"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <path
        d="M50 28c-4.6 8.3-12 12.8-22.3 13.7-3.1.3-6.2.4-9.4.2 5.1 5.5 11 8.2 17.8 8.2 11.2 0 20.3-7 24.1-17.6A42.8 42.8 0 0 0 50 28Z"
        fill="none"
        stroke="#28F58D"
        strokeWidth="4"
        strokeLinecap="round"
        opacity=".9"
      />
      <circle cx="34" cy="33" r="5.5" fill="#050807" stroke="#9CFCC4" strokeWidth="2" />
      <circle cx="34" cy="33" r="1.9" fill="#9CFCC4" />
    </svg>
  );
}

function QrCode({ value }) {
  return (
    <QRCodeSVG
      value={value || "about:blank"}
      size={220}
      level="M"
      includeMargin={false}
      bgColor="#ffffff"
      fgColor="#000000"
      className="block aspect-square h-auto w-full"
    />
  );
}

createRoot(document.getElementById("root")).render(<FlowVoiceDesktopConsole />);
