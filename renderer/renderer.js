/* ─────────────────────────────────────────────────────────────
   MOONWALK – Renderer (Raw Audio Streaming to Backend)
   ───────────────────────────────────────────────────────────── */

const State = Object.freeze({
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  LOADING: "LOADING",
  DOING: "DOING",
  RESPONDING: "RESPONDING"
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
  logInfo: () => { },
  openDashboard: () => { }
};

/* ── IPC Bridge ── */

/* ── DOM Refs ── */
const wrapper = document.getElementById("ui-wrapper");
const uiSpeech = document.getElementById("ui-speech");
const uiLoading = document.getElementById("ui-loading");
const uiDoing = document.getElementById("ui-doing");
const uiResponse = document.getElementById("ui-response");
const statusEl = document.getElementById("status-text");
const doingTextEl = document.getElementById("doing-text");
const appIconEl = document.getElementById("app-icon");
const responseTextEl = document.getElementById("response-text");
const responseCursorEl = document.getElementById("response-cursor");
const responseDismissEl = document.getElementById("response-dismiss");
const keyCapture = document.getElementById("key-capture");

/* ── New Panel DOM Refs ── */
const agentBubble = document.getElementById("agent-bubble");
const agentDot = document.getElementById("agent-dot");
const toolsPopup = document.getElementById("tools-popup");
const agentDrawer = document.getElementById("agent-drawer");
const drawerThreads = document.getElementById("drawer-threads");
const drawerCount = document.getElementById("drawer-count");
const drawerEmpty = document.getElementById("drawer-empty");

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
  autoResetTimer: null,
  streamTimer: null,       // Character-by-character typing interval
  streamQueue: "",         // Text waiting to be streamed
  streamIndex: 0,          // Current position in stream

  // Audio Streaming Pipeline
  audioStream: null,
  audioContext: null,
  sourceNode: null,
  scriptProcessor: null,

  // Agent tracking
  agents: {},           // id -> agent state
  runningAgents: 0,
  totalAgents: 0,
  toolsOpen: false,
  drawerOpen: false,
  activeMenu: null,     // Currently open context menu agent ID
};

/* ── UI State Management ── */
function setIslandState(nextStateClass) {
  wrapper.className = `glass-pill ${nextStateClass}`;
}

