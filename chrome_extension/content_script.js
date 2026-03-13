(function() {
// This IIFE prevents "Identifier has already been declared" errors 
// when the extension is injected multiple times into the same page.

if (window.__moonwalk_injected__) return;
window.__moonwalk_injected__ = true;

// ═══════════════════════════════════════════════════════════════
//  Moonwalk — Content Script v2
//  1. data-agent-id sequential tagging for instant element lookup
//  2. Aggressive DOM distillation (strips noise, pruned a11y tree)
//  3. MutationObserver-driven DOM change events for verify phase
// ═══════════════════════════════════════════════════════════════

const INTERACTIVE_SELECTOR = [
  "button",
  "a[href]",
  "input",
  "textarea",
  "select",
  "summary",
  "[contenteditable='true']",
  "[contenteditable='']",
  "[tabindex]:not([tabindex='-1'])",
  "img[alt]",
  "video",
  "[role='button']",
  "[role='link']",
  "[role='textbox']",
  "[role='searchbox']",
  "[role='combobox']",
  "[role='tab']",
  "[role='menuitem']",
  "[role='option']",
  "[role='radio']",
  "[role='checkbox']",
  "[role='switch']",
  "[role='slider']",
  "[role='treeitem']",
  "[role='gridcell']",
].join(",");

const READABLE_SELECTOR = [
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "p",
  "li",
  "label",
  "blockquote",
  "figcaption",
  "caption",
  "td",
  "th",
  "pre",
  "code",
].join(",");

// ── Noise tags to strip during DOM distillation ──
const NOISE_TAGS = new Set([
  "svg", "script", "style", "noscript", "link", "meta",
  "iframe", "object", "embed", "applet", "param", "source", "track",
]);

// ── Agent ID state ──
let _nextAgentId = 1;
const _agentIdMap = new Map(); // agent_id → Element (O(1) lookup)

// ═══════════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════════

function textOf(node) {
  return (node?.innerText || node?.textContent || "").trim().replace(/\s+/g, " ").slice(0, 160);
}

function isVisible(el) {
  if (NOISE_TAGS.has(el.tagName.toLowerCase())) return false;
  const style = window.getComputedStyle(el);
  if (style.visibility === "hidden" || style.display === "none" || parseFloat(style.opacity) === 0) {
    return false;
  }
  if (el.getAttribute("aria-hidden") === "true") return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function isInViewport(el) {
  const rect = el.getBoundingClientRect();
  return (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < window.innerHeight &&
    rect.left < window.innerWidth
  );
}

function roleOf(el) {
  return el.getAttribute("role") || el.tagName.toLowerCase();
}

function actionTypesFor(el) {
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute("type") || "").toLowerCase();
  const role = roleOf(el).toLowerCase();
  const actions = new Set();

  if (
    ["button", "a", "summary"].includes(tag) ||
    ["button", "link", "tab", "menuitem", "switch", "treeitem", "gridcell"].includes(role)
  ) {
    actions.add("click");
  }
  if (
    ["input", "textarea"].includes(tag) ||
    ["textbox", "searchbox", "combobox"].includes(role) ||
    el.isContentEditable
  ) {
    actions.add("click");
    actions.add("type");
  }
  if (tag === "select" || ["combobox", "listbox", "option"].includes(role)) {
    actions.add("select");
  }
  if (tag === "input" && ["checkbox", "radio"].includes(type)) {
    actions.add("click");
  }
  if (tag === "input" && type === "range") {
    actions.add("click");
  }
  if (role === "slider") {
    actions.add("click");
  }
  if (tag === "img" || tag === "video") {
    actions.add("click");
  }
  if (actions.size === 0 && el.hasAttribute("tabindex")) {
    actions.add("click");
  }

  return [...actions];
}

function domPath(el) {
  const parts = [];
  let current = el;
  while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 8) {
    const tag = current.tagName.toLowerCase();
    const siblings = current.parentElement
      ? [...current.parentElement.children].filter((child) => child.tagName === current.tagName)
      : [current];
    const index = siblings.indexOf(current);
    parts.unshift(`${tag}:${index}`);
    current = current.parentElement;
  }
  return parts.join(">");
}

function ancestorLabels(el) {
  const labels = [];
  let current = el.parentElement;
  let depth = 0;
  while (current && labels.length < 3 && depth < 6) {
    depth++;
    const ariaLabel = (current.getAttribute("aria-label") || "").trim();
    if (ariaLabel) {
      labels.push(ariaLabel.slice(0, 80));
      current = current.parentElement;
      continue;
    }
    const innerText = textOf(current);
    if (innerText && innerText.length <= 120) {
      labels.push(innerText.slice(0, 80));
    }
    current = current.parentElement;
  }
  return labels;
}

// ═══════════════════════════════════════════════════════════════
//  1. data-agent-id Tagging
// ═══════════════════════════════════════════════════════════════

function assignAgentId(el) {
  const existing = el.getAttribute("data-agent-id");
  if (existing) {
    const id = parseInt(existing, 10);
    if (!isNaN(id) && id > 0) {
      _agentIdMap.set(id, el);
      _nextAgentId = Math.max(_nextAgentId, id + 1);
      return id;
    }
  }
  const id = _nextAgentId++;
  el.setAttribute("data-agent-id", String(id));
  _agentIdMap.set(id, el);
  return id;
}

function lookupByAgentId(agentId) {
  const fromMap = _agentIdMap.get(agentId);
  if (fromMap && fromMap.isConnected) return fromMap;
  const el = document.querySelector('[data-agent-id="' + agentId + '"]');
  if (el) _agentIdMap.set(agentId, el);
  return el;
}

function pruneAgentIdMap(maxEntries) {
  let scanned = 0;
  const limit = maxEntries || 2500;
  for (const [id, el] of _agentIdMap.entries()) {
    scanned++;
    if (!el || !el.isConnected) {
      _agentIdMap.delete(id);
    }
    if (scanned >= limit) break;
  }
}

function isReadableCandidate(el) {
  if (!isVisible(el)) return false;
  const text = textOf(el);
  if (!text) return false;

  const tag = el.tagName.toLowerCase();
  const headingLike = ["h1", "h2", "h3", "h4", "h5", "h6", "label", "th", "td"].includes(tag);
  if (headingLike) {
    return text.length >= 2;
  }

  if (text.length < 24) {
    return false;
  }

  // Avoid giant container-level blocks that duplicate the entire page.
  const childCount = el.children ? el.children.length : 0;
  if (childCount > 10 && text.length > 120) {
    return false;
  }

  return true;
}

// ═══════════════════════════════════════════════════════════════
//  Element Serialization (with agent-id)
// ═══════════════════════════════════════════════════════════════

function serializeElement(el, index) {
  const agentId = assignAgentId(el);
  const rect = el.getBoundingClientRect();
  const text = textOf(el);
  const ariaLabel = (el.getAttribute("aria-label") || "").trim();
  const name = (el.getAttribute("name") || "").trim();
  const placeholder = (el.getAttribute("placeholder") || "").trim();
  const href = (el.getAttribute("href") || "").trim();
  const alt = (el.getAttribute("alt") || "").trim();
  const title = (el.getAttribute("title") || "").trim();
  const role = roleOf(el);
  const path = domPath(el);
  const labels = ancestorLabels(el);
  const viewport = isInViewport(el);
  const refId = "mw_" + agentId;

  return {
    ref_id: refId,
    agent_id: agentId,
    role,
    tag: el.tagName.toLowerCase(),
    text: text || alt || title,
    aria_label: ariaLabel,
    name,
    placeholder,
    href,
    value: typeof el.value === "string" ? el.value.slice(0, 120) : "",
    context_text: labels.join(" | "),
    frame_path: "main",
    dom_path: path,
    bounds: {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
    visible: true,
    enabled: !el.disabled,
    checked: !!el.checked,
    selected: !!el.selected,
    in_viewport: viewport,
    action_types: actionTypesFor(el),
    fingerprint: {
      role,
      text: text || alt || title,
      aria_label: ariaLabel,
      name,
      placeholder,
      href,
      ancestor_labels: labels,
      frame_path: "main",
      dom_path: path,
      sibling_index: index,
      stable_attributes: {
        id: el.id || "",
        class: (el.className || "").toString().slice(0, 120),
        type: el.getAttribute("type") || "",
      },
    },
  };
}

// ═══════════════════════════════════════════════════════════════
//  2. Element Collection (distilled + deduplicated)
// ═══════════════════════════════════════════════════════════════

function collectElements() {
  pruneAgentIdMap(3000);
  const seen = new Set();
  const results = [];
  const interactiveNodes = document.querySelectorAll(INTERACTIVE_SELECTOR);
  const readableNodes = document.querySelectorAll(READABLE_SELECTOR);

  for (const el of interactiveNodes) {
    if (results.length >= 320) break;
    if (seen.has(el)) continue;
    if (!isVisible(el)) continue;
    seen.add(el);
    results.push(serializeElement(el, results.length));
  }

  for (const el of readableNodes) {
    if (results.length >= 520) break;
    if (seen.has(el)) continue;
    if (!isReadableCandidate(el)) continue;
    seen.add(el);
    results.push(serializeElement(el, results.length));
  }

  return results;
}

function buildSnapshot(sessionId, tabId) {
  return {
    session_id: sessionId,
    tab_id: tabId,
    url: window.location.href,
    title: document.title,
    generation: Date.now(),
    frame_id: "main",
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scrollX: Math.round(window.scrollX),
      scrollY: Math.round(window.scrollY),
      scrollHeight: document.documentElement.scrollHeight,
      pageHeight: document.documentElement.scrollHeight,
    },
    elements: collectElements(),
    opaque_regions: [],
  };
}

// ═══════════════════════════════════════════════════════════════
//  Action Execution (agent-id powered)
// ═══════════════════════════════════════════════════════════════

function normalizeText(value) {
  return String(value || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

/**
 * Resolve the target element for an action.
 * Priority: agent_id (O(1)) -> ref_id parse -> heuristic fallback.
 */
function findTargetForAction(action) {
  // -- Fast path: agent_id lookup (O(1)) --
  var agentId = action?.agent_id || action?.metadata?.agent_id;
  if (agentId) {
    var el = lookupByAgentId(Number(agentId));
    if (el && isVisible(el)) {
      return {
        element: el,
        payload: serializeElement(el, 0),
        score: 10000,
        matchedBy: "agent_id",
      };
    }
  }

  // -- Medium path: ref_id "mw_N" parse (O(1) via agent_id map) --
  var refId = action?.ref_id || "";
  if (refId.startsWith("mw_")) {
    var parsed = parseInt(refId.slice(3), 10);
    if (!isNaN(parsed)) {
      var el2 = lookupByAgentId(parsed);
      if (el2 && isVisible(el2)) {
        return {
          element: el2,
          payload: serializeElement(el2, 0),
          score: 10000,
          matchedBy: "ref_id",
        };
      }
    }
  }

  // -- Slow path: heuristic matching (fallback) --
  return findTargetByHeuristic(action);
}

function findTargetByHeuristic(action) {
  const candidates = collectActionCandidates();
  let best = null;

  for (const candidate of candidates) {
    const score = scoreCandidate(action, candidate.payload);
    if (score < 0) continue;
    if (!best || score > best.score) {
      best = { ...candidate, score, matchedBy: "heuristic" };
    }
  }

  if (!best || best.score <= 0) return null;
  return best;
}

function collectActionCandidates() {
  const seen = new Set();
  const results = [];
  const nodes = document.querySelectorAll(INTERACTIVE_SELECTOR);

  for (const el of nodes) {
    if (results.length >= 400) break;
    if (seen.has(el)) continue;
    if (!isVisible(el)) continue;
    seen.add(el);
    results.push({ element: el, payload: serializeElement(el, results.length) });
  }

  return results;
}

function scoreCandidate(action, payload) {
  const metadata = action?.metadata || {};
  const requestedAction = String(action?.action || "");
  if (
    requestedAction &&
    Array.isArray(payload.action_types) &&
    payload.action_types.length &&
    !payload.action_types.includes(requestedAction)
  ) {
    return -1;
  }

  let score = 0;
  if (payload.ref_id === action?.ref_id) score += 1000;
  if (metadata.dom_path && payload.dom_path === metadata.dom_path) score += 300;
  if (metadata.tag && payload.tag === metadata.tag) score += 40;
  if (metadata.role && payload.role === metadata.role) score += 40;

  const expectedLabels = [
    metadata.label,
    metadata.text,
    metadata.aria_label,
    metadata.name,
    metadata.placeholder,
    metadata.href,
  ]
    .map(normalizeText)
    .filter(Boolean);

  const actualLabels = [
    payload.text,
    payload.aria_label,
    payload.name,
    payload.placeholder,
    payload.href,
    payload.context_text,
  ]
    .map(normalizeText)
    .filter(Boolean);

  for (const expected of expectedLabels) {
    for (const actual of actualLabels) {
      if (actual === expected) score += 120;
      else if (actual.includes(expected) || expected.includes(actual)) score += 60;
    }
  }

  if (payload.in_viewport) score += 15;

  return score;
}

function dispatchInputEvents(el) {
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function setElementValue(el, value) {
  if (el instanceof HTMLInputElement) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    return;
  }
  if (el instanceof HTMLTextAreaElement) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    return;
  }
  if (el.isContentEditable) {
    el.textContent = value;
  }
}

function executeClick(el) {
  el.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  el.focus?.({ preventScroll: true });
  if (typeof el.click === "function") {
    el.click();
    return;
  }
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
}

function executeType(el, action) {
  const text = String(action?.text || "");
  el.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  el.focus?.({ preventScroll: true });

  // Contenteditable elements (Google Docs, Notion, rich text editors):
  // Use document.execCommand which goes through the browser editing pipeline.
  // Direct textContent/value assignment is silently ignored by canvas-based editors.
  if (el.isContentEditable) {
    if (action?.clear_first) {
      document.execCommand("selectAll", false, null);
      document.execCommand("delete", false, null);
    }
    if (!document.execCommand("insertText", false, text)) {
      el.textContent = text;
    }
    dispatchInputEvents(el);
    return;
  }

  if (action?.clear_first) {
    setElementValue(el, "");
    dispatchInputEvents(el);
  }
  setElementValue(el, text);
  dispatchInputEvents(el);
}

function executeSelect(el, action) {
  if (!(el instanceof HTMLSelectElement)) {
    throw new Error("Target element is not a select control.");
  }
  const optionText = normalizeText(action?.option);
  const option = [...el.options].find(function(candidate) {
    return normalizeText(candidate.textContent) === optionText || normalizeText(candidate.value) === optionText;
  });
  if (!option) {
    throw new Error("Option not found: " + (action?.option || ""));
  }
  el.value = option.value;
  dispatchInputEvents(el);
}

async function executeAction(action, sessionId, tabId) {
  const match = findTargetForAction(action);
  if (!match) {
    return {
      ok: false,
      message: "Could not resolve target for " + (action?.ref_id || "unknown ref") + ".",
      executedRefId: "",
      matchedBy: "none",
    };
  }

  const { element, payload, matchedBy } = match;
  if (!payload.enabled) {
    return {
      ok: false,
      message: "Target " + payload.ref_id + " is disabled.",
      executedRefId: payload.ref_id,
      matchedBy: matchedBy || "unknown",
    };
  }

  try {
    if (action?.action === "click") {
      executeClick(element);
    } else if (action?.action === "type") {
      executeType(element, action);
    } else if (action?.action === "select") {
      executeSelect(element, action);
    } else {
      throw new Error("Unsupported action: " + (action?.action || "unknown"));
    }

    return {
      ok: true,
      message: action.action + " executed on " + payload.ref_id + " (via " + matchedBy + ")",
      executedRefId: payload.ref_id,
      matchedBy: matchedBy || "unknown",
    };
  } catch (error) {
    return {
      ok: false,
      message: String(error?.message || error),
      executedRefId: payload.ref_id,
      matchedBy: matchedBy || "unknown",
    };
  }
}

// -- Snapshot Sending (debounced) --

let _snapshotDebounceTimer = null;

async function sendSnapshot(sessionId, tabId) {
  const snapshot = buildSnapshot(sessionId, tabId);
  try {
    await chrome.runtime.sendMessage({
      type: "moonwalk_snapshot",
      snapshot,
    });
  } catch (error) {
    console.debug("[Moonwalk Content] Snapshot send failed", error);
  }
}

function debouncedSnapshot(sessionId, tabId, delayMs) {
  delayMs = delayMs || 300;
  if (_snapshotDebounceTimer) clearTimeout(_snapshotDebounceTimer);
  _snapshotDebounceTimer = setTimeout(function() {
    _snapshotDebounceTimer = null;
    sendSnapshot(sessionId, tabId);
  }, delayMs);
}

// ═══════════════════════════════════════════════════════════════
//  3. MutationObserver — Verify Phase + Auto-snapshot
// ═══════════════════════════════════════════════════════════════

let _observerActive = false;
let _observerSessionId = "";
let _observerTabId = "";
let _mutationBatch = 0;
const MUTATION_BATCH_THRESHOLD = 5;
const MUTATION_DEBOUNCE_MS = 400;

// Pending action verification (set before action execution)
let _pendingVerify = null;

function registerPendingVerify(actionId, refId, actionType) {
  _pendingVerify = {
    actionId: actionId,
    refId: refId,
    actionType: actionType,
    timestamp: Date.now(),
  };
}

function startMutationObserver(sessionId, tabId) {
  if (_observerActive) return;
  _observerActive = true;
  _observerSessionId = sessionId;
  _observerTabId = tabId;

  const observer = new MutationObserver(function(mutations) {
    let dominated = false;
    const changeTypes = new Set();

    for (const m of mutations) {
      if (m.type === "childList" && (m.addedNodes.length > 0 || m.removedNodes.length > 0)) {
        dominated = true;
        if (m.addedNodes.length > 0) changeTypes.add("nodes_added");
        if (m.removedNodes.length > 0) changeTypes.add("nodes_removed");
      }
      if (m.type === "attributes") {
        const attr = m.attributeName || "";
        // Ignore our own agent-id attribute changes
        if (attr === "data-agent-id") continue;
        if (["disabled", "aria-hidden", "hidden", "style", "class", "aria-expanded", "aria-selected", "checked"].includes(attr)) {
          dominated = true;
          changeTypes.add("attr_" + attr);
        }
      }
    }

    if (!dominated) return;

    // -- Verify phase: push dom_change_event if pending --
    if (_pendingVerify && (Date.now() - _pendingVerify.timestamp) < 10000) {
      try {
        chrome.runtime.sendMessage({
          type: "moonwalk_dom_change",
          event: {
            action_id: _pendingVerify.actionId,
            ref_id: _pendingVerify.refId,
            action_type: _pendingVerify.actionType,
            change_types: Array.from(changeTypes),
            timestamp: Date.now(),
            session_id: _observerSessionId,
            tab_id: _observerTabId,
          },
        });
      } catch (e) {
        console.debug("[Moonwalk Content] DOM change event send failed", e);
      }
      _pendingVerify = null; // one-shot per action
    }

    // -- Auto-snapshot on meaningful DOM changes --
    _mutationBatch++;
    if (_mutationBatch >= MUTATION_BATCH_THRESHOLD) {
      _mutationBatch = 0;
      debouncedSnapshot(_observerSessionId, _observerTabId, MUTATION_DEBOUNCE_MS);
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["disabled", "aria-hidden", "hidden", "style", "class", "aria-expanded", "aria-selected", "checked", "data-agent-id"],
  });
}

// -- Message Handling --

// ── Moonwalk Research Highlight Styles ──
const HIGHLIGHT_STYLE_ID = "moonwalk-research-highlight-style";
const RESEARCH_OVERLAY_ID = "moonwalk-research-overlay";
let _researchOverlayTimer = null;

function ensureHighlightStyles() {
  if (document.getElementById(HIGHLIGHT_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = HIGHLIGHT_STYLE_ID;
  style.textContent = `
    @keyframes moonwalk-pulse {
      0%   { box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.6); }
      50%  { box-shadow: 0 0 8px 4px rgba(99, 102, 241, 0.3); }
      100% { box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.6); }
    }
    .moonwalk-highlight-reading {
      outline: 2px solid rgba(99, 102, 241, 0.7) !important;
      background-color: rgba(99, 102, 241, 0.08) !important;
      animation: moonwalk-pulse 1.5s ease-in-out infinite;
      transition: outline 0.3s, background-color 0.3s;
    }
    .moonwalk-highlight-reading-text {
      background-color: rgba(245, 158, 11, 0.18) !important;
      outline: 1px solid rgba(245, 158, 11, 0.55) !important;
    }
    #${RESEARCH_OVERLAY_ID} {
      position: fixed;
      top: 18px;
      right: 18px;
      width: 340px;
      max-width: calc(100vw - 32px);
      max-height: min(52vh, 420px);
      display: none;
      z-index: 2147483647;
      overflow: hidden;
      border: 1px solid rgba(15, 23, 42, 0.24);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(248, 250, 252, 0.97));
      color: #0f172a;
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
      backdrop-filter: blur(10px);
      border-radius: 14px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      pointer-events: none;
    }
    #${RESEARCH_OVERLAY_ID}.visible {
      display: block;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-shell {
      padding: 14px 14px 12px;
      display: grid;
      gap: 10px;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-head {
      display: grid;
      gap: 2px;
      padding-bottom: 8px;
      border-bottom: 1px solid rgba(15, 23, 42, 0.1);
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-kicker {
      font-size: 10px;
      letter-spacing: 0.16em;
      color: #475569;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-title {
      font-size: 15px;
      line-height: 1.3;
      font-weight: 600;
      color: #0f172a;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 10px;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-field {
      min-width: 0;
      display: grid;
      gap: 3px;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-field-full {
      grid-column: 1 / -1;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-label {
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #64748b;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-value {
      font-size: 12px;
      line-height: 1.45;
      color: #0f172a;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-snippet {
      margin: 0;
      font-size: 12px;
      line-height: 1.5;
      color: #1e293b;
      white-space: pre-wrap;
      overflow: hidden;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 8;
    }
    #${RESEARCH_OVERLAY_ID} .moonwalk-overlay-source {
      word-break: break-word;
      white-space: normal;
    }
  `;
  (document.head || document.documentElement).appendChild(style);
}

function ensureResearchOverlay() {
  ensureHighlightStyles();
  let overlay = document.getElementById(RESEARCH_OVERLAY_ID);
  if (overlay) return overlay;

  overlay = document.createElement("div");
  overlay.id = RESEARCH_OVERLAY_ID;
  overlay.setAttribute("role", "status");
  overlay.setAttribute("aria-live", "polite");
  overlay.innerHTML = `
    <div class="moonwalk-overlay-shell">
      <div class="moonwalk-overlay-head">
        <div class="moonwalk-overlay-kicker">MOONWALK</div>
        <div class="moonwalk-overlay-title">Active reading</div>
      </div>
      <div class="moonwalk-overlay-grid">
        <div class="moonwalk-overlay-field">
          <div class="moonwalk-overlay-label">Tool</div>
          <div class="moonwalk-overlay-value" data-moonwalk-field="tool"></div>
        </div>
        <div class="moonwalk-overlay-field">
          <div class="moonwalk-overlay-label">Items</div>
          <div class="moonwalk-overlay-value" data-moonwalk-field="itemCount"></div>
        </div>
        <div class="moonwalk-overlay-field moonwalk-overlay-field-full">
          <div class="moonwalk-overlay-label">Title</div>
          <div class="moonwalk-overlay-value" data-moonwalk-field="title"></div>
        </div>
        <div class="moonwalk-overlay-field moonwalk-overlay-field-full">
          <div class="moonwalk-overlay-label">Source</div>
          <div class="moonwalk-overlay-value moonwalk-overlay-source" data-moonwalk-field="sourceUrl"></div>
        </div>
        <div class="moonwalk-overlay-field moonwalk-overlay-field-full">
          <div class="moonwalk-overlay-label">Snippet</div>
          <pre class="moonwalk-overlay-snippet" data-moonwalk-field="snippet"></pre>
        </div>
      </div>
    </div>
  `;
  (document.body || document.documentElement).appendChild(overlay);
  return overlay;
}

function overlayFieldValue(overlay, field) {
  return overlay.querySelector('[data-moonwalk-field="' + field + '"]');
}

function setOverlayField(overlay, field, value, fallback) {
  const el = overlayFieldValue(overlay, field);
  if (!el) return;
  const text = String(value || fallback || "").trim();
  el.textContent = text || fallback || "";
}

function hideResearchOverlay() {
  const overlay = document.getElementById(RESEARCH_OVERLAY_ID);
  if (!overlay) return;
  overlay.classList.remove("visible");
}

function showResearchOverlay(details, durationMs) {
  const overlay = ensureResearchOverlay();
  const itemCount = Number(details?.itemCount || 0);
  const tool = String(details?.tool || "").trim() || "browser_read";
  const title = String(details?.title || "").trim() || document.title || "(untitled)";
  const sourceUrl = String(details?.sourceUrl || "").trim() || window.location.href;
  const snippet = String(details?.snippet || "").trim() || "No extracted snippet available.";

  setOverlayField(overlay, "tool", tool, "browser_read");
  setOverlayField(overlay, "itemCount", itemCount > 0 ? String(itemCount) : "visible", "visible");
  setOverlayField(overlay, "title", title, "(untitled)");
  setOverlayField(overlay, "sourceUrl", sourceUrl, window.location.href);
  setOverlayField(overlay, "snippet", snippet, "No extracted snippet available.");

  overlay.classList.add("visible");

  if (_researchOverlayTimer) {
    clearTimeout(_researchOverlayTimer);
    _researchOverlayTimer = null;
  }
  if (durationMs > 0) {
    _researchOverlayTimer = setTimeout(function() {
      hideResearchOverlay();
      _researchOverlayTimer = null;
    }, durationMs);
  }
}

function highlightElements(agentIds, durationMs, mode, overlayDetails) {
  ensureHighlightStyles();
  durationMs = durationMs || 3000;
  mode = mode || "reading"; // "reading" | "text"
  const cls = mode === "text" ? "moonwalk-highlight-reading-text" : "moonwalk-highlight-reading";
  const highlighted = [];
  showResearchOverlay(overlayDetails || {}, durationMs);

  for (const aid of agentIds) {
    const el = lookupByAgentId(Number(aid));
    if (!el) continue;
    el.classList.add(cls);
    highlighted.push(el);
    // Scroll first highlighted element into view
    if (highlighted.length === 1) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // Auto-remove after duration
  if (durationMs > 0) {
    setTimeout(function() {
      for (const el of highlighted) {
        el.classList.remove(cls);
      }
    }, durationMs);
  }

  return highlighted.length;
}

function highlightReadableContent(durationMs, overlayDetails) {
  ensureHighlightStyles();
  durationMs = durationMs || 4000;
  const readableNodes = document.querySelectorAll(READABLE_SELECTOR);
  const highlighted = [];
  showResearchOverlay(overlayDetails || {}, durationMs);

  for (const el of readableNodes) {
    if (!isVisible(el) || !isInViewport(el)) continue;
    if (!isReadableCandidate(el)) continue;
    el.classList.add("moonwalk-highlight-reading-text");
    highlighted.push(el);
    if (highlighted.length >= 30) break; // cap for performance
  }

  if (durationMs > 0) {
    setTimeout(function() {
      for (const el of highlighted) {
        el.classList.remove("moonwalk-highlight-reading-text");
      }
    }, durationMs);
  }

  return highlighted.length;
}

chrome.runtime.onMessage.addListener(function(message, sender, sendResponse) {
  if (message?.type === "moonwalk_collect_snapshot") {
    const sid = message.sessionId || _observerSessionId;
    const tid = message.tabId || _observerTabId;
    _observerSessionId = sid;
    _observerTabId = tid;
    sendSnapshot(sid, tid);
    startMutationObserver(sid, tid);
    sendResponse?.({ ok: true });
    return true;
  }
  if (message?.type === "moonwalk_scroll") {
    var direction = message.direction || "down";
    var amount = message.amount || "page";
    var pixels = 0;
    var viewH = window.innerHeight;

    if (amount === "page") pixels = Math.round(viewH * 0.85);
    else if (amount === "half") pixels = Math.round(viewH * 0.5);
    else pixels = parseInt(amount, 10) || Math.round(viewH * 0.85);

    if (direction === "up") pixels = -pixels;
    else if (direction === "top") { window.scrollTo({ top: 0, behavior: "auto" }); pixels = 0; }
    else if (direction === "bottom") { window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "auto" }); pixels = 0; }

    if (pixels !== 0) window.scrollBy({ top: pixels, behavior: "auto" });

    // Brief pause for scroll to settle, then send fresh snapshot + result
    setTimeout(function() {
      var sid = message.sessionId || _observerSessionId;
      var tid = message.tabId || _observerTabId;
      sendSnapshot(sid, tid);
      sendResponse?.({
        ok: true,
        scrollY: Math.round(window.scrollY),
        pageHeight: document.documentElement.scrollHeight,
        viewportHeight: window.innerHeight,
        atBottom: (window.scrollY + window.innerHeight) >= (document.documentElement.scrollHeight - 5),
        atTop: window.scrollY <= 0,
      });
    }, 120);
    return true;
  }
  if (message?.type === "moonwalk_evaluate_js") {
    try {
      const result = window.eval(message.script);
      sendResponse?.({ ok: true, result: String(result) });
    } catch (error) {
      sendResponse?.({ ok: false, error: String(error?.message || error) });
    }
    return true;
  }
  
  if (message?.type === "moonwalk_extract_data") {
    try {
      const target = message.target;
      let result = "";
      if (target === "gdocs") {
         const kix = document.querySelector('.kix-appview-editor');
         if (kix && kix.innerText) result = kix.innerText.substring(0, 8000);
         else if (document.body && document.body.innerText) result = document.body.innerText.substring(0, 8000);
         else if (document.documentElement && document.documentElement.innerText) result = document.documentElement.innerText.substring(0, 8000);
      } else if (target === "gcal") {
         var chips = document.querySelectorAll('[data-eventid],[data-eventchip-action],.KF4T6b,.lKHqkb');
         var evs = [];
         var max_results = 250;
         for(var i=0; i < Math.min(chips.length, max_results); i++){
           var c = chips[i];
           evs.push({title: c.getAttribute('data-tooltip') || c.getAttribute('aria-label') || c.innerText.slice(0,80)});
         }
         result = JSON.stringify(evs);
      } else if (target === "body") {
         var t = document.body ? document.body.innerText : "";
         result = t ? t.substring(0, 8000) : "";
      }
      sendResponse?.({ ok: true, result: String(result) });
    } catch (error) {
      sendResponse?.({ ok: false, error: String(error?.message || error) });
    }
    return true;
  }
  if (message?.type === "moonwalk_execute_action") {
    const action = message.action;
    // Register pending verify BEFORE executing so MutationObserver
    // catches changes triggered by this action
    if (action?.action_id) {
      registerPendingVerify(action.action_id, action.ref_id, action.action);
    }
    executeAction(action, message.sessionId, message.tabId)
      .then(function(result) { sendResponse?.(result); })
      .catch(function(error) {
        sendResponse?.({
          ok: false,
          message: String(error?.message || error),
          executedRefId: "",
          matchedBy: "none",
        });
      });
    return true;
  }
  // ── Highlight elements the agent is reading/researching ──
  if (message?.type === "moonwalk_highlight") {
    const agentIds = message.agentIds || [];
    const duration = message.duration || 3000;
    const mode = message.mode || "reading";
    const overlayDetails = {
      tool: message.tool || "",
      title: message.title || "",
      sourceUrl: message.sourceUrl || "",
      snippet: message.snippet || "",
      itemCount: Number(message.itemCount || 0),
    };
    let count = 0;
    if (agentIds.length > 0) {
      count = highlightElements(agentIds, duration, mode, overlayDetails);
    } else {
      // No specific IDs → highlight all visible readable content
      count = highlightReadableContent(duration, overlayDetails);
    }
    sendResponse?.({ ok: true, highlighted: count, overlayVisible: true });
    return true;
  }
  return false;
});
})();
