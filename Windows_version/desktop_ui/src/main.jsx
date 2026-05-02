import React from "react";
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
};

function desktopApi() {
  return window.pywebview?.api;
}

function FlowBridgeDarkGreenPreview() {
  const [state, setState] = React.useState(fallbackState);
  const [message, setMessage] = React.useState("");

  const ip = state.ip;
  const port = state.port;
  const token = state.token;
  const url = state.url || `http://${ip}:${port}/?token=${token}&v=voice`;

  const refresh = React.useCallback(async () => {
    const api = desktopApi();
    if (!api) {
      return;
    }
    const next = await api.get_state();
    setState(next);
  }, []);

  React.useEffect(() => {
    const timer = window.setInterval(refresh, 600);
    const ready = () => refresh();
    window.addEventListener("pywebviewready", ready);
    refresh();
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("pywebviewready", ready);
    };
  }, [refresh]);

  async function callApi(action, payload) {
    const api = desktopApi();
    if (!api) {
      setMessage("Desktop API is not ready.");
      return;
    }
    try {
      const method = api[action];
      const result = payload === undefined ? await method() : await method(payload);
      setState(result.state);
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

      <main className="relative mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col overflow-hidden px-8 py-5">
        <header className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="grid h-11 w-11 place-items-center rounded-xl border border-[#28F58D]/25 bg-[#0B1D14] font-mono text-sm font-black text-[#80FFBA] shadow-[0_0_32px_rgba(40,245,141,0.15)]">
              FB
            </div>
            <div>
              <div className="font-mono text-[11px] uppercase tracking-[0.35em] text-[#74E7A5]/70">Flow Bridge</div>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[#F0FFF5]">Desktop Connection Console</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="font-mono text-xs text-[#5B7062]">{ip}:{port}</span>
            <ServiceBadge running={state.running} />
          </div>
        </header>

        <section className="grid min-h-0 flex-1 grid-cols-12 gap-5">
          <div className="col-span-12 rounded-[26px] border border-[#1E3B2B] bg-[#08100D]/88 p-5 shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl lg:col-span-8">
            <div className="mb-4 flex items-start justify-between gap-6">
              <div>
                <div className="mb-2 inline-flex rounded-full border border-[#28F58D]/20 bg-[#10271B] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.22em] text-[#7BFFB5]">
                  Mobile Input Endpoint
                </div>
                <h2 className="text-[28px] font-semibold leading-tight tracking-[-0.03em] text-[#F2FFF7]">
                  Scan, open, and start dictating.
                </h2>
                <p className="mt-2 max-w-xl text-sm leading-5 text-[#8EA99A]">
                  Use your phone as a voice input surface. The recognized text will be written to the active cursor on this desktop.
                </p>
              </div>
              <div className="hidden rounded-2xl border border-[#1A3326] bg-[#06100B] px-4 py-3 text-right lg:block">
                <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Local</div>
                <div className="mt-1 font-mono text-sm text-[#9CFCC4]">{ip}</div>
                <div className="mt-0.5 font-mono text-xs text-[#688876]">port {port}</div>
              </div>
            </div>

            <div className="rounded-[22px] border border-[#21462F] bg-gradient-to-br from-[#0D2116] via-[#09140F] to-[#07100B] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-sm font-medium text-[#DDFCE7]">Open this URL on your phone</div>
                <div className="font-mono text-[11px] text-[#60836E]">Token included</div>
              </div>

              <div className="flex items-center gap-3 rounded-2xl border border-[#2C6241] bg-[#030805] p-2.5 shadow-[0_0_0_1px_rgba(62,255,145,0.03)]">
                <code className="min-w-0 flex-1 truncate px-3 font-mono text-[13px] text-[#B9FFD4]">{url}</code>
                <button onClick={() => callApi("copy_url")} className="rounded-xl border border-[#2E7447] bg-[#10291B] px-4 py-2 text-xs font-semibold text-[#B9FFD4] transition hover:bg-[#163A26]">
                  Copy URL
                </button>
                <button onClick={() => callApi("open_url")} className="rounded-xl bg-[#28F58D] px-4 py-2 text-xs font-bold text-[#041008] shadow-[0_0_28px_rgba(40,245,141,0.24)] transition hover:bg-[#67FFAD]">
                  Open
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11px] text-[#5F7767]">
                <span>IP <strong className="font-medium text-[#9BE7B6]">{ip}</strong></span>
                <span>
                  PORT{" "}
                  <input
                    value={port}
                    disabled={state.running}
                    onChange={(event) => callApi("set_port", event.target.value)}
                    className="w-16 rounded-md border border-[#21462F] bg-[#06100B] px-2 py-1 font-medium text-[#9BE7B6] outline-none disabled:opacity-60"
                  />
                </span>
                <span>
                  TOKEN{" "}
                  <input
                    value={token}
                    disabled={state.running}
                    onChange={(event) => callApi("set_token", event.target.value)}
                    className="w-44 rounded-md border border-[#21462F] bg-[#06100B] px-2 py-1 font-medium text-[#9BE7B6] outline-none disabled:opacity-60"
                  />
                </span>
                <button disabled={state.running} onClick={() => callApi("regenerate_token")} className="rounded-md border border-[#2E7447] bg-[#10291B] px-2 py-1 text-[#B9FFD4] disabled:cursor-not-allowed disabled:opacity-45">
                  New Token
                </button>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-4">
              <ThinInfo title="Status" value={state.running ? "Listening for mobile input" : "Service is stopped"} />
              <ThinInfo title="Network" value="Same Wi-Fi or LAN required" />
            </div>

            {message && (
              <div className="mt-4 rounded-2xl border border-[#21462F] bg-[#06100B]/80 px-4 py-3 text-xs leading-5 text-[#A8F7C4]">
                {message}
              </div>
            )}

            <div className="mt-4 rounded-2xl border border-[#2F2A17] bg-[#161308]/75 px-4 py-3 text-xs leading-5 text-[#D7C47A]">
              To control elevated windows, run the client with administrator privileges.
            </div>
          </div>

          <aside className="col-span-12 flex min-h-0 flex-col rounded-[26px] border border-[#1E3B2B] bg-[#08100D]/88 p-5 shadow-[0_26px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl lg:col-span-4">
            <div className="mb-3 text-center">
              <div className="mx-auto mb-2 h-1 w-16 rounded-full bg-gradient-to-r from-transparent via-[#28F58D] to-transparent opacity-80" />
              <h2 className="text-xl font-semibold text-[#F2FFF7]">Quick Connect</h2>
              <p className="mt-1 text-sm leading-5 text-[#8EA99A]">Scan this QR code from your phone.</p>
            </div>

            <div className="mx-auto w-full max-w-[250px] rounded-[28px] border border-[#2C6241] bg-gradient-to-br from-[#153321] to-[#06100B] p-3 shadow-[0_0_60px_rgba(40,245,141,0.13)]">
              <div className="rounded-[20px] bg-white p-3">
                <QrCode value={url} />
              </div>
            </div>

            <div className="mt-3 rounded-2xl border border-[#21462F] bg-[#06100B] p-4">
              <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[#5B7062]">Bound Session</div>
              <code className="block truncate font-mono text-xs text-[#B9FFD4]">{token}</code>
              <div className="mt-3 h-px bg-[#193324]" />
              <code className="mt-3 block truncate font-mono text-xs text-[#82B995]">{ip}:{port}</code>
            </div>

            <div className="mt-auto pt-3">
              {state.running ? (
                <button onClick={() => callApi("stop_service")} className="w-full rounded-2xl border border-[#285C3B] bg-[#0C1E14] py-3 text-sm font-semibold text-[#A8F7C4] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition hover:bg-[#12301F]">
                  Stop Service
                </button>
              ) : (
                <button onClick={() => callApi("start_service")} className="w-full rounded-2xl bg-[#28F58D] py-3 text-sm font-bold text-[#041008] shadow-[0_0_28px_rgba(40,245,141,0.22)] transition hover:bg-[#67FFAD]">
                  Start Service
                </button>
              )}
            </div>
          </aside>
        </section>
      </main>
      </div>
    </div>
  );
}

function CustomTitleBar({ onMinimize, onMaximize, onClose }) {
  return (
    <div className="relative z-20 flex h-11 shrink-0 items-center border-b border-[#173524] bg-[#050807]/96 shadow-[0_1px_0_rgba(40,245,141,0.08)]">
      <div className="flex h-full items-center gap-3 px-4">
        <div className="grid h-7 w-7 place-items-center rounded-lg border border-[#28F58D]/25 bg-[#0B1D14] font-mono text-[10px] font-black text-[#80FFBA] shadow-[0_0_18px_rgba(40,245,141,0.12)]">
          FB
        </div>
        <div className="font-mono text-[11px] uppercase tracking-[0.28em] text-[#8BFFBA]/75">Flow Bridge</div>
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

function ThinInfo({ title, value }) {
  return (
    <div className="rounded-2xl border border-[#193324] bg-[#06100B]/80 px-4 py-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[#5B7062]">{title}</div>
      <div className="mt-1 text-sm text-[#C8E7D2]">{value}</div>
    </div>
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

createRoot(document.getElementById("root")).render(<FlowBridgeDarkGreenPreview />);
