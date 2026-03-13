# Moonwalk Agent Architecture V2 — Improvement Plan

> Legacy design note: this document describes an earlier step-plan-heavy V2
> design and is no longer the authoritative runtime reference. See
> [runtime-spine.md](./runtime-spine.md) for the current active architecture.

## Executive Summary

The current Moonwalk agent architecture has a **31.8% benchmark pass rate** with major issues in:
1. **Tool selection accuracy** — Agent calls wrong tools or over-calls the same tool
2. **Reasoning depth** — Simple reactive loop lacks true planning
3. **Context blindness** — No structured world model, just raw context injection
4. **Recovery failures** — Poor error handling and no self-correction

This document proposes a **ReAct + Planning + Memory** hybrid architecture to achieve >85% benchmark accuracy.

---

## Current Architecture Analysis

### Problems Identified

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CURRENT FLOW (FLAWED)                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   User Request                                                              │
│        │                                                                    │
│        ▼                                                                    │
│   ┌─────────────┐                                                           │
│   │   Router    │  ← Classifies FAST vs POWERFUL (often wrong)              │
│   │  (Flash)    │                                                           │
│   └──────┬──────┘                                                           │
│          │                                                                  │
│          ▼                                                                  │
│   ┌─────────────┐                                                           │
│   │   Execute   │  ← Raw loop: LLM → tool → LLM → tool...                   │
│   │    Loop     │    NO planning phase, NO verification                    │
│   └──────┬──────┘                                                           │
│          │                                                                  │
│          ▼                                                                  │
│   ┌─────────────┐                                                           │
│   │  Response   │  ← Often premature, no quality check                      │
│   └─────────────┘                                                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Root Causes of Failures

| Issue | Root Cause | Impact |
|-------|-----------|--------|
| **Over-calling tools** | No planning — agent fires tools reactively | "Opening Spotify" called 4 times |
| **Wrong tool selection** | 30+ tools overwhelm the model | Picks `run_shell` when `open_app` needed |
| **No verification** | Execute-and-done mentality | Says "done" before checking success |
| **Context flooding** | Raw context string dumped into prompt | Model loses important details |
| **Poor error recovery** | Simple retry with same approach | Loops until MAX_ITERATIONS |
| **No task decomposition** | Complex tasks treated as atomic | Multi-step tasks fail halfway |

---

## Proposed V2 Architecture

### Core Paradigm: **Sense-Plan-Act-Verify (SPAV)**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PROPOSED V2 FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   User Request                                                              │
│        │                                                                    │
│        ▼                                                                    │
│   ╔═══════════════════════════════════════════════════════════════════╗     │
│   ║                    1. SENSE (Context Engine)                      ║     │
│   ║  ┌─────────────────────────────────────────────────────────────┐  ║     │
│   ║  │  • Structured World State (not raw strings)                 │  ║     │
│   ║  │  • Intent Classification (what does user REALLY want?)     │  ║     │
│   ║  │  • Entity Extraction (apps, files, URLs mentioned)         │  ║     │
│   ║  │  • Ambiguity Detection (need clarification?)               │  ║     │
│   ║  └─────────────────────────────────────────────────────────────┘  ║     │
│   ╚══════════════════════════════╤════════════════════════════════════╝     │
│                                  │                                          │
│                                  ▼                                          │
│   ╔═══════════════════════════════════════════════════════════════════╗     │
│   ║                    2. PLAN (Task Decomposition)                   ║     │
│   ║  ┌─────────────────────────────────────────────────────────────┐  ║     │
│   ║  │  • Break task into explicit steps                          │  ║     │
│   ║  │  • Select minimal tool set for each step                   │  ║     │
│   ║  │  • Define success criteria per step                        │  ║     │
│   ║  │  • Estimate if clarification needed BEFORE acting          │  ║     │
│   ║  └─────────────────────────────────────────────────────────────┘  ║     │
│   ╚══════════════════════════════╤════════════════════════════════════╝     │
│                                  │                                          │
│         ┌────────────────────────┼────────────────────────┐                 │
│         │ Clarification needed?  │                        │                 │
│         ▼                        ▼                        │                 │
│   ┌───────────┐           ╔══════════════════════════╗    │                 │
│   │await_reply│           ║   3. ACT (Execute Plan)  ║    │                 │
│   │(ask user) │           ║  ┌────────────────────┐  ║    │                 │
│   └───────────┘           ║  │ Execute ONE step   │  ║    │                 │
│                           ║  │ at a time          │──╬────┘                 │
│                           ║  └─────────┬──────────┘  ║                      │
│                           ╚════════════╪═════════════╝                      │
│                                        │                                    │
│                                        ▼                                    │
│   ╔═══════════════════════════════════════════════════════════════════╗     │
│   ║                    4. VERIFY (Quality Gate)                       ║     │
│   ║  ┌─────────────────────────────────────────────────────────────┐  ║     │
│   ║  │  • Did the tool succeed? (check result)                    │  ║     │
│   ║  │  • Is the world state as expected? (re-sense)              │  ║     │
│   ║  │  • Should I continue, retry, or abort?                     │  ║     │
│   ║  └─────────────────────────────────────────────────────────────┘  ║     │
│   ╚══════════════════════════════╤════════════════════════════════════╝     │
│                                  │                                          │
│              ┌───────────────────┼───────────────────┐                      │
│              │                   │                   │                      │
│         [Failed]            [Continue]          [Complete]                  │
│              │                   │                   │                      │
│              ▼                   │                   ▼                      │
│   ┌──────────────────┐           │         ┌──────────────────┐             │
│   │  Error Recovery  │           │         │  send_response   │             │
│   │  (new strategy)  │───────────┘         │  (verified done) │             │
│   └──────────────────┘                     └──────────────────┘             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Deep Dives

