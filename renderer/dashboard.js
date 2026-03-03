/* ─────────────────────────────────────────────────────────────
   MOONWALK – Agent Dashboard Logic
   Connects to the backend WebSocket to receive real-time
   sub_agent_update messages and manage agent lifecycle.
   ───────────────────────────────────────────────────────────── */

const WS_URL = "ws://127.0.0.1:8000/ws";

/* ── State ── */
const dash = {
    ws: null,
    reconnectTimer: null,
    reconnectDelay: 1000,
    agents: {},         // agentId -> { task, status, logs, result, error, iterations, created_at, ... }
    selectedId: null,
    refreshTimer: null,  // Auto-refresh elapsed times
};

/* ── DOM Refs ── */
const sidebar = document.getElementById("sidebar");
const sidebarEmpty = document.getElementById("sidebar-empty");
const detailPanel = document.getElementById("detail-panel");
const detailEmpty = document.getElementById("detail-empty");
const detailContent = document.getElementById("detail-content");
const detailStatusDot = document.getElementById("detail-status-dot");
const detailTitle = document.getElementById("detail-title");
const detailTask = document.getElementById("detail-task");
const detailElapsed = document.getElementById("detail-elapsed");
const detailIterations = document.getElementById("detail-iterations");
const checklistSection = document.getElementById("checklist-section");
const checklistList = document.getElementById("checklist-list");
const logViewer = document.getElementById("log-viewer");
const logEmpty = document.getElementById("log-empty");
const resultSection = document.getElementById("result-section");
const resultText = document.getElementById("result-text");
const errorSection = document.getElementById("error-section");
const errorText = document.getElementById("error-text");
const reviewSection = document.getElementById("review-section");
const reviewTopic = document.getElementById("review-topic");
const reviewSummary = document.getElementById("review-summary");
const reviewFeedback = document.getElementById("review-feedback");
const btnApprove = document.getElementById("btn-approve");
const btnFeedback = document.getElementById("btn-feedback");
const agentCountEl = document.getElementById("agent-count");
const btnPause = document.getElementById("btn-pause");
const btnStop = document.getElementById("btn-stop");
const btnRefresh = document.getElementById("btn-refresh");


/* ═══════════════════════════════════════════════════════════════
   Utilities
   ═══════════════════════════════════════════════════════════════ */

function formatElapsed(seconds) {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
}

function now() { return Date.now() / 1000; }


/* ═══════════════════════════════════════════════════════════════
   Sidebar Rendering
   ═══════════════════════════════════════════════════════════════ */

function renderSidebar() {
    const ids = Object.keys(dash.agents);

    // Show/hide empty state
    if (ids.length === 0) {
        sidebarEmpty.classList.remove("hidden");
        agentCountEl.textContent = "0 agents";
        return;
    }

    sidebarEmpty.classList.add("hidden");

    const runningCount = ids.filter(id => dash.agents[id].status === "running").length;
    const totalCount = ids.length;
    agentCountEl.textContent = runningCount > 0
        ? `${runningCount} running / ${totalCount} total`
        : `${totalCount} agent${totalCount !== 1 ? "s" : ""}`;

    // Remove old cards
    sidebar.querySelectorAll(".agent-card").forEach(el => el.remove());

    // Sort: running first, then by created_at descending
    const sorted = ids.sort((a, b) => {
        const statusOrder = { running: 0, error: 1, stopping: 2, stopped: 3, completed: 4 };
        const sa = statusOrder[dash.agents[a].status] ?? 5;
        const sb = statusOrder[dash.agents[b].status] ?? 5;
        if (sa !== sb) return sa - sb;
        return (dash.agents[b].created_at || 0) - (dash.agents[a].created_at || 0);
    });

    for (const id of sorted) {
        const agent = dash.agents[id];
        const card = document.createElement("div");
        card.className = `agent-card${dash.selectedId === id ? " selected" : ""}`;
        card.dataset.agentId = id;

        const elapsed = agent.completed_at
            ? (agent.completed_at - agent.created_at)
            : (now() - agent.created_at);

        card.innerHTML = `
      <div class="agent-card-header">
        <div class="status-dot ${agent.status}"></div>
        <span class="agent-id">${id}</span>
      </div>
      <div class="agent-task">${escapeHtml(agent.task || "Unknown task")}</div>
      <div class="agent-card-footer">
        <span class="agent-elapsed">${formatElapsed(elapsed)}</span>
        <span class="log-count-badge">${(agent.logs || []).length} logs</span>
      </div>
    `;

        card.addEventListener("click", () => selectAgent(id));
        sidebar.appendChild(card);
    }
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}


/* ═══════════════════════════════════════════════════════════════
   Detail Panel Rendering
   ═══════════════════════════════════════════════════════════════ */

function selectAgent(id) {
    dash.selectedId = id;
    renderSidebar(); // Update selected highlight
    renderDetail();
}

