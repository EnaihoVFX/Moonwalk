/* ─────────────────────────────────────────────────────────────
   MOONWALK – Renderer (Raw Audio Streaming to Backend)
   ───────────────────────────────────────────────────────────── */

const State = Object.freeze({
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  LOADING: "LOADING",
  DOING: "DOING"
});

const WS_URL = "ws://127.0.0.1:8000/ws";

/* ── IPC Bridge ── */
const bridge = window.overlayAPI || {
  hideWindow: async () => { },
  enableMouse: () => { },
  disableMouse: () => { },
  onStartListening: () => () => { },
  onOverlayHidden: () => () => { },
  logError: () => { },
  logInfo: () => { }
};

/* ── IPC Bridge ── */

/* ── DOM Refs ── */
const wrapper = document.getElementById("ui-wrapper");
const uiSpeech = document.getElementById("ui-speech");
const uiLoading = document.getElementById("ui-loading");
const uiDoing = document.getElementById("ui-doing");
const statusEl = document.getElementById("status-text");
const doingTextEl = document.getElementById("doing-text");
const appIconEl = document.getElementById("app-icon");
const tierBadge = document.getElementById("tier-badge");
const keyCapture = document.getElementById("key-capture");

/* ── App State ── */
const app = {
  current: State.IDLE,
  visible: true,
  ws: null,
  reconnectTimer: null,
  reconnectDelay: 700,
  reconnectMaxDelay: 7000,
  mouseEnabled: false,
  isDisposed: false,
  detectedApp: "",
  actionMessage: "Processing...",

  // Audio Streaming Pipeline
  audioStream: null,
  audioContext: null,
  sourceNode: null,
  scriptProcessor: null
};

/* ── UI State Management ── */
function setIslandState(nextStateClass) {
  wrapper.className = `glass-pill ${nextStateClass}`;
}

function setState(next, { tier = "", text = null, force = false } = {}) {
  if (!force && app.current === next) return;
  app.current = next;

  uiSpeech.classList.add('hidden');
  uiLoading.classList.add('hidden');
  uiDoing.classList.add('hidden');

  if (next === State.IDLE) {
    setIslandState('state-idle');
    uiSpeech.classList.remove('hidden');
    statusEl.innerText = "Hey Moonwalk";
  }
  else if (next === State.LISTENING) {
    setIslandState('state-listening');
    uiSpeech.classList.remove('hidden');
    statusEl.innerText = "Listening...";
  }
  else if (next === State.LOADING) {
    setIslandState('state-loading');
    uiLoading.classList.remove('hidden');
  }
  else if (next === State.DOING) {
    setIslandState('state-doing');
    uiDoing.classList.remove('hidden');

    if (text) doingTextEl.innerText = text;

    if (app.detectedApp) {
      const domain = `${app.detectedApp.replace(/\s+/g, '')}.com`;
      appIconEl.src = `https://icon.horse/icon/${domain}`;
      appIconEl.style.display = 'block';
    } else {
      appIconEl.style.display = 'none';
    }

    if (tier) {
      tierBadge.innerText = tier;
      tierBadge.classList.remove('hidden');
    } else {
      tierBadge.classList.add('hidden');
    }
  }
}

function clearCommandContext() {
  app.detectedApp = "";
  app.actionMessage = "Processing...";
  appIconEl.src = "";
  appIconEl.style.display = 'none';
}

function setMouseEnabled(enabled) {
  const next = Boolean(enabled);
  if (app.mouseEnabled === next) return;
  app.mouseEnabled = next;
  next ? bridge.enableMouse() : bridge.disableMouse();
}

/* ── Audio Encoding (PCM to Base64 WAV) ── */

function floatTo16BitPCM(output, offset, input) {
  for (let i = 0; i < input.length; i++, offset += 2) {
    // Clamp between -1 and 1
    const s = Math.max(-1, Math.min(1, input[i]));
    // Convert to 16-bit integer (multiply by 0x7FFF)
    output.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
}

function writeString(view, offset, string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}

// Packages the raw Float32Array PCM chunk into a full WAV file Buffer
function encodeWAVChunk(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  // RIFF chunk descriptor
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');

  // fmt sub-chunk
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);             // Subchunk1Size (16 for PCM)
  view.setUint16(20, 1, true);              // AudioFormat (1 for PCM)
  view.setUint16(22, 1, true);              // NumChannels (1: mono)
  view.setUint32(24, sampleRate, true);     // SampleRate
  view.setUint32(28, sampleRate * 2, true); // ByteRate (SampleRate * NumChannels * BitsPerSample/8)
  view.setUint16(32, 2, true);              // BlockAlign (NumChannels * BitsPerSample/8)
  view.setUint16(34, 16, true);             // BitsPerSample

  // data sub-chunk
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  // Write the PCM samples
  floatTo16BitPCM(view, 44, samples);

  return buffer;
}

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary);
}

/* ── Continuous Microphone Streaming ── */