### 1. Context Engine (Sense Phase)

**Current Problem:** Raw context strings cause information loss and model confusion.

**Solution:** Structured World State object with typed fields.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONTEXT ENGINE V2                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Raw Inputs                     Structured World State                     │
│   ──────────                     ─────────────────────                      │
│                                                                             │
│   ┌─────────────┐               ┌─────────────────────────────────────────┐ │
│   │ AppleScript │──────────────▶│  WorldState {                           │ │
│   │  (L1)       │               │    active_app: "Safari"                 │ │
│   └─────────────┘               │    window_title: "GitHub - Moonwalk"    │ │
│                                 │    browser_url: "https://github.com/..."│ │
│   ┌─────────────┐               │    detected_entities: ["GitHub",        │ │
│   │ Browser DOM │──────────────▶│                       "repository"]     │ │
│   │  (L2)       │               │    user_intent: {                       │ │
│   └─────────────┘               │      action: "navigation"               │ │
│                                 │      target: "URL"                      │ │
│   ┌─────────────┐               │      confidence: 0.92                   │ │
│   │  Vision     │──────────────▶│    }                                    │ │
│   │  (L3)       │               │    ambiguous: false                     │ │
│   └─────────────┘               │    clarification_needed: null           │ │
│                                 │  }                                      │ │
│   ┌─────────────┐               │                                         │ │
│   │ User Prompt │──────────────▶│                                         │ │
│   │ + History   │               └─────────────────────────────────────────┘ │
│   └─────────────┘                                                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**New Classes:**

```python
@dataclass
class UserIntent:
    action: str          # "open", "search", "create", "delete", "analyze"
    target_type: str     # "app", "file", "url", "content"
    target_value: str    # "Spotify", "~/Documents/x.py", etc.
    modifiers: list[str] # ["background", "fullscreen", etc.]
    confidence: float    # 0.0 - 1.0
    requires_clarification: bool

@dataclass  
class WorldState:
    # Desktop state
    active_app: str
    window_title: str
    browser_url: Optional[str]
    running_apps: list[str]
    
    # Extracted entities
    mentioned_apps: list[str]
    mentioned_files: list[str]
    mentioned_urls: list[str]
    
    # Intent analysis
    intent: UserIntent
    
    # Memory context
    recent_actions: list[str]  # last 5 tool calls
    conversation_topic: str
```

---

### 2. Task Planner (Plan Phase)

**Current Problem:** Agent fires tools reactively without a plan, leading to redundant calls.