function setState(next, { tier = "", text = null, appName = "", force = false } = {}) {
  if (!force && app.current === next) return;
  app.current = next;

  uiSpeech.classList.add('hidden');
  uiLoading.classList.add('hidden');
  uiDoing.classList.add('hidden');

  // Hide response card when switching to non-response states
  if (next !== State.RESPONDING) {
    dismissResponseCard(true);
  }

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

    if (options.iconUrl) {
      appIconEl.src = options.iconUrl;
      appIconEl.style.display = 'block';
    } else {
      appIconEl.style.display = 'none';
    }
  }
  else if (next === State.RESPONDING) {
    setIslandState('state-loading');
    uiLoading.classList.remove('hidden');
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

/* ── Response Card: Streaming Text ── */

function showResponseCard(fullText, awaitInput = false) {
  // Cancel any existing stream
  if (app.streamTimer) {
    clearInterval(app.streamTimer);
    app.streamTimer = null;
  }
  if (app.autoResetTimer) {
    clearTimeout(app.autoResetTimer);
    app.autoResetTimer = null;
  }

  // Reset card content
  responseTextEl.textContent = "";
  responseCursorEl.classList.remove('hidden');
  app.streamQueue = fullText;
  app.streamIndex = 0;

  // Show the card with animation
  uiResponse.classList.remove('hidden', 'dismissing');

  // Switch pill to the loading dots while streaming
  setState(State.RESPONDING, { force: true });

  // Stream characters
  const CHAR_DELAY = 25; // ms per character
  app.streamTimer = setInterval(() => {
    if (app.streamIndex >= app.streamQueue.length) {
      // Done streaming
      clearInterval(app.streamTimer);
      app.streamTimer = null;
      responseCursorEl.classList.add('hidden');

      if (awaitInput) {
        // ── Awaiting user reply: switch pill to LISTENING ──
        setIslandState('state-listening');
        uiLoading.classList.add('hidden');
        uiSpeech.classList.remove('hidden');
        statusEl.innerText = "Listening...";
        app.current = State.LISTENING;

        // Longer timeout for await mode
        app.autoResetTimer = setTimeout(() => {
          dismissResponseCard();
          setState(State.IDLE, { force: true });
          clearCommandContext();
          app.autoResetTimer = null;
        }, 30000);
      } else {
        // ── Final response: switch pill back to idle ──
        setIslandState('state-idle');
        uiLoading.classList.add('hidden');
        uiSpeech.classList.remove('hidden');
        statusEl.innerText = "Hey Moonwalk";

        app.autoResetTimer = setTimeout(() => {
          dismissResponseCard();
          app.current = State.IDLE;
          clearCommandContext();
          app.autoResetTimer = null;
        }, 10000);
      }
      return;
    }

    // Add next character
    responseTextEl.textContent += app.streamQueue[app.streamIndex];
    app.streamIndex++;

    // Auto-scroll to bottom
    uiResponse.scrollTop = uiResponse.scrollHeight;
  }, CHAR_DELAY);
}

function dismissResponseCard(instant = false) {
  // Stop any ongoing stream
  if (app.streamTimer) {
    clearInterval(app.streamTimer);
    app.streamTimer = null;
  }

  if (instant || uiResponse.classList.contains('hidden')) {
    uiResponse.classList.add('hidden');
    uiResponse.classList.remove('dismissing');
    return;
  }

  // Animate out
  uiResponse.classList.add('dismissing');
  setTimeout(() => {
    uiResponse.classList.add('hidden');
    uiResponse.classList.remove('dismissing');
  }, 300);
}

// Dismiss button
responseDismissEl.addEventListener('click', () => {
  if (app.autoResetTimer) {
    clearTimeout(app.autoResetTimer);
    app.autoResetTimer = null;
  }
  dismissResponseCard();
  setState(State.IDLE, { force: true });
  clearCommandContext();
});

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
    // Higher frequency chunks for lower latency (1024 samples @ 16kHz = 64ms)
    app.scriptProcessor = app.audioContext.createScriptProcessor(1024, 1, 1);

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
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }

      console.log("[WS] Received:", msg);

      // ── Agent message types ──

      // 1. "thinking" — Agent is reasoning (show bouncing dots)
      if (msg.type === "thinking" || msg.type === "progress" || msg.state === "state-loading") {
        setState(State.LOADING, { force: true });
        return;
      }

      // 2. "doing" — Agent is executing a tool (show spinner + action text)
      if (msg.type === "doing") {
        // Cancel any pending auto-reset so sequential tool steps show properly
        if (app.autoResetTimer) {
          clearTimeout(app.autoResetTimer);
          app.autoResetTimer = null;
        }
        setState(State.DOING, {
          text: msg.text || "Working...",
          appName: msg.app || "",
          iconUrl: msg.icon_url || "",
          force: true
        });
        return;
      }

      // 3. "response" — Agent finished, show final answer
      if (msg.type === "response" || msg.type === "action") {
        const payload = msg.payload || {};
        const text = payload.text || "Done!";
        const display = payload.display || (text.length > 40 ? "card" : "pill");
        const awaitInput = payload.await_input || false;
        app.detectedApp = payload.app || "";

        if (display === "card") {
          // ── Conversational response → show streaming card
          showResponseCard(text, awaitInput);
        } else {
          // ── Tool confirmation → show in pill
          setState(State.DOING, {
            text: text,
            appName: payload.app || "",
            iconUrl: payload.icon_url || "",
            force: true
          });

          if (msg.auto_reset !== false) {
            app.autoResetTimer = setTimeout(() => {
              if (app.current === State.DOING) {
                setState(State.IDLE, { force: true });
                clearCommandContext();
              }
              app.autoResetTimer = null;
            }, 8000);
          }
        }
        return;
      }

      // 4. "sub_agent_update" — Background agent status change
      if (msg.type === "sub_agent_update") {
        handleAgentUpdate(msg);
        return;
      }

      // 5. "status" — Direct state transitions (idle, listening, etc.)
      const stateStr = msg.state || (msg.type === "status" ? msg.state : null);
      if (stateStr) {
        if (app.autoResetTimer) {
          clearTimeout(app.autoResetTimer);
          app.autoResetTimer = null;
        }
        const nextState = State[stateStr.toUpperCase().replace("STATE-", "")];
        if (nextState) {
          setState(nextState, { force: true });
          if (nextState === State.IDLE) clearCommandContext();
        }
      }

      // 6. "ipc_trigger" — Native electron control commands from the backend LLM
      if (msg.type === "ipc_trigger") {
        if (msg.command === "open-dashboard" && bridge.openDashboard) {
          bridge.openDashboard();
        }
        return;
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
// Hit-test: check if mouse is over any interactive element
function isOverInteractive(event) {
  const x = event.clientX;
  const y = event.clientY;
  const rects = [
    wrapper.getBoundingClientRect(),
    agentBubble.getBoundingClientRect(),
  ];
  // Check tools popup if visible
  if (app.toolsOpen) rects.push(toolsPopup.getBoundingClientRect());
  // Check drawer if visible
  if (app.drawerOpen) rects.push(agentDrawer.getBoundingClientRect());
  // Check response card if visible
  if (!uiResponse.classList.contains('hidden')) rects.push(uiResponse.getBoundingClientRect());

  return rects.some(r => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
}

document.addEventListener("mousemove", (event) => {
  if (!app.visible) return setMouseEnabled(false);
  setMouseEnabled(isOverInteractive(event));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (app.toolsOpen) return toggleToolsPopup(false);
    if (app.drawerOpen) return toggleDrawer(false);
    return bridge.hideWindow();
  }
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

/* ══════════════════════════════════════════════════════════════
   AGENT BUBBLE, TOOLS POPUP & BOTTOM DRAWER
   ══════════════════════════════════════════════════════════════ */

// ── Bubble click → toggle tools popup + agent drawer ──
agentBubble.addEventListener("click", (e) => {
  e.stopPropagation();
  const opening = !app.toolsOpen;
  toggleToolsPopup(opening);
  toggleDrawer(opening);
});

function toggleToolsPopup(show) {
  app.toolsOpen = show;
  if (show) {
    toolsPopup.classList.remove("hidden");
    // Trigger reflow for animation
    void toolsPopup.offsetWidth;
    toolsPopup.classList.add("visible");
    agentBubble.classList.add("active");
  } else {
    toolsPopup.classList.remove("visible");
    agentBubble.classList.remove("active");
    setTimeout(() => toolsPopup.classList.add("hidden"), 300);
  }
}

// Close popup when clicking outside
document.addEventListener("click", (e) => {
  if (app.toolsOpen && !toolsPopup.contains(e.target) && !agentBubble.contains(e.target)) {
    toggleToolsPopup(false);
  }
  // Close any open context menus
  if (app.activeMenu) {
    const menu = document.querySelector(`.agent-context-menu[data-agent="${app.activeMenu}"]`);
    if (menu && !menu.contains(e.target)) {
      menu.remove();
      app.activeMenu = null;
    }
  }
});

// ── Drawer toggle ──
function toggleDrawer(show) {
  app.drawerOpen = show;
  if (show) {
    agentDrawer.classList.remove("hidden");
    // Trigger reflow for slide-up
    void agentDrawer.offsetWidth;
    agentDrawer.classList.add("visible");
  } else {
    agentDrawer.classList.remove("visible");
    setTimeout(() => agentDrawer.classList.add("hidden"), 400);
  }
}

// Drawer handle click to toggle
document.getElementById("drawer-handle").addEventListener("click", () => {
  toggleDrawer(!app.drawerOpen);
});

// ── Agent Update Handler ──
function handleAgentUpdate(msg) {
  const id = msg.agent_id || msg.id || "unknown";
  const status = msg.status || "";

  if (status === "spawned") {
    app.agents[id] = {
      id,
      task: msg.task || "Background Task",
      status: "running",
      createdAt: Date.now(),
      iterations: 0,
      logs: [],
    };
    app.runningAgents++;
    app.totalAgents++;
    // Auto-open drawer on first agent
    if (!app.drawerOpen) toggleDrawer(true);
  } else if (status === "log") {
    if (app.agents[id]) {
      app.agents[id].logs.push(msg.message || "");
    }
  } else if (status === "iteration") {
    if (app.agents[id]) {
      app.agents[id].iterations = msg.iteration || 0;
    }
  } else if (status === "completed") {
    if (app.agents[id]) {
      app.agents[id].status = "completed";
      app.agents[id].result = msg.result || "Done";
    }
    app.runningAgents = Math.max(0, app.runningAgents - 1);
  } else if (status === "error") {
    if (app.agents[id]) {
      app.agents[id].status = "error";
      app.agents[id].error = msg.error || "Unknown error";
    }
    app.runningAgents = Math.max(0, app.runningAgents - 1);
  } else if (status === "stopped") {
    if (app.agents[id]) {
      app.agents[id].status = "stopped";
    }
    app.runningAgents = Math.max(0, app.runningAgents - 1);
  }

  renderAgentDrawer();
}

// ── Live Timer Polling ──
// Auto-refresh the drawer every second so the elapsed time increments 
// (stuck at '0s' otherwise since WebSockets only fire on state change)
setInterval(() => {
  if (app.runningAgents > 0 || app.drawerOpen) {
    renderAgentDrawer();
  }
}, 1000);

// ── Render Agent Thread Cards ──
function renderAgentDrawer() {
  const ids = Object.keys(app.agents);

  // Update counter
  drawerCount.textContent = app.runningAgents > 0
    ? `${app.runningAgents} running`
    : `${ids.length} agent${ids.length !== 1 ? "s" : ""}`;

  // Update notification dot
  if (app.runningAgents > 0) {
    agentDot.classList.remove("hidden");
  } else {
    agentDot.classList.add("hidden");
  }

  // Render cards
  if (ids.length === 0) {
    drawerEmpty.style.display = "block";
    // Remove all cards
    drawerThreads.querySelectorAll(".agent-thread-card").forEach(c => c.remove());
    return;
  }

  drawerEmpty.style.display = "none";

  // Remove stale cards
  drawerThreads.querySelectorAll(".agent-thread-card").forEach(c => {
    if (!app.agents[c.dataset.agentId]) c.remove();
  });

  // Add or update cards
  ids.forEach(id => {
    const agent = app.agents[id];
    let card = drawerThreads.querySelector(`.agent-thread-card[data-agent-id="${id}"]`);

    if (!card) {
      card = document.createElement("div");
      card.className = "agent-thread-card";
      card.dataset.agentId = id;
      drawerThreads.appendChild(card);
    }

    const elapsed = getElapsed(agent.createdAt);
    const healthClass = agent.status === "error" ? "error" : agent.status === "completed" ? "healthy" : "working";
    const healthWidth = agent.status === "completed" ? 100 : agent.status === "error" ? 100 : Math.min(95, Math.max(15, (agent.iterations || 1) * 8));
    const statusText = agent.status === "running" ? `Running · ${elapsed}` : agent.status === "completed" ? "Completed" : agent.status === "error" ? "Error" : "Stopped";
    const statusClass = agent.status;

    card.innerHTML = `
      <div class="agent-card-header">
        <span class="agent-card-title">${escapeHtml(agent.task.substring(0, 40))}</span>
        <button class="agent-card-menu" data-id="${id}">⋮</button>
      </div>
      <div class="agent-health-bar">
        <div class="agent-health-fill ${healthClass}" style="width: ${healthWidth}%"></div>
      </div>
      <div class="agent-card-footer">
        <span class="agent-card-status ${statusClass}">${statusText}</span>
        <span class="agent-card-time">${elapsed}</span>
      </div>
    `;

    // 3-dot menu handler
    card.querySelector(".agent-card-menu").addEventListener("click", (e) => {
      e.stopPropagation();
      showAgentMenu(id, e.target);
    });
  });
}

function showAgentMenu(agentId, anchorEl) {
  // Close existing
  document.querySelectorAll(".agent-context-menu").forEach(m => m.remove());
  app.activeMenu = agentId;

  const card = anchorEl.closest(".agent-thread-card");
  const menu = document.createElement("div");
  menu.className = "agent-context-menu";
  menu.dataset.agent = agentId;
  menu.innerHTML = `
    <button data-action="logs">📋 View Logs</button>
    <button data-action="dashboard">📊 Open Dashboard</button>
    <button data-action="stop" class="danger">⏹ Stop Agent</button>
  `;

  menu.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const action = btn.dataset.action;
      if (action === "logs") {
        const agent = app.agents[agentId];
        if (agent) console.log(`[Agent ${agentId} Logs]`, agent.logs);
        alert(`Agent ${agentId} Logs:\n${(agent?.logs || []).join("\n") || "No logs yet."}`);
      } else if (action === "dashboard") {
        bridge.openDashboard();
      } else if (action === "stop") {
        if (app.ws && app.ws.readyState === WebSocket.OPEN) {
          app.ws.send(JSON.stringify({ type: "stop_agent", agent_id: agentId }));
        }
      }
      menu.remove();
      app.activeMenu = null;
    });
  });

  card.appendChild(menu);
}

// ── Utility Functions ──
function getElapsed(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
