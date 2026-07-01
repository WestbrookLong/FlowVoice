import React from "react";
import { createPortal } from "react-dom";
import { createRoot } from "react-dom/client";
import { QRCodeSVG } from "qrcode.react";
import "./styles.css";

const agentFloatMode = window.location.hash === "#agent-float";
if (agentFloatMode) {
  document.documentElement.classList.add("agent-float-page");
}

const fallbackState = {
  running: false,
  token: "loading",
  ip: "127.0.0.1",
  port: "8787",
  url: "",
  status: "SERVICE STOPPED",
  desktopVoice: {
    running: false,
    paused: false,
    status: "STOPPED",
    error: null,
    modelPath: "",
  },
  desktopVoiceSettings: {
    engine: "vosk",
    funasrMode: "offline",
    funasrModel: "iic/SenseVoiceSmall",
    funasrStreamingChunkMs: 600,
    baiduDevPid: "80001",
    semanticReranker: "bert",
    semanticModel: "hfl/chinese-macbert-base",
    punctuationStrategy: "spoken",
    voiceCommands: true,
    hotwords: "",
  },
  textAgent: {
    modeEnabled: false,
    recording: false,
    paused: false,
    provider: "preview",
    configured: false,
    triggerChars: 80,
    activeSession: null,
    recentSessions: [],
    polishing: false,
  },
  textAgentStyle: "meeting_notes",
  textAgentHotkey: {
    registered: false,
    error: null,
    label: "Ctrl+Alt+Space",
  },
  inputGate: {
    paused: false,
    label: "Alt+M",
    version: 0,
  },
  inputGateHotkey: {
    registered: false,
    error: null,
    label: "Alt+M",
  },
  typingStats: {
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
  const isAgentFloat = agentFloatMode;
  const [state, setState] = React.useState(fallbackState);
  const [message, setMessage] = React.useState("");
  const [desktopVoiceSettingsOpen, setDesktopVoiceSettingsOpen] = React.useState(false);
  const [typingStatsOpen, setTypingStatsOpen] = React.useState(false);
  const refreshInFlight = React.useRef(false);

  const ip = state.ip;
  const port = state.port;
  const token = state.token;
  const url = state.url || `http://${ip}:${port}/?token=${token}&v=voice`;
  const desktopVoice = state.desktopVoice || fallbackState.desktopVoice;
  const desktopVoiceSettings = state.desktopVoiceSettings || fallbackState.desktopVoiceSettings;
  const textAgent = state.textAgent || fallbackState.textAgent;
  const textAgentSession = textAgent.activeSession || {
    rawText: textAgent.rawText || "",
    status: textAgent.status || "idle",
  };
  const textAgentStyle = state.textAgentStyle || fallbackState.textAgentStyle;
  const textAgentHotkey = state.textAgentHotkey || fallbackState.textAgentHotkey;
  const inputGate = state.inputGate || fallbackState.inputGate;
  const inputGateHotkey = state.inputGateHotkey || fallbackState.inputGateHotkey;
  const typingStats = state.typingStats || fallbackState.typingStats;

  const refresh = React.useCallback(async () => {
    const api = desktopApi();
    if (!api || refreshInFlight.current) {
      return;
    }
    refreshInFlight.current = true;
    try {
      const next = isAgentFloat ? await api.get_agent_float_state() : await api.get_state();
      setState((previous) => ({ ...previous, ...next }));
    } finally {
      refreshInFlight.current = false;
    }
  }, [isAgentFloat]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    let started = false;
    const interval = isAgentFloat ? 500 : 650;
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
  }, [isAgentFloat, refresh]);

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

  function updateDesktopVoiceSettings(patch) {
    callApi("set_desktop_voice_settings", {
      ...desktopVoiceSettings,
      ...patch,
    });
  }

  function updateTextAgentStyle(style) {
    callApi("set_text_agent_style", style);
  }

  if (isAgentFloat) {
    return (
      <AgentFloat
        textAgent={textAgent}
        session={textAgentSession}
        hotkey={textAgentHotkey}
        onOpen={() => callApi("show_main_window")}
        onToggle={() => callApi("toggle_text_agent_recording")}
        onStop={() => callApi("stop_text_agent_recording")}
        onPause={() => callApi("pause_text_agent_recording")}
        onResume={() => callApi("resume_text_agent_recording")}
      />
    );
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
          <div className="col-span-12 rounded-[26px] border border-[#1E3B2B] bg-[#08100D]/88 p-5 shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl lg:col-span-8">
            {message && (
              <div className="mb-4 rounded-2xl border border-[#21462F] bg-[#06100B]/80 px-4 py-3 text-xs leading-5 text-[#A8F7C4]">
                {message}
              </div>
            )}

            <TextAgentPanel
              textAgent={textAgent}
              session={textAgentSession}
              style={textAgentStyle}
              hotkey={textAgentHotkey}
              onModeChange={(enabled) => callApi("set_text_agent_mode", { enabled })}
              onStyleChange={updateTextAgentStyle}
              onStart={() => callApi("start_text_agent_recording", { style: textAgentStyle })}
              onStop={() => callApi("stop_text_agent_recording")}
              onPause={() => callApi("pause_text_agent_recording")}
              onResume={() => callApi("resume_text_agent_recording")}
              onCopy={() => callApi("copy_text_agent_result")}
              onCopyPartial={() => callApi("copy_partial_text_agent_notes")}
            />

            <div className="mt-4 rounded-2xl border border-[#2F2A17] bg-[#161308]/75 px-4 py-3 text-xs leading-5 text-[#D7C47A]">
              To control elevated windows, run the client with administrator privileges.
            </div>
          </div>

          <aside className="col-span-12 flex min-h-0 flex-col rounded-[26px] border border-[#1E3B2B] bg-[#08100D]/88 p-5 shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl lg:col-span-4">
            <div className="mb-2 text-center">
              <div className="mx-auto mb-2 h-1 w-16 rounded-full bg-gradient-to-r from-transparent via-[#28F58D] to-transparent opacity-80" />
              <h2 className="text-xl font-semibold text-[#F2FFF7]">Quick Connect</h2>
              <p className="mt-1 text-sm leading-5 text-[#8EA99A]">Scan this QR code from your phone.</p>
            </div>

            <div className="mx-auto w-full max-w-[190px] rounded-2xl border border-[#2C6241] bg-gradient-to-br from-[#153321] to-[#06100B] p-2.5 shadow-[0_0_48px_rgba(40,245,141,0.12)]">
              <div className="rounded-xl bg-white p-2.5">
                <QrCode value={url} />
              </div>
            </div>

            <button
              type="button"
              onClick={() => setTypingStatsOpen(true)}
              className="mt-3 w-full rounded-2xl border border-[#21462F] bg-[#06100B] p-4 text-left transition hover:border-[#2E7447] hover:bg-[#08150D]"
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Typing Activity</div>
                  <div className="mt-1 text-xs text-[#7FA98E]">普通输入字数</div>
                </div>
                <span className="text-xl text-[#7FA98E]" aria-hidden="true">›</span>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                <TypingStatValue label="本日" value={typingStats.today?.total || 0} />
                <TypingStatValue label="本周" value={typingStats.week?.total || 0} />
                <TypingStatValue label="本月" value={typingStats.month?.total || 0} />
              </div>
            </button>

            <div className="mt-3 rounded-2xl border border-[#21462F] bg-[#06100B] p-4">
              <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Bound Session</div>
              <code className="block truncate font-mono text-xs text-[#B9FFD4]">{token}</code>
              <div className="mt-3 h-px bg-[#193324]" />
              <div className="mt-3 grid grid-cols-2 gap-2">
                {state.running ? (
                  <button onClick={() => callApi("stop_service")} className="rounded-xl border border-[#285C3B] bg-[#0C1E14] py-2.5 text-xs font-semibold text-[#A8F7C4] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition hover:bg-[#12301F]">
                    Stop Service
                  </button>
                ) : (
                  <button onClick={() => callApi("start_service")} className="rounded-xl bg-[#28F58D] py-2.5 text-xs font-bold text-[#041008] shadow-[0_0_24px_rgba(40,245,141,0.2)] transition hover:bg-[#67FFAD]">
                    Start Service
                  </button>
                )}
                <button onClick={() => callApi("refresh_connection")} className="rounded-xl border border-[#2E7447] bg-[#10291B] py-2.5 text-xs font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
                  Refresh
                </button>
              </div>
            </div>

            <div className="mt-3 rounded-2xl border border-[#21462F] bg-[#06100B] p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Computer Voice</div>
                  <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-[#C8E7D2]">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${desktopVoice.running && !desktopVoice.paused ? "bg-[#28F58D] shadow-[0_0_12px_rgba(40,245,141,0.85)]" : desktopVoice.paused ? "bg-[#D7C47A]" : desktopVoice.error ? "bg-[#E26A5E]" : "bg-[#5B7062]"}`} />
                    <span className="truncate">{desktopVoice.error || desktopVoice.status || "STOPPED"}</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setDesktopVoiceSettingsOpen(true)}
                  className="shrink-0 rounded-lg border border-[#193324] bg-[#050C08]/70 px-2.5 py-1.5 font-mono text-[10px] text-[#7FA98E] transition hover:bg-[#0C1E14] hover:text-[#A8F7C4]"
                >
                  Settings
                </button>
              </div>

              {desktopVoice.running ? (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {desktopVoice.paused ? (
                    <button onClick={() => callApi("resume_desktop_voice")} className="rounded-xl bg-[#28F58D] py-2 text-xs font-bold text-[#041008] transition hover:bg-[#67FFAD]">
                      Resume
                    </button>
                  ) : (
                    <button onClick={() => callApi("pause_desktop_voice")} className="rounded-xl border border-[#2F2A17] bg-[#161308]/75 py-2 text-xs font-semibold text-[#D7C47A] transition hover:bg-[#211C0B]">
                      Pause
                    </button>
                  )}
                  <button onClick={() => callApi("stop_desktop_voice")} className="rounded-xl border border-[#285C3B] bg-[#0C1E14] py-2 text-xs font-semibold text-[#A8F7C4] transition hover:bg-[#12301F]">
                    Stop
                  </button>
                </div>
              ) : (
                <button onClick={() => callApi("start_desktop_voice")} className="mt-3 w-full rounded-xl border border-[#2E7447] bg-[#10291B] py-2 text-xs font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
                  Start Computer Voice
                </button>
              )}
            </div>

            <button
              type="button"
              onClick={() => callApi("toggle_input_pause")}
              className={`mt-3 w-full rounded-2xl border p-3 text-left transition ${
                inputGate.paused
                  ? "border-[#6A5A20] bg-[#211C0B] hover:bg-[#2A230D]"
                  : "border-[#21462F] bg-[#06100B] hover:border-[#2E7447] hover:bg-[#08150D]"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Input Gate</div>
                  <div className={`mt-1 text-sm font-semibold ${inputGate.paused ? "text-[#D7C47A]" : "text-[#B9FFD4]"}`}>
                    {inputGate.paused ? "Input Paused" : "Input Active"}
                  </div>
                </div>
                <div className="text-right font-mono text-[10px] text-[#789484]">
                  {inputGateHotkey.label || inputGate.label}
                </div>
              </div>
            </button>

          </aside>
        </section>
      </main>
      {desktopVoiceSettingsOpen && (
        <DesktopVoiceSettingsPage
          settings={desktopVoiceSettings}
          running={desktopVoice.running}
          onChange={updateDesktopVoiceSettings}
          onClose={() => setDesktopVoiceSettingsOpen(false)}
        />
      )}
      {typingStatsOpen && (
        <TypingStatsPage
          stats={typingStats}
          onClose={() => setTypingStatsOpen(false)}
        />
      )}
      </div>
    </div>
  );
}

const textAgentStyles = [
  ["meeting_notes", "Meeting Notes"],
  ["formal_paragraph", "Formal Paragraph"],
  ["summary_bullets", "Summary Bullets"],
  ["todo_items", "Todo Items"],
  ["faithful_cleanup", "Faithful Cleanup"],
];

function AgentFloat({ textAgent, session, hotkey, onOpen, onToggle, onStop, onPause, onResume }) {
  const recording = Boolean(textAgent.recording);
  const paused = Boolean(textAgent.paused);
  const finalizing = session.status === "finalizing" || textAgent.polishing;
  const completed = Boolean(textAgent.completed) || (session.status === "done" && Boolean(session.finalText));
  const preview = session.rawText || (completed ? "本次会议纪要已保存至剪贴板" : "等待手机端输入原始文本");
  const status = finalizing ? "整理中" : recording ? "记录中" : paused ? "已暂停" : completed ? "已完成" : "待机";
  const previewRef = React.useRef(null);
  const autoFollowRef = React.useRef(true);

  React.useEffect(() => {
    const element = previewRef.current;
    if (element && autoFollowRef.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [preview, status]);

  function trackPreviewScroll() {
    const element = previewRef.current;
    if (!element) {
      return;
    }
    autoFollowRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 10;
  }

  return (
    <div className="h-screen overflow-hidden bg-transparent px-2 py-1 text-[#DDE7DF]">
      <div className="pywebview-drag-region flex h-full flex-col items-center justify-center">
        <button
          type="button"
          onClick={onOpen}
          className="relative h-[104px] w-full rounded-[24px] border border-[#CFE0D4] bg-[#FAFFF9] px-4 py-3 text-left text-[#06100B] shadow-[0_16px_38px_rgba(0,0,0,0.2)] transition hover:bg-white"
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[#2A6F45]">{status}</span>
            <span className="font-mono text-[10px] text-[#789484]">{hotkey.label}</span>
          </div>
          <div
            ref={previewRef}
            onScroll={trackPreviewScroll}
            className="h-[58px] overflow-y-auto whitespace-pre-wrap pr-1 text-[13px] font-semibold leading-5 [scrollbar-width:thin]"
          >
            {preview}
          </div>
          <span className="absolute bottom-[-10px] left-1/2 h-5 w-5 -translate-x-1/2 rotate-45 border-b border-r border-[#CFE0D4] bg-[#FAFFF9]" />
        </button>
        <div className="mt-4 flex h-11 items-center gap-3">
          {recording || paused ? (
            <button
              type="button"
              onClick={onStop}
              title="停止并整理"
              aria-label="停止并整理"
              className="grid h-10 w-10 place-items-center rounded-full bg-[#E04747] shadow-[0_8px_20px_rgba(224,71,71,0.32)] transition hover:bg-[#F05A5A]"
            >
              <span className="h-3 w-3 rounded-[2px] bg-white" />
            </button>
          ) : (
            <button
              type="button"
              onClick={onToggle}
              title="开始记录"
              aria-label="开始记录"
              className="grid h-10 w-10 place-items-center rounded-full bg-[#20C975] shadow-[0_8px_20px_rgba(32,201,117,0.32)] transition hover:bg-[#35DB89]"
            >
              <span className="h-3.5 w-3.5 rounded-full border-[3px] border-white" />
            </button>
          )}
          <button
            type="button"
            onClick={paused ? onResume : onPause}
            disabled={!recording && !paused}
            title={paused ? "继续记录" : "暂停记录"}
            aria-label={paused ? "继续记录" : "暂停记录"}
            className="grid h-10 w-10 place-items-center rounded-full border border-[#C8D8CD] bg-white text-[#163824] shadow-[0_8px_20px_rgba(0,0,0,0.16)] transition hover:bg-[#EFF8F2] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {paused ? (
              <span className="ml-0.5 h-0 w-0 border-y-[7px] border-l-[11px] border-y-transparent border-l-[#1E5B38]" />
            ) : (
              <span className="flex gap-1">
                <span className="h-4 w-1 rounded-full bg-[#1E5B38]" />
                <span className="h-4 w-1 rounded-full bg-[#1E5B38]" />
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function TextAgentPanel({ textAgent, session, style, hotkey, onModeChange, onStyleChange, onStart, onStop, onPause, onResume, onCopy, onCopyPartial }) {
  const recording = Boolean(textAgent.recording);
  const paused = Boolean(textAgent.paused);
  const finalText = session.finalText || "";
  const segments = Array.isArray(session.segmentSummaries) ? session.segmentSummaries : [];
  const finalizing = session.status === "finalizing" || textAgent.polishing;

  return (
    <div className="rounded-2xl border border-[#21462F] bg-[#06100B] p-4">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Text Agent</div>
          <div className="flex items-center gap-2 text-sm text-[#C8E7D2]">
            <span className={`h-2 w-2 rounded-full ${recording ? "bg-[#28F58D] shadow-[0_0_12px_rgba(40,245,141,0.85)]" : paused ? "bg-[#D7C47A]" : textAgent.polishing ? "bg-[#D7C47A]" : session.error ? "bg-[#E26A5E]" : "bg-[#5B7062]"}`} />
            <span>{recording ? "Recording mobile text" : paused ? "Paused: normal injection" : textAgent.polishing ? "Polishing" : session.status || "Idle"}</span>
          </div>
          <div className="mt-1 text-xs text-[#6F8878]">
            Provider {textAgent.provider} · trigger every {textAgent.triggerChars || 80} chars · {hotkey.label}
          </div>
        </div>
        <button
          type="button"
          onClick={() => onModeChange(!textAgent.modeEnabled)}
          className={`rounded-xl border px-3 py-2 text-xs font-bold transition ${textAgent.modeEnabled ? "border-[#28F58D]/35 bg-[#0D2A19] text-[#8BFFBA]" : "border-[#285C3B] bg-[#0C1E14] text-[#7EA88E]"}`}
        >
          {textAgent.modeEnabled ? "Mode On" : "Mode Off"}
        </button>
      </div>

      <div className="mb-3 grid gap-3 lg:grid-cols-[1fr_auto]">
        <select
          value={style}
          onChange={(event) => onStyleChange(event.target.value)}
          className="rounded-xl border border-[#21462F] bg-[#030805] px-3 py-2 text-sm text-[#B9FFD4] outline-none transition focus:border-[#2E7447]"
        >
          {textAgentStyles.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        {recording || paused ? (
          <div className="grid grid-cols-2 gap-2">
            <button onClick={onStop} className="rounded-xl bg-[#28F58D] px-4 py-2 text-sm font-bold text-[#041008] shadow-[0_0_24px_rgba(40,245,141,0.18)] transition hover:bg-[#67FFAD]">
              Stop + Copy
            </button>
            <button onClick={paused ? onResume : onPause} className="rounded-xl border border-[#2F2A17] bg-[#161308]/75 px-4 py-2 text-sm font-semibold text-[#D7C47A] transition hover:bg-[#211C0B]">
              {paused ? "Resume" : "Pause"}
            </button>
          </div>
        ) : (
          <button onClick={onStart} className="rounded-xl border border-[#2E7447] bg-[#10291B] px-4 py-2 text-sm font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
            Start Recording
          </button>
        )}
      </div>

      <MeetingMinutesFlow
        segments={segments}
        polishing={finalizing}
        completed={session.status === "done" && Boolean(finalText)}
      />
      {session.error && <TextAgentBlock title="Error" text={session.error} danger />}

      {(segments.length > 0 || finalText) && (
        <div className="mt-3 flex justify-end gap-2">
          {segments.length > 0 && (
            <button onClick={onCopyPartial} className="rounded-xl border border-[#31503C] bg-[#0A1710] px-5 py-2 text-xs font-semibold text-[#9BCBAB] transition hover:bg-[#10251A]">
              Copy Partial Notes
            </button>
          )}
          {finalText && (
            <button onClick={onCopy} className="rounded-xl border border-[#2E7447] bg-[#10291B] px-5 py-2 text-xs font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
              Copy Final Notes
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function MeetingMinutesFlow({ segments, polishing, completed }) {
  return (
    <section className="mt-4 min-h-[420px] rounded-2xl border border-[#193324] bg-[#030805]/72 p-5">
      <div className="mb-5 flex items-center justify-between gap-4 border-b border-[#193324] pb-4">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[#5B7062]">Meeting Minutes Flow</div>
          <h3 className="mt-1 text-lg font-semibold text-[#F2FFF7]">实时会议纪要</h3>
        </div>
        <div className="flex items-center gap-2 text-xs text-[#6F8878]">
          <span>{segments.length} 段</span>
          {polishing && <span className="rounded-full border border-[#6A5A20] bg-[#211C0B] px-3 py-1 text-[#D7C47A]">AI 整理中</span>}
          {completed && <span className="rounded-full border border-[#2E7447] bg-[#10291B] px-3 py-1 text-[#8BFFBA]">已完成</span>}
        </div>
      </div>

      {segments.length === 0 ? (
        <div className="grid min-h-[300px] place-items-center text-center">
          <div>
            <div className="mx-auto mb-3 h-2 w-2 rounded-full bg-[#28F58D] shadow-[0_0_18px_rgba(40,245,141,0.65)]" />
            <div className="text-sm font-semibold text-[#BFDAC8]">
              {polishing ? "AI 正在生成第一段纪要" : "每新增约 80 字后生成一段会议纪要"}
            </div>
            <div className="mt-1 text-xs text-[#587060]">原始文本仅显示在浮标中</div>
          </div>
        </div>
      ) : (
        <div className="max-h-[560px] overflow-y-auto pr-2">
          {segments.map((segment, index) => (
            <MeetingMinuteItem key={segment.id || index} segment={segment} isLast={index === segments.length - 1} />
          ))}
          {polishing && (
            <div className="ml-[88px] mt-2 flex items-center gap-2 text-xs text-[#D7C47A]">
              <span className="h-2 w-2 animate-pulse rounded-full bg-[#D7C47A]" />
              正在整理新的会议片段
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function MeetingMinuteItem({ segment, isLast }) {
  const time = formatMeetingTime(segment.createdAt);
  const points = Array.isArray(segment.keyPoints) ? segment.keyPoints : [];
  const actions = Array.isArray(segment.actionItems) ? segment.actionItems : [];

  return (
    <article className="grid grid-cols-[64px_24px_minmax(0,1fr)] gap-3">
      <div className="pt-1 text-right font-mono text-[11px] text-[#5F7767]">{time}</div>
      <div className="relative flex justify-center">
        <span className="relative z-10 mt-1.5 h-3 w-3 rounded-full border-2 border-[#06100B] bg-[#28F58D] shadow-[0_0_12px_rgba(40,245,141,0.5)]" />
        {!isLast && <span className="absolute bottom-0 top-4 w-px bg-[#21462F]" />}
      </div>
      <div className="mb-6 min-w-0 rounded-2xl border border-[#193324] bg-[#06100B]/86 p-4">
        <h4 className="text-sm font-semibold text-[#E8FFF0]">{segment.title || `会议片段 ${segment.index || ""}`}</h4>
        {segment.summary && <p className="mt-2 text-sm leading-6 text-[#A9C7B3]">{segment.summary}</p>}
        {points.length > 0 && (
          <ul className="mt-3 space-y-2">
            {points.map((point, index) => (
              <li key={index} className="flex gap-2 text-xs leading-5 text-[#91B69E]">
                <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-[#28F58D]" />
                <span>{point}</span>
              </li>
            ))}
          </ul>
        )}
        {actions.length > 0 && (
          <div className="mt-4 border-t border-[#193324] pt-3">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[#6F8878]">Action Items</div>
            <div className="space-y-2">
              {actions.map((action, index) => (
                <label key={index} className="flex items-start gap-2 text-xs leading-5 text-[#B5D2BE]">
                  <input type="checkbox" className="mt-1 accent-[#28F58D]" />
                  <span>
                    {action.text}
                    {(action.owner || action.deadline) && (
                      <span className="ml-2 text-[#667D6D]">
                        {[action.owner, action.deadline].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

function formatMeetingTime(value) {
  if (!value) {
    return "--:--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TextAgentBlock({ title, text, muted = false, danger = false }) {
  return (
    <div className={`mt-3 rounded-2xl border px-4 py-3 ${danger ? "border-[#663A16] bg-[#1B0A0A]/50" : "border-[#193324] bg-[#050C08]/70"}`}>
      <div className={`mb-1 text-xs font-semibold ${danger ? "text-[#FFD9D9]" : "text-[#DDFCE7]"}`}>{title}</div>
      <div className={`max-h-28 overflow-auto whitespace-pre-wrap text-xs leading-5 ${danger ? "text-[#FFD9D9]" : muted ? "text-[#6F8878]" : "text-[#A8F7C4]"}`}>
        {text}
      </div>
    </div>
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

        <div className="grid grid-cols-3 border-y border-[#193324]">
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

function DesktopVoiceSettingsPage({ settings, running, onChange, onClose }) {
  return (
    <div className="fixed inset-0 z-40 bg-[#050807]/96 backdrop-blur-2xl">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute right-[-140px] top-[-160px] h-[420px] w-[420px] rounded-full bg-[#28F58D]/10 blur-[90px]" />
        <div className="absolute bottom-[-220px] left-[-140px] h-[520px] w-[520px] rounded-full bg-[#1FA463]/12 blur-[110px]" />
        <div className="absolute inset-0 opacity-[0.045] [background-image:linear-gradient(rgba(255,255,255,.6)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.6)_1px,transparent_1px)] [background-size:36px_36px]" />
      </div>
      <div className="relative mx-auto flex h-full max-w-5xl flex-col px-8 py-7">
        <div className="mb-5 flex items-center justify-between">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-[0.32em] text-[#74E7A5]/70">Computer Voice</div>
            <h2 className="mt-2 text-3xl font-semibold tracking-[-0.03em] text-[#F2FFF7]">桌面语音输入设置</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-2xl border border-[#285C3B] bg-[#0C1E14] px-5 py-3 text-sm font-semibold text-[#A8F7C4] transition hover:bg-[#12301F]"
          >
            关闭
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto rounded-[28px] border border-[#1E3B2B] bg-[#08100D]/88 p-5 shadow-[0_26px_80px_rgba(0,0,0,0.5)]">
          <div className="grid gap-4 lg:grid-cols-2">
            <SettingsSection title="模型" caption={running ? "引擎、模型和热词保存后需要重启桌面语音生效。" : "选择本地识别引擎和模型。"}>
              <SettingSelect
                title="识别引擎"
                value={settings.engine}
                options={[
                  ["vosk", "Vosk MVP（当前稳定）"],
                  ["funasr", "FunASR（高质量，可选）"],
                  ["baidu", "Baidu API (cloud ASR)"],
                ]}
                onChange={(engine) => onChange({ engine })}
              />
              {settings.engine === "baidu" && (
                <>
                  <SettingText
                    title="Baidu dev_pid"
                    value={settings.baiduDevPid || "80001"}
                    onChange={(baiduDevPid) => onChange({ baiduDevPid })}
                  />
                  <div className="rounded-2xl border border-[#2F2A17] bg-[#161308]/75 px-4 py-3 text-xs leading-5 text-[#D7C47A]">
                    Set FLOWVOICE_BAIDU_API_KEY and FLOWVOICE_BAIDU_SECRET_KEY before starting Computer Voice. Default dev_pid is 80001; FLOWVOICE_BAIDU_DEV_PID can override it.
                  </div>
                </>
              )}
              <SettingSelect
                title="FunASR 模型"
                value={settings.funasrModel}
                disabled={settings.engine !== "funasr" || ["streaming", "candidate_streaming"].includes(settings.funasrMode)}
                options={[
                  ["iic/SenseVoiceSmall", "SenseVoiceSmall（中英混说）"],
                  ["paraformer-zh", "Paraformer 中文"],
                ]}
                onChange={(funasrModel) => onChange({ funasrModel })}
              />
              <SettingSelect
                title="FunASR 模式"
                value={settings.funasrMode}
                disabled={settings.engine !== "funasr"}
                options={[
                  ["offline", "Offline：句末识别，兼容当前行为"],
                  ["streaming", "Streaming：paraformer-zh-streaming partial"],
                  ["candidate_streaming", "Candidate Streaming：top-k + 语义纠察"],
                ]}
                onChange={(funasrMode) => onChange({ funasrMode })}
              />
              {settings.engine === "funasr" && ["streaming", "candidate_streaming"].includes(settings.funasrMode) && (
                <>
                  <SettingNumber
                    title="流式上屏间隔"
                    value={settings.funasrStreamingChunkMs || 600}
                    min={100}
                    max={1000}
                    step={50}
                    suffix="ms"
                    onChange={(funasrStreamingChunkMs) => onChange({ funasrStreamingChunkMs })}
                  />
                  <div className="rounded-2xl border border-[#2F2A17] bg-[#161308]/75 px-4 py-3 text-xs leading-5 text-[#D7C47A]">
                    Streaming 模式固定使用 paraformer-zh-streaming；Candidate Streaming 会读取 decoder top-k 并在候选空间内做语义纠察。上屏间隔越小越流畅，但模型调用更频繁，partial 稳定性可能下降。
                  </div>
                </>
              )}
              {settings.engine === "funasr" && settings.funasrMode === "candidate_streaming" && (
                <>
                  <SettingSelect
                    title="语义纠察模型"
                    value={settings.semanticReranker || "bert"}
                    options={[
                      ["bert", "BERT MLM：MacBERT 候选重排"],
                      ["heuristic", "Heuristic：热词/规则回退"],
                    ]}
                    onChange={(semanticReranker) => onChange({ semanticReranker })}
                  />
                  <SettingText
                    title="BERT 模型"
                    value={settings.semanticModel || "hfl/chinese-macbert-base"}
                    disabled={(settings.semanticReranker || "bert") !== "bert"}
                    onChange={(semanticModel) => onChange({ semanticModel })}
                  />
                  <div className="rounded-2xl border border-[#21462F] bg-[#06100B]/80 px-4 py-3 text-xs leading-5 text-[#8EA99A]">
                    BERT MLM 只在 ASR top-k 候选空间内打分，不自由生成文本；模型加载失败时会自动回退到 Heuristic，不会中断语音输入。
                  </div>
                </>
              )}
            </SettingsSection>

            <SettingsSection title="输入法策略" caption="这些设置控制识别文本进入光标前的处理方式。">
              <SettingSelect
                title="标点策略"
                value={settings.punctuationStrategy}
                options={[
                  ["spoken", "口述标点：逗号/句号 -> 标点"],
                  ["model", "模型标点：保留 ASR 输出标点"],
                  ["none", "不处理标点"],
                ]}
                onChange={(punctuationStrategy) => onChange({ punctuationStrategy })}
              />
              <DesktopVoiceSetting
                title="英文语音控制"
                description="enter / back / backspace / delete all"
                enabled={settings.voiceCommands}
                onChange={(voiceCommands) => onChange({ voiceCommands })}
              />
            </SettingsSection>
          </div>

          <SettingsSection title="热词" caption="每行一个词，用于增强人名、软件名、项目名和中英混合专有词。" className="mt-4">
            <textarea
              value={settings.hotwords || ""}
              onChange={(event) => onChange({ hotwords: event.target.value })}
              spellCheck={false}
              placeholder={"OpenAI\nVS Code\nObsidian\nPhotoshop"}
              className="min-h-36 w-full resize-none rounded-2xl border border-[#21462F] bg-[#030805] px-4 py-3 font-mono text-sm text-[#B9FFD4] outline-none transition placeholder:text-[#3D5647] focus:border-[#2E7447]"
            />
            <div className="mt-3 rounded-2xl border border-[#2F2A17] bg-[#161308]/75 px-4 py-3 text-xs leading-5 text-[#D7C47A]">
              FunASR 支持热词增强；Vosk 当前保留这个配置但不会使用。后续可以继续把这里扩展为词库文件。
            </div>
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

function SettingsSection({ title, caption, className = "", children }) {
  return (
    <section className={`rounded-3xl border border-[#1E3B2B] bg-[#06100B]/86 p-5 ${className}`}>
      <div className="mb-4">
        <h3 className="text-lg font-semibold text-[#F2FFF7]">{title}</h3>
        <p className="mt-1 text-xs leading-5 text-[#6F8878]">{caption}</p>
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function SettingSelect({ title, value, options, onChange, disabled = false }) {
  return (
    <label className={`block rounded-2xl border border-[#193324] bg-[#050C08]/70 p-3 ${disabled ? "opacity-45" : ""}`}>
      <span className="mb-2 block text-xs font-semibold text-[#DDFCE7]">{title}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-[#21462F] bg-[#030805] px-3 py-2 text-sm text-[#B9FFD4] outline-none transition focus:border-[#2E7447] disabled:cursor-not-allowed"
      >
        {options.map(([optionValue, label]) => (
          <option key={optionValue} value={optionValue}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}

function SettingNumber({ title, value, onChange, min, max, step = 1, suffix = "", disabled = false }) {
  return (
    <label className={`block rounded-2xl border border-[#193324] bg-[#050C08]/70 p-3 ${disabled ? "opacity-45" : ""}`}>
      <span className="mb-2 block text-xs font-semibold text-[#DDFCE7]">{title}</span>
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={(event) => {
            const next = Number.parseInt(event.target.value, 10);
            if (Number.isFinite(next)) {
              onChange(next);
            }
          }}
          className="min-w-0 flex-1 rounded-xl border border-[#21462F] bg-[#030805] px-3 py-2 text-sm text-[#B9FFD4] outline-none transition focus:border-[#2E7447] disabled:cursor-not-allowed"
        />
        {suffix && <span className="font-mono text-xs text-[#6F8878]">{suffix}</span>}
      </div>
    </label>
  );
}

function SettingText({ title, value, onChange, disabled = false }) {
  return (
    <label className={`block rounded-2xl border border-[#193324] bg-[#050C08]/70 p-3 ${disabled ? "opacity-45" : ""}`}>
      <span className="mb-2 block text-xs font-semibold text-[#DDFCE7]">{title}</span>
      <input
        value={value}
        disabled={disabled}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-[#21462F] bg-[#030805] px-3 py-2 font-mono text-sm text-[#B9FFD4] outline-none transition focus:border-[#2E7447] disabled:cursor-not-allowed"
      />
    </label>
  );
}

function DesktopVoiceSetting({ title, description, enabled, onChange }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="text-xs font-semibold text-[#DDFCE7]">{title}</div>
        <div className="mt-0.5 truncate text-[11px] text-[#6F8878]">{description}</div>
      </div>
      <button
        type="button"
        onClick={() => onChange(!enabled)}
        className={`relative h-7 w-12 shrink-0 rounded-full border transition ${enabled ? "border-[#28F58D]/30 bg-[#2F8E53]" : "border-[#285C3B] bg-[#0C1E14]"}`}
        aria-pressed={enabled}
      >
        <span className={`absolute top-1 h-5 w-5 rounded-full transition ${enabled ? "left-6 bg-[#6DF59A] shadow-[0_0_12px_rgba(109,245,154,0.5)]" : "left-1 bg-[#6F8878]"}`} />
      </button>
    </div>
  );
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