**Solution:** Explicit planning step that outputs a structured execution plan.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TASK PLANNER V2                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Input: WorldState + User Request                                          │
│                                                                             │
│                      ┌─────────────────────────────────┐                    │
│                      │       PLANNING PROMPT           │                    │
│                      │  ───────────────────────────────│                    │
│                      │  "Given the world state and     │                    │
│                      │   user request, output a JSON   │                    │
│                      │   execution plan with:          │                    │
│                      │   - steps[]                     │                    │
│                      │   - required_tools[]            │                    │
│                      │   - success_criteria            │                    │
│                      │   - needs_clarification: bool"  │                    │
│                      └───────────────┬─────────────────┘                    │
│                                      │                                      │
│                                      ▼                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         ExecutionPlan                               │   │
│   │  ───────────────────────────────────────────────────────────────────│   │
│   │  {                                                                  │   │
│   │    "task_summary": "Open Spotify and play music",                   │   │
│   │    "needs_clarification": false,                                    │   │
│   │    "steps": [                                                       │   │
│   │      {                                                              │   │
│   │        "id": 1,                                                     │   │
│   │        "action": "Open Spotify application",                        │   │
│   │        "tool": "open_app",                                          │   │
│   │        "args": {"app_name": "Spotify"},                             │   │
│   │        "success_criteria": "Spotify becomes active app",            │   │
│   │        "fallback": "Check if Spotify is installed"                  │   │
│   │      },                                                             │   │
│   │      {                                                              │   │
│   │        "id": 2,                                                     │   │
│   │        "action": "Press play button",                               │   │
│   │        "tool": "play_media",                                        │   │
│   │        "args": {},                                                  │   │
│   │        "success_criteria": "Music starts playing",                  │   │
│   │        "depends_on": [1]                                            │   │
│   │      }                                                              │   │
│   │    ],                                                               │   │
│   │    "final_response": "Playing music on Spotify!"                    │   │
│   │  }                                                                  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Insight:** The planner should be a **separate, lightweight LLM call** (or even rule-based for simple tasks) that runs BEFORE the execution loop.

---

### 3. Executor (Act Phase)

**Current Problem:** Agent executes tools in a blind loop with no step tracking.

**Solution:** Step-by-step executor that tracks progress against the plan.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STEP EXECUTOR V2                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ExecutionPlan.steps                                                       │
│        │                                                                    │
│        ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                    FOR EACH STEP IN PLAN:                           │   │
│   │  ─────────────────────────────────────────────────────────────────  │   │
│   │                                                                     │   │
│   │   ┌──────────────────┐                                              │   │
│   │   │ 1. Pre-check     │  Is this step still needed?                  │   │
│   │   │    (optional)    │  (e.g., Spotify already open → skip step 1)  │   │
│   │   └────────┬─────────┘                                              │   │
│   │            │                                                        │   │
│   │            ▼                                                        │   │
│   │   ┌──────────────────┐                                              │   │
│   │   │ 2. Execute Tool  │  Call tool with planned args                 │   │
│   │   │                  │                                              │   │
│   │   └────────┬─────────┘                                              │   │
│   │            │                                                        │   │
│   │            ▼                                                        │   │
│   │   ┌──────────────────┐                                              │   │
│   │   │ 3. Verify Result │  Did it match success_criteria?              │   │
│   │   │                  │                                              │   │
│   │   └────────┬─────────┘                                              │   │
│   │            │                                                        │   │
│   │      ┌─────┴─────┐                                                  │   │
│   │      │           │                                                  │   │
│   │   [Success]   [Failure]                                             │   │
│   │      │           │                                                  │   │
│   │      ▼           ▼                                                  │   │
│   │   Next Step   ┌──────────────────┐                                  │   │
│   │               │ Recovery Logic   │                                  │   │
│   │               │ - Retry (1x)     │                                  │   │
│   │               │ - Try fallback   │                                  │   │
│   │               │ - Replan (ask    │                                  │   │
│   │               │   planner again) │                                  │   │
│   │               │ - Abort + notify │                                  │   │
│   │               └──────────────────┘                                  │   │
│   │                                                                     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 4. Verification Gate (Verify Phase)

**Current Problem:** No verification — agent says "done" without checking.