async function startAudioStreaming() {
  if (app.audioStream) return; // Already running

  try {
    app.audioStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
    app.audioContext = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: 16000 // Force 16kHz for backend speech models
    });

    // We use ScriptProcessorNode because it's the easiest cross-platform way 
    // to access raw PCM data without AudioWorklet complexity.
    app.sourceNode = app.audioContext.createMediaStreamSource(app.audioStream);
    app.scriptProcessor = app.audioContext.createScriptProcessor(4096, 1, 1);

    app.scriptProcessor.onaudioprocess = (event) => {
      // Only send if websocket is open
      if (!app.ws || app.ws.readyState !== WebSocket.OPEN) return;

      const inputBuffer = event.inputBuffer.getChannelData(0); // Mono Float32Array
      const sampleRate = app.audioContext.sampleRate; // Typically 16000 here

      // Pack into a WAV wrapper
      const wavBuffer = encodeWAVChunk(inputBuffer, sampleRate);

      // Convert to base64
      const base64Audio = arrayBufferToBase64(wavBuffer);

      // Stream to Python Backend
      app.ws.send(JSON.stringify({
        type: "audio_chunk",
        payload: base64Audio
      }));
    };

    app.sourceNode.connect(app.scriptProcessor);
    app.scriptProcessor.connect(app.audioContext.destination);

    console.log("Started continuous audio streaming at 16kHz");
  } catch (err) {
    console.error("Failed to access microphone:", err);
    statusEl.innerText = "Mic Error";
    if (bridge.logError) {
      bridge.logError(`Mic Access Failed: ${err.message}`);
    }
  }
}

async function stopAudioStreaming() {
  if (app.scriptProcessor) {
    app.scriptProcessor.disconnect();
    app.scriptProcessor = null;
  }
  if (app.sourceNode) {
    app.sourceNode.disconnect();
    app.sourceNode = null;
  }
  if (app.audioContext) {
    await app.audioContext.close();
    app.audioContext = null;
  }
  if (app.audioStream) {
    app.audioStream.getTracks().forEach(track => track.stop());
    app.audioStream = null;
  }
}


/* ── WebSocket ── */

function scheduleReconnect() {
  if (app.isDisposed || app.reconnectTimer) return;
  app.reconnectTimer = window.setTimeout(() => {
    app.reconnectTimer = null;
    if (!app.isDisposed) connectWebSocket();
  }, app.reconnectDelay);
  app.reconnectDelay = Math.min(Math.round(app.reconnectDelay * 1.6), app.reconnectMaxDelay);
}

function connectWebSocket() {
  if (app.isDisposed) return;
  if (app.ws && (app.ws.readyState === WebSocket.OPEN || app.ws.readyState === WebSocket.CONNECTING)) return;

  try {
    app.ws = new WebSocket(WS_URL);
    app.ws.addEventListener("open", () => {
      app.reconnectDelay = 700;
      statusEl.innerText = "Hey Moonwalk";
    });

    app.ws.addEventListener("message", (event) => {
      if (typeof event.data !== "string") return;
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }

      // Python Backend controls the UI simply by sending { state: "DOING", text: "..." }
      if (payload.state) {
        const nextState = State[payload.state.toUpperCase()];
        if (nextState) {

          if (nextState === State.DOING || payload.text) {
            setState(State.DOING, {
              tier: payload.tier || "",
              text: payload.text || "Processing...",
              force: true
            });

            // Auto-reset UI if requested
            if (payload.auto_reset !== false) {
              setTimeout(() => setState(State.IDLE, { force: true }), 3000);
            }
          } else {
            // Just a normal state change (e.g. LISTENING or LOADING)
            setState(nextState, { force: true });
          }
        }
      }
    });

    app.ws.addEventListener("error", (err) => {
      console.error("[WS] Connection Error:", err);
      if (bridge.logError) {
        bridge.logError("WebSocket connection failed to ws://127.0.0.1:8000/ws");
      }
    });

    app.ws.addEventListener("close", (e) => {
      console.warn("[WS] Connection Closed:", e.code, e.reason);
      if (bridge.logError) {
        bridge.logError(`WebSocket closed: ${e.code} ${e.reason}`);
      }
      scheduleReconnect();
    });
  } catch {
    scheduleReconnect();
  }
}

/* ── Events ── */
wrapper.addEventListener("mouseenter", () => app.visible && setMouseEnabled(true));
wrapper.addEventListener("mouseleave", () => app.visible && setMouseEnabled(false));
document.addEventListener("mousemove", (event) => {
  if (!app.visible) return setMouseEnabled(false);
  const rect = wrapper.getBoundingClientRect();
  setMouseEnabled(event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") return bridge.hideWindow();
});

// Since Python backend controls wake now, the hotkey could just send a manual override if we wanted to
bridge.onStartListening(() => {
  if (app.ws && app.ws.readyState === WebSocket.OPEN) {
    app.ws.send(JSON.stringify({ type: "hotkey_pressed", payload: true }));
  }
});

bridge.onOverlayHidden(async () => {
  clearCommandContext();
  app.visible = true;
  wrapper.classList.remove("hidden");
  setMouseEnabled(false);
  setState(State.IDLE, { force: true });
});

window.addEventListener("beforeunload", async () => {
  app.isDisposed = true;
  await stopAudioStreaming();
  if (app.reconnectTimer) clearTimeout(app.reconnectTimer);
  if (app.ws && app.ws.readyState <= WebSocket.OPEN) app.ws.close();
});

/* ── Init ── */
setState(State.IDLE, { force: true });
wrapper.classList.remove("hidden");
setMouseEnabled(false);

// 1. Connect WS
connectWebSocket();

// 2. Start continuously recording and streaming Base64 WAV chunks
startAudioStreaming();