function renderDetail() {
    if (!dash.selectedId || !dash.agents[dash.selectedId]) {
        detailContent.classList.add("hidden");
        detailEmpty.classList.remove("hidden");
        return;
    }

    detailEmpty.classList.add("hidden");
    detailContent.classList.remove("hidden");

    const agent = dash.agents[dash.selectedId];

    // Status dot
    detailStatusDot.className = `detail-status-dot ${agent.status}`;
    detailStatusDot.style.background = `var(--status-${agent.status === "stopping" ? "stopped" : agent.status})`;

    // Header info
    detailTitle.textContent = dash.selectedId;
    detailTask.textContent = agent.task || "No task description";

    // Meta badges
    const elapsed = agent.completed_at
        ? (agent.completed_at - agent.created_at)
        : (now() - agent.created_at);
    detailElapsed.textContent = formatElapsed(elapsed);
    detailIterations.textContent = `${agent.iterations || 0} iterations`;

    // Checklist section
    if (agent.checklist && agent.checklist.length > 0) {
        checklistSection.classList.remove("hidden");
        renderChecklist(agent.checklist);
    } else {
        checklistSection.classList.add("hidden");
    }

    // Log viewer
    renderLogs(agent);

    // Result section
    if (agent.result) {
        resultSection.classList.remove("hidden");
        resultText.textContent = agent.result;
    } else {
        resultSection.classList.add("hidden");
    }

    // Error section
    if (agent.error) {
        errorSection.classList.remove("hidden");
        errorText.textContent = agent.error;
    } else {
        errorSection.classList.add("hidden");
    }

    // Review section
    if (agent.status === "paused_for_review") {
        reviewSection.classList.remove("hidden");
        reviewTopic.textContent = agent.review_topic || "Please review the agent's progress.";
        reviewSummary.textContent = agent.result || "";
    } else {
        reviewSection.classList.add("hidden");
    }

    // Toolbar buttons
    const isActive = agent.status === "running";
    btnPause.disabled = !isActive;
    btnStop.disabled = !isActive && agent.status !== "stopping";
}

function renderChecklist(tasks) {
    if (checklistList.children.length === tasks.length) return; // Prevent unnecessary DOM clears if same length

    checklistList.innerHTML = "";
    tasks.forEach(task => {
        const li = document.createElement("li");
        li.innerHTML = `<span class="check-box"></span><span class="task-text">${escapeHtml(task)}</span>`;
        checklistList.appendChild(li);
    });
}

function renderLogs(agent) {
    const logs = agent.logs || [];

    if (logs.length === 0) {
        logEmpty.classList.remove("hidden");
        // Remove all log entries
        logViewer.querySelectorAll(".log-entry").forEach(el => el.remove());
        return;
    }

    logEmpty.classList.add("hidden");

    // Only add new entries (avoid full re-render for performance)
    const existingCount = logViewer.querySelectorAll(".log-entry").length;

    for (let i = existingCount; i < logs.length; i++) {
        const log = logs[i];
        const entry = document.createElement("div");
        entry.className = `log-entry${log.startsWith("[Thought]") ? " thought" : ""}`;

        // Parse timestamp if present
        const match = log.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)/);
        if (match) {
            entry.innerHTML = `<span class="log-time">${match[1]}</span><span class="log-msg">${escapeHtml(match[2])}</span>`;
        } else {
            entry.innerHTML = `<span class="log-msg">${escapeHtml(log)}</span>`;
        }

        logViewer.appendChild(entry);
    }

    // Auto-scroll to bottom
    logViewer.scrollTop = logViewer.scrollHeight;
}


/* ═══════════════════════════════════════════════════════════════
   WebSocket Connection
   ═══════════════════════════════════════════════════════════════ */

function connectWebSocket() {
    if (dash.ws && (dash.ws.readyState === WebSocket.OPEN || dash.ws.readyState === WebSocket.CONNECTING)) {
        return;
    }

    try {
        dash.ws = new WebSocket(WS_URL);

        dash.ws.addEventListener("open", () => {
            console.log("[Dashboard] Connected to backend");
            dash.reconnectDelay = 1000;

            // Request a full state snapshot
            dash.ws.send(JSON.stringify({ type: "dashboard_sync" }));
        });

        dash.ws.addEventListener("message", (event) => {
            if (typeof event.data !== "string") return;
            let msg;
            try { msg = JSON.parse(event.data); } catch { return; }

            handleMessage(msg);
        });

        dash.ws.addEventListener("close", () => {
            console.log("[Dashboard] Disconnected, reconnecting...");
            scheduleReconnect();
        });

        dash.ws.addEventListener("error", () => {
            scheduleReconnect();
        });

    } catch {
        scheduleReconnect();
    }
}

function scheduleReconnect() {
    if (dash.reconnectTimer) return;
    dash.reconnectTimer = setTimeout(() => {
        dash.reconnectTimer = null;
        connectWebSocket();
    }, dash.reconnectDelay);
    dash.reconnectDelay = Math.min(dash.reconnectDelay * 1.5, 10000);
}


/* ═══════════════════════════════════════════════════════════════
   Message Handling
   ═══════════════════════════════════════════════════════════════ */