**Solution:** Explicit verification step after critical actions.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       VERIFICATION GATE V2                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   After Tool Execution:                                                     │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                    VERIFICATION STRATEGIES                          │   │
│   │  ─────────────────────────────────────────────────────────────────  │   │
│   │                                                                     │   │
│   │   Tool Type          │ Verification Method                          │   │
│   │   ─────────────────────────────────────────────                     │   │
│   │   open_app           │ Check active_app == target app               │   │
│   │   open_url           │ Check browser_url contains domain            │   │
│   │   click_element      │ Re-sense UI tree, verify state change        │   │
│   │   write_file         │ Read back first line to confirm              │   │
│   │   run_shell          │ Check exit code + output for errors          │   │
│   │   type_text          │ Visual verify (read_screen) if critical      │   │
│   │                                                                     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   Implementation:                                                           │
│                                                                             │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐       │
│   │  Tool Result    │────▶│  Verify Logic   │────▶│  Confidence     │       │
│   │  (raw output)   │     │  (per-tool)     │     │  Score (0-1)    │       │
│   └─────────────────┘     └─────────────────┘     └────────┬────────┘       │
│                                                            │                │
│                           ┌────────────────────────────────┼────────┐       │
│                           │                                │        │       │
│                      [conf > 0.8]                    [0.5-0.8]  [< 0.5]     │
│                           │                                │        │       │
│                           ▼                                ▼        ▼       │
│                      ┌─────────┐                    ┌───────────────────┐   │
│                      │ Proceed │                    │ Trigger Recovery  │   │
│                      └─────────┘                    └───────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tool Selection Intelligence

### Problem: 30+ Tools Overwhelm Models

**Current Approach:** Keyword-based progressive scoping (flaky).

**Proposed Approach:** Tool Embeddings + Semantic Selection

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      SMART TOOL SELECTION V2                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   1. PRE-COMPUTE: Embed all tool descriptions                               │
│   ─────────────────────────────────────────────                             │
│                                                                             │
│   tools_embeddings = {                                                      │
│     "open_app": embed("Open a macOS application by name"),                  │
│     "open_url": embed("Open a URL in the default browser"),                 │
│     "run_shell": embed("Execute a shell command in terminal"),              │
│     ...                                                                     │
│   }                                                                         │
│                                                                             │
│   2. AT RUNTIME: Embed user request + context                               │
│   ───────────────────────────────────────────                               │
│                                                                             │
│   request_embedding = embed("Open Spotify" + context)                       │
│                                                                             │
│   3. SELECT: Top-K most relevant tools                                      │
│   ─────────────────────────────────────                                     │
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │  similarity_scores = {                                            │     │
│   │    "open_app": 0.94,    ← HIGH MATCH                              │     │
│   │    "open_url": 0.41,                                              │     │
│   │    "run_shell": 0.28,                                             │     │
│   │    ...                                                            │     │
│   │  }                                                                │     │
│   │                                                                   │     │
│   │  selected_tools = top_k(similarity_scores, k=8)                   │     │
│   │  → ["open_app", "send_response", "await_reply", ...]              │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│   4. FALLBACK: Always include core tools                                    │
│   ──────────────────────────────────────                                    │
│                                                                             │
│   final_tools = selected_tools ∪ {"send_response", "await_reply"}           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tool Categories for Structured Selection

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TOOL TAXONOMY                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │  COMMUNICATION (always available)                                 │     │
│   │  ─────────────────────────────────                                │     │
│   │  • send_response  - Final answer to user                         │     │
│   │  • await_reply    - Ask user for clarification                   │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │  PERCEPTION (for understanding state)                             │     │
│   │  ─────────────────────────────────────                            │     │
│   │  • read_screen    - Vision/OCR of screen                         │     │
│   │  • get_ui_tree    - Structured UI hierarchy                      │     │
│   │  • read_file      - File content                                 │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │  APP CONTROL (opening/closing apps)                               │     │
│   │  ──────────────────────────────────                               │     │
│   │  • open_app       - Launch app                                   │     │
│   │  • quit_app       - Close app                                    │     │
│   │  • close_window   - Close current window                         │     │
│   │  • open_url       - Open URL in browser                          │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │  UI AUTOMATION (clicking/typing)                                  │     │
│   │  ───────────────────────────────                                  │     │
│   │  • click_element  - Click UI element                             │     │
│   │  • type_text      - Type keyboard input                          │     │
│   │  • press_key      - Press special keys                           │     │
│   │  • run_shortcut   - Keyboard shortcuts                           │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │  FILE/SYSTEM (terminal, files)                                    │     │
│   │  ──────────────────────────────                                   │     │
│   │  • run_shell      - Execute shell command                        │     │
│   │  • read_file      - Read file content                            │     │
│   │  • write_file     - Write/create file                            │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Memory Architecture V2

### Current Problem: Simple turn buffer, no semantic memory.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MEMORY ARCHITECTURE V2                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                     WORKING MEMORY (RAM)                            │   │
│   │  ─────────────────────────────────────────────────────────────────  │   │
│   │  • Current task plan (ExecutionPlan)                               │   │
│   │  • Current step index                                              │   │
│   │  • Recent tool results (last 5)                                    │   │
│   │  • Current world state (WorldState)                                │   │
│   │  • Active conversation turns (last 10)                             │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                     EPISODIC MEMORY (Session)                       │   │
│   │  ─────────────────────────────────────────────────────────────────  │   │
│   │  • All completed tasks this session                                │   │
│   │  • Successful tool sequences (for learning)                        │   │
│   │  • Errors encountered and resolutions                              │   │
│   │  • User corrections ("No, I meant...")                             │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                     SEMANTIC MEMORY (Persisted)                     │   │
│   │  ─────────────────────────────────────────────────────────────────  │   │
│   │  • User preferences (e.g., "always use Chrome not Safari")         │   │
│   │  • Learned shortcuts (e.g., "Spotify → play workflow")             │   │
│   │  • Frequently accessed files/apps                                  │   │
│   │  • Domain knowledge (user is a developer, uses VS Code)            │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Full Request Flow (V2)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      COMPLETE V2 REQUEST FLOW                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   User: "Open YouTube and search for lofi music"                            │
│                                                                             │
│   ════════════════════════════════════════════════════════════════════════  │
│   PHASE 1: SENSE                                                            │
│   ════════════════════════════════════════════════════════════════════════  │
│                                                                             │
│   1.1 Gather Context (parallel)                                             │
│       ├── AppleScript → active_app="Terminal", window="zsh"                 │
│       ├── Clipboard → empty                                                 │
│       └── History → no recent YouTube activity                              │
│                                                                             │
│   1.2 Intent Classification                                                 │
│       └── UserIntent {                                                      │
│             action: "search"                                                │
│             target_type: "url"                                              │
│             target_value: "youtube.com"                                     │
│             modifiers: ["search:lofi music"]                                │
│             confidence: 0.95                                                │
│             requires_clarification: false                                   │
│           }                                                                 │
│                                                                             │
│   1.3 Tool Selection (semantic)                                             │
│       └── selected_tools = [open_url, type_text, press_key,                 │
│                             send_response, await_reply]                     │
│                                                                             │
│   ════════════════════════════════════════════════════════════════════════  │
│   PHASE 2: PLAN                                                             │
│   ════════════════════════════════════════════════════════════════════════  │
│                                                                             │
│   2.1 Generate Execution Plan                                               │
│       └── ExecutionPlan {                                                   │
│             task_summary: "Open YouTube and search for lofi music"          │
│             needs_clarification: false                                      │
│             steps: [                                                        │
│               {id:1, action:"Open YouTube", tool:"open_url",                │
│                args:{url:"https://youtube.com"}, verify:"browser_url"}      │
│               {id:2, action:"Wait for load", tool:"wait",                   │
│                args:{seconds:1.5}}                                          │
│               {id:3, action:"Type search query", tool:"type_text",          │
│                args:{text:"lofi music"}}                                    │
│               {id:4, action:"Submit search", tool:"press_key",              │
│                args:{key:"Return"}}                                         │
│             ]                                                               │
│             final_response: "Searching for lofi music on YouTube!"          │
│           }                                                                 │
│                                                                             │
│   ════════════════════════════════════════════════════════════════════════  │
│   PHASE 3: ACT + VERIFY (per step)                                          │
│   ════════════════════════════════════════════════════════════════════════  │
│                                                                             │
│   Step 1: open_url("https://youtube.com")                                   │
│       └── Execute → "Successfully opened URL"                               │
│       └── Verify → browser_url contains "youtube.com" ✓                     │
│                                                                             │
│   Step 2: wait(1.5)                                                         │
│       └── Execute → waited                                                  │
│       └── Verify → N/A (no verification needed)                             │
│                                                                             │
│   Step 3: type_text("lofi music")                                           │
│       └── Execute → "Typed text"                                            │
│       └── Verify → trust (no easy verification)                             │
│                                                                             │
│   Step 4: press_key("Return")                                               │
│       └── Execute → "Pressed Return"                                        │
│       └── Verify → trust                                                    │
│                                                                             │
│   ════════════════════════════════════════════════════════════════════════  │
│   PHASE 4: RESPOND                                                          │
│   ════════════════════════════════════════════════════════════════════════  │
│                                                                             │
│   All steps complete + verified → send_response:                            │
│       "Searching for lofi music on YouTube!"                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Error Recovery Strategies

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ERROR RECOVERY MATRIX                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Error Type           │ Strategy                │ Example                  │
│   ─────────────────────────────────────────────────────────────────────────│
│   Tool not found       │ Check tool list,        │ "run_bash" → "run_shell" │
│                        │ suggest alternative     │                          │
│   ─────────────────────────────────────────────────────────────────────────│
│   Arg validation fail  │ Re-read tool schema,    │ Missing required arg     │
│                        │ fix and retry           │                          │
│   ─────────────────────────────────────────────────────────────────────────│
│   Tool execution error │ Read error message,     │ App not installed        │
│                        │ try alternative or ask  │ → ask user to install    │
│   ─────────────────────────────────────────────────────────────────────────│
│   Verification failed  │ Re-sense world state,   │ App didn't open          │
│                        │ retry or replan         │ → wait longer, retry     │
│   ─────────────────────────────────────────────────────────────────────────│
│   Too many retries     │ Abort gracefully,       │ 3 failures on same step  │
│                        │ explain to user         │ → "I couldn't open X"    │
│   ─────────────────────────────────────────────────────────────────────────│
│   Ambiguous request    │ await_reply with        │ "Open it" → "Open what?" │
│                        │ clarifying question     │                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Implement `WorldState` dataclass
- [ ] Implement `UserIntent` extraction (lightweight LLM call or rule-based)
- [ ] Refactor `perception.py` to return `WorldState` instead of `ContextSnapshot`
- [ ] Add tool embeddings infrastructure