function handleMessage(msg) {
    // Full state snapshot from cloud server
    if (msg.type === "dashboard_state") {
        const agents = msg.agents || {};
        for (const [id, state] of Object.entries(agents)) {
            dash.agents[id] = state;
        }
        renderSidebar();
        if (dash.selectedId) renderDetail();
        return;
    }

    // Real-time sub-agent update
    if (msg.type === "sub_agent_update") {
        const id = msg.agent_id;
        if (!id) return;

        // Create or update agent state
        if (!dash.agents[id]) {
            dash.agents[id] = {
                task: msg.task || "Unknown",
                status: msg.status || "running",
                logs: [],
                checklist: [],
                result: null,
                error: null,
                iterations: 0,
                created_at: now(),
                completed_at: null,
            };
        }

        const agent = dash.agents[id];

        // Update based on message status
        if (msg.status === "spawned") {
            agent.task = msg.task || agent.task;
            agent.status = "running";
            agent.created_at = msg.created_at || now();
        }
        else if (msg.status === "progress") {
            if (msg.message) {
                agent.logs.push(`[${new Date().toLocaleTimeString()}] ${msg.message}`);
            }
        }
        else if (msg.status === "checklist_updated") {
            if (msg.checklist) {
                agent.checklist = msg.checklist;
                agent.status = "running"; // Keep status running
            }
        }
        else if (msg.status === "log") {
            if (msg.message) {
                agent.logs.push(msg.message);
            }
        }
        else if (msg.status === "completed") {
            agent.status = "completed";
            agent.result = msg.result || agent.result;
            agent.completed_at = now();
        }
        else if (msg.status === "error") {
            agent.status = "error";
            agent.error = msg.error || "Unknown error";
            agent.completed_at = now();
        }
        else if (msg.status === "stopped") {
            agent.status = "stopped";
            agent.completed_at = now();
        }
        else if (msg.status === "paused_for_review") {
            agent.status = "paused_for_review";
            agent.review_topic = msg.review_topic;
            agent.result = msg.result; // optionally holds summary
        }

        if (msg.iterations !== undefined) {
            agent.iterations = msg.iterations;
        }

        // Re-render
        renderSidebar();
        if (dash.selectedId === id) renderDetail();
        return;
    }
}


/* ═══════════════════════════════════════════════════════════════
   Toolbar Actions
   ═══════════════════════════════════════════════════════════════ */

btnStop.addEventListener("click", () => {
    if (!dash.selectedId || !dash.ws) return;
    dash.ws.send(JSON.stringify({
        type: "dashboard_action",
        action: "stop_agent",
        agent_id: dash.selectedId,
    }));
    // Optimistic update
    if (dash.agents[dash.selectedId]) {
        dash.agents[dash.selectedId].status = "stopping";
        renderSidebar();
        renderDetail();
    }
});

btnPause.addEventListener("click", () => {
    if (!dash.selectedId || !dash.ws) return;
    dash.ws.send(JSON.stringify({
        type: "dashboard_action",
        action: "pause_agent",
        agent_id: dash.selectedId,
    }));
});

btnRefresh.addEventListener("click", () => {
    if (dash.ws && dash.ws.readyState === WebSocket.OPEN) {
        dash.ws.send(JSON.stringify({ type: "dashboard_sync" }));
    }
});

btnApprove.addEventListener("click", () => {
    if (!dash.selectedId || !dash.ws) return;
    dash.ws.send(JSON.stringify({
        type: "resume_agent",
        agent_id: dash.selectedId,
        action: "approve",
        feedback: "Approved by user."
    }));

    // Optimistic update
    reviewSection.classList.add("hidden");
    if (dash.agents[dash.selectedId]) {
        dash.agents[dash.selectedId].status = "running";
        renderSidebar();
        renderDetail();
    }
});

btnFeedback.addEventListener("click", () => {
    if (!dash.selectedId || !dash.ws) return;
    const feedbackText = reviewFeedback.value.trim();
    if (!feedbackText) return;

    dash.ws.send(JSON.stringify({
        type: "resume_agent",
        agent_id: dash.selectedId,
        action: "feedback",
        feedback: feedbackText
    }));

    reviewFeedback.value = "";

    // Optimistic update
    reviewSection.classList.add("hidden");
    if (dash.agents[dash.selectedId]) {
        dash.agents[dash.selectedId].status = "running";
        renderSidebar();
        renderDetail();
    }
});


/* ═══════════════════════════════════════════════════════════════
   Elapsed Time Auto-Refresh
   ═══════════════════════════════════════════════════════════════ */

dash.refreshTimer = setInterval(() => {
    // Re-render sidebar elapsed times for running agents
    const hasRunning = Object.values(dash.agents).some(a => a.status === "running");
    if (hasRunning) {
        renderSidebar();
        if (dash.selectedId && dash.agents[dash.selectedId]?.status === "running") {
            renderDetail();
        }
    }
}, 1000);


/* ═══════════════════════════════════════════════════════════════
   Init
   ═══════════════════════════════════════════════════════════════ */

// Check if we opened the dashboard pre-selected to a specific agent
const urlParams = new URLSearchParams(window.location.search);
const preselectedAgent = urlParams.get('agent');
if (preselectedAgent) {
    dash.selectedId = preselectedAgent;
}

renderSidebar();
connectWebSocket();