### Phase 2: Planning (Week 3-4)
- [ ] Implement `ExecutionPlan` dataclass
- [ ] Create planning prompt template
- [ ] Add `TaskPlanner` class that generates plans
- [ ] Wire planner into `MoonwalkAgent.run()`

### Phase 3: Verification (Week 5-6)
- [ ] Implement per-tool verification strategies
- [ ] Add verification step after each tool execution
- [ ] Implement confidence scoring
- [ ] Add recovery logic (retry, fallback, replan)

### Phase 4: Memory (Week 7-8)
- [ ] Implement `WorkingMemory` class
- [ ] Add `EpisodicMemory` for session learning
- [ ] Persist `SemanticMemory` to ~/.moonwalk/
- [ ] Add memory injection into prompts

### Phase 5: Testing & Tuning (Week 9-10)
- [ ] Run benchmarks with new architecture
- [ ] Tune planning prompts
- [ ] Tune verification thresholds
- [ ] Target: >85% benchmark pass rate

---

## Key Metrics to Track

| Metric | Current | Target |
|--------|---------|--------|
| Benchmark Pass Rate | 31.8% | >85% |
| Avg Tool Calls per Task | ~4.5 | <2.5 |
| Redundant Tool Calls | High | Zero |
| Avg Latency | ~37s | <15s |
| Error Recovery Rate | ~20% | >80% |

---

## Appendix: Prompting Improvements

### Current System Prompt Issues:
1. Too long (1500+ tokens)
2. Too many rules (cognitive overload for model)
3. Examples not structured
4. No explicit planning instructions

### Proposed System Prompt Structure:
```
[ROLE] You are Moonwalk, a desktop AI assistant.

[CAPABILITIES] 
- Desktop control (apps, files, browser)
- System commands (terminal, clipboard)
- Vision (screenshots, UI trees)

[WORKFLOW]
1. UNDERSTAND: Parse user intent
2. PLAN: Output execution steps as JSON
3. EXECUTE: Run tools one-by-one
4. VERIFY: Check each step succeeded
5. RESPOND: Confirm completion

[RULES]
- Never guess coordinates (use get_ui_tree first)
- Ask for clarification if ambiguous
- Verify before responding "done"

[FORMAT]
Always output structured plans, not raw tool calls.
```

---

*Document generated: March 2026*
*Author: Moonwalk Development Team*
