"""
Moonwalk — Agent Core
======================
Two-phase agentic execution:
  1. ROUTE — Flash classifies request → Fast or Powerful
  2. EXECUTE — chosen model runs the agent loop with tools
"""

import asyncio
import json
import time as _time
import os
import urllib.parse
from typing import Callable, Optional, Awaitable
from functools import partial

# Force print to flush so Electron sees logs in real-time
print = partial(print, flush=True)

# Load .env file
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Moonwalk modules
import perception
from tools import registry as tool_registry
from memory import ConversationMemory, UserPreferences, TaskStore
from model_router import ModelRouter, Tier
from providers import LLMProvider, LLMResponse


# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════

MAX_AGENT_ITERATIONS = 10

SYSTEM_PROMPT = """You are Moonwalk, a desktop AI assistant running on macOS. You are fast, witty, and action-oriented.

## Your Capabilities
- You can see the user's active application, window title, browser URL, and sometimes their screen.
- You have tools to control the user's Mac: open/close apps, search the web, play media, type text, run keyboard shortcuts, and more.
- You can run terminal commands (run_shell) for file management, system info, git, package installs, running scripts.
- You can read and write files — create scripts, edit configs, write notes.
- You can see the screen (read_screen) and you can read the EXACT macOS UI element tree (get_ui_tree).
- You can fully automate the OS: click elements, press raw keys, move/drag the mouse, and manage windows.
- You can interact with the clipboard (copy/paste between apps).
- You can have multi-turn conversations and remember context within a session.

## Response Tools
You have TWO ways to talk to the user:
- **send_response**: Use for FINAL answers. The conversation ends after this.
- **await_reply**: Use when you want the user to respond (clarifying questions, interactive conversations).
CRITICAL: Do NOT output conversational text directly. ALWAYS use one of these two communication tools.

## SPEED RULES (CRITICAL — follow these to be fast)
1. **Call as many tools as possible per turn.** Don't make one tool call when you can make five. Emit ALL the tools you need in a SINGLE response.
2. **ALWAYS bundle send_response with your final action.** Example: call open_app("CapCut") AND send_response("Opening CapCut!") in the SAME turn. This is 2x faster.
3. **Never waste a turn on wait() alone.** Combine wait() with the next action(s) you'll do after the pause.
4. **Batch sequential actions.** If you know the sequence (click → wait → type → press_key), emit ALL of them at once.
5. **Use get_ui_tree first for coordinates**, not read_screen. get_ui_tree is 5x faster than read_screen.

## Agent Spawning Protocol
When the user FIRST asks to create/spawn a background agent:
1. Use `await_reply` to ask clarifying questions (what to output, URLs, file paths, etc.).
2. Determine if the agent needs `intrusive=True` (needs Mac UI access) or `intrusive=False` (silent cloud execution).

CRITICAL: Once the user replies with enough details (even if vague), you MUST call `spawn_agent` with a detailed task description. Do NOT just describe what you would do — actually CALL the `spawn_agent` tool. If the user has already answered your questions, call `spawn_agent` NOW in this turn.

## Behavior Rules
1. **Be concise.** 1-2 sentences max.
2. **Act first, explain briefly.** 
3. **DO NOT GUESS COORDINATES.** Use `get_ui_tree()` or `read_screen()` first.
4. **Chain tools for complex tasks.**
5. **Never hallucinate tools.** Only use tools that are available.
6. **Web services vs apps.** YouTube, Gmail, etc. → use open_url. Spotify, Slack → use open_app.
7. **Use run_shell for system tasks.** Disk space, file listing, git, pip, brew, etc.
8. **ALWAYS wait after clicking buttons that open dialogs.** After any `click_element` that opens a file picker or dialog — call `wait(seconds=1)` BEFORE keyboard shortcuts.
9. **File pickers:** After Cmd+Shift+G, you land IN the folder. Click the specific file, THEN click Open.

## Few-Shot Examples (Follow these formats strictly)

**Example 1: Simple Action + Response**
User: "Open YouTube"
Action (Concurrent): open_url(url="https://youtube.com") AND send_response(message="Opening YouTube now!")

**Example 2: Screen Reading + Analysis**
User: "What's on my screen?"
Action 1: read_screen() 
(Tool returns OCR/Vision text)
Action 2: send_response(message="I see you have the Safari browser open to a Wikipedia article about cats.")

**Example 3: Interactive Agent Spawning**
User: "Can you create an agent to monitor Bitcoin?"
Action 1: await_reply(message="Sure! What price threshold should I look out for?")
(User replies: "Alert me if it crosses $90k")
Action 2: spawn_agent(task="Monitor Bitcoin price. If it crosses $90k, notify the user.", intrusive=False) AND send_response(message="I've spawned a background agent to monitor Bitcoin for you.")
"""
# WebSocket callback type
WSCallback = Callable[[dict], Awaitable[None]]


# ═══════════════════════════════════════════════════════════════
#  Agent Class
# ═══════════════════════════════════════════════════════════════

class MoonwalkAgent:
    """The core agentic loop with intelligent model routing."""

    def __init__(self):
        self.conversation = ConversationMemory(max_turns=20, idle_timeout=300)
        self.preferences = UserPreferences()
        self.task_store = TaskStore()
        self.router = ModelRouter()
        self._pending_reply_provider: Optional[LLMProvider] = None

    def _build_system_prompt(self) -> str:
        """Build the full system prompt with user preferences."""
        prompt = SYSTEM_PROMPT
        prefs = self.preferences.to_prompt_string()
        if prefs:
            prompt += f"\n\n{prefs}"
        return prompt

    async def run(
        self,
        user_text: str,
        context: perception.ContextSnapshot,
        ws_callback: Optional[WSCallback] = None,
    ) -> tuple:
        """
        Main entry point. Returns (response_text, awaiting_reply).
          1. Route → Flash classifies (Fast or Powerful)
          2. Execute → chosen model runs agent loop with tools
        """
        print(f"\n[Agent] ═══ New Request ═══")
        print(f"[Agent] Text: '{user_text}'")
        print(f"[Agent] Context: app={context.active_app}, title={context.window_title}")

        # Stream "thinking" state to UI
        if ws_callback:
            await ws_callback({"type": "thinking"})

        # ── Phase 1: Route (unless we are answering a pending reply) ──
        if self._pending_reply_provider:
            # We asked a question last turn, so the user is answering it.
            provider = self._pending_reply_provider
            
            # CRITICAL FIX: If the user was using the FAST model, but the conversation history 
            # shows we are in an agent-creation flow, ESCALATE to POWERFUL immediately.
            # FAST is not smart enough to spawn agents with correct configs.
            if self.router.fast and provider.name == self.router.fast.name:
                history_text = " ".join([m.get("parts", [{"text": ""}])[0].get("text", "") for m in self.conversation.get_history()])
                if "agent" in history_text.lower() or "spawn" in history_text.lower():
                    if self.router.powerful:
                        provider = self.router.powerful
                        print(f"[Agent] Escalating pending reply to {provider.name} (Agent creation detected)")
            
            print(f"[Agent] Resuming with {provider.name} (Awaiting reply resolved)")
            self._pending_reply_provider = None  # consume the lock
        else:
            has_screenshot = context.screenshot_path is not None
            context_summary = f"Active app: {context.active_app}, Window: {context.window_title}"
            if context.browser_url:
                context_summary += f", URL: {context.browser_url}"

            try:
                decision = await self.router.route(
                    user_text,
                    context_summary=context_summary,
                    has_screenshot=has_screenshot,
                )
                provider = decision.provider
                print(f"[Agent] Using {provider.name} ({decision.reason})")
            except RuntimeError as e:
                # No models available
                error_msg = str(e)
                if ws_callback:
                    await ws_callback({
                        "type": "response",
                        "payload": {"text": error_msg, "app": ""}
                    })
                return (error_msg, False)

        # ── Phase 2: Execute agent loop with chosen provider ──
        return await self._execute(user_text, context, provider, ws_callback)

    async def _execute(
        self,
        user_text: str,
        context: perception.ContextSnapshot,
        provider: LLMProvider,
        ws_callback: Optional[WSCallback] = None,
    ) -> tuple:
        """
        Run the full agent loop with the given LLM provider.
        Supports multi-step tool calling with re-planning.
        """
        # Build user message with context
        context_str = context.to_prompt_string()
        user_content = f"{user_text}\n\n{context_str}" if context_str else user_text

        # Add pure user text to conversation memory (preventing context prefix pollution)
        self.conversation.add_user(user_text)

        # Prepare messages
        messages = self.conversation.get_history()
        
        # Inject context purely into the LAST message for this single API execution run
        # This keeps the history prefix identical across multi-turn interactions!
        if messages and messages[-1]["role"] == "user" and context_str:
            messages[-1]["parts"][0]["text"] = user_content

        # Load screenshot if available
        image_data = None
        if context.screenshot_path and os.path.exists(context.screenshot_path) and provider.supports_vision:
            try:
                with open(context.screenshot_path, "rb") as f:
                    image_data = f.read()
                print(f"[Agent] Attached screenshot ({len(image_data)} bytes)")
            except Exception as e:
                print(f"[Agent] Failed to load screenshot: {e}")

        # ── Progressive Tool Scoping ──
        # Weaker models get overwhelmed by 30+ tools. Only send what's relevant.
        all_tools = tool_registry.declarations()
        
        # Tier 1: Always available core tools
        core_tool_names = {"send_response", "await_reply", "run_shell", "read_file", "write_file"}
        
        # Determine extra tiers based on keywords in prompt/history/context
        # We look at the whole recent conversation because the user might have said "agent" 2 turns ago
        history_text = " ".join([
            part.get("text", "") 
            for m in messages 
            for part in m.get("parts", []) 
            if "text" in part
        ])
        intent_text = (user_text + " " + history_text + " " + (context.active_app or "")).lower()
        
        needs_mac_ui = any(kw in intent_text for kw in ["open", "close", "quit", "click", "type", "press", "play", "screen", "ui", "window"])
        mac_ui_tools = {"open_app", "close_window", "quit_app", "play_media", "type_text", "run_shortcut", "open_url", "read_screen", "get_ui_tree", "click_element", "hover_element"}
        
        needs_agents = any(kw in intent_text for kw in ["agent", "background", "monitor", "task", "sub"])
        agent_tools = {"spawn_agent", "list_agents", "get_agent_output", "stop_agent"}
        
        active_tool_names = set(core_tool_names)
        if needs_mac_ui:
            active_tool_names.update(mac_ui_tools)
        if needs_agents:
            active_tool_names.update(agent_tools)
            
        # Filter the full tool list
        tools = [t for t in all_tools if t["name"] in active_tool_names]
        print(f"[Agent] Scoped {len(all_tools)} total tools down to {len(tools)} active tools based on intent.")
        final_response = ""
        awaiting_reply = False  # Set True if await_reply tool was used
        full_response = None
        
        # Track tool errors to prevent infinite crash loops
        tool_error_counts = {}

        # ── Agent Loop ──
        t_start = _time.time()
        for iteration in range(MAX_AGENT_ITERATIONS):
            t_iter = _time.time()
            print(f"[Agent] Iteration {iteration + 1}/{MAX_AGENT_ITERATIONS} via {provider.name}")

            # Thinking Injection (Chain-of-Thought)
            # Force weaker models to evaluate progress before firing the next tool
            if iteration > 0:
                messages.append({
                    "role": "user",
                    "parts": [{"text": "[SYSTEM CHECKPOINT] Briefly state what you've accomplished so far, what your next immediate step is, and then execute the necessary tools to achieve it."}]
                })

            # Call the LLM with streaming — use low temperature for deterministic tool calling
            full_response = LLMResponse(provider=provider.name)
            full_response.raw_model_parts = []
            
            got_final = False
            tool_response_parts = []

            try:
                if hasattr(provider, 'generate_stream'):
                    stream_iter = provider.generate_stream(
                        messages=messages,
                        system_prompt=self._build_system_prompt(),
                        tools=tools if provider.supports_tools else [],
                        image_data=image_data if iteration == 0 else None,
                        temperature=0.2,
                    )
                else: 
                     # Fallback for providers that don't support streaming yet (like Ollama)
                    raise NotImplementedError("Streaming required.")
                
                async for chunk in stream_iter:
                    if chunk.error:
                        full_response.error = chunk.error
                        break
                        
                    if chunk.text:
                        full_response.text = (full_response.text or "") + chunk.text
                        if ws_callback:
                            # Stream text directly to client as thoughts arrive
                            await ws_callback({  # type: ignore
                                "type": "thought",
                                "text": chunk.text
                            })
                            
                    if chunk.raw_model_parts:
                        full_response.raw_model_parts.extend(chunk.raw_model_parts)
                        
                    if chunk.has_tool_calls:
                        full_response.tool_calls.extend(chunk.tool_calls)
                        
                        # Process newly arrived tools immediately
                        for tc in chunk.tool_calls:
                            print(f"[Agent] Tool: {tc.name}({tc.args})")
                            if ws_callback:
                                display = self._tool_display_text(tc.name, tc.args)
                                icon_url = self._get_icon_url(tc.name, tc.args, context.active_app)
                                await ws_callback({  # type: ignore
                                    "type": "doing",
                                    "text": display,
                                    "tool": tc.name,
                                    "icon_url": icon_url,
                                    "app": context.active_app.lower() if context.active_app else ""
                                })

                            # Execute tool
                            t_tool = _time.time()
                            try:
                                result = await tool_registry.execute(tc.name, tc.args)
                                print(f"[Agent] ⏱ Tool ({tc.name}) done in {_time.time() - t_tool:.1f}s")
                                
                                # Check terminal actions
                                if tc.name == "send_response" and isinstance(result, str) and result.startswith("RESPONSE:"):
                                    final_response = result[len("RESPONSE:"):]
                                    got_final = True
                                    continue
                                    
                                # Intercept IPC Commands from tools
                                try:
                                    if isinstance(result, str) and '"_IPC_COMMAND_"' in result:
                                        ipc_data = json.loads(result)
                                        if "_IPC_COMMAND_" in ipc_data:
                                            if ws_callback:
                                                await ws_callback({ # type: ignore
                                                    "type": "ipc_trigger",
                                                    "command": ipc_data["_IPC_COMMAND_"]
                                                })
                                            result = f"Successfully triggered IPC command: {ipc_data['_IPC_COMMAND_']}"
                                except Exception:
                                    pass
                                    
                                if tc.name == "await_reply" and isinstance(result, str) and result.startswith("AWAIT:"):
                                    await_msg = result[len("AWAIT:"):]
                                    print(f"[Agent] ⏳ Awaiting user reply: {await_msg}")
                                    self.conversation.add_model(await_msg)
                                    if ws_callback:
                                        await ws_callback({  # type: ignore
                                            "type": "response",
                                            "payload": {
                                                "text": await_msg,
                                                "display": "card",
                                                "await_input": True,
                                                "app": context.active_app.lower() if context.active_app else "",
                                            }
                                        })
                                    awaiting_reply = True
                                    got_final = True
                                    
                                    # Lock the router to this provider for the next turn
                                    self._pending_reply_provider = provider
                                    continue
                                
                                tool_response_parts.append({
                                    "function_response": {
                                        "name": tc.name,
                                        "response": {"result": result}
                                    }
                                })
                            except Exception as e:
                                # Auto-Retry Mechanism with Error Feedback
                                error_msg = str(e)
                                print(f"[Agent] ✕ Tool ({tc.name}) failed: {error_msg}")
                                
                                tool_error_counts[tc.name] = tool_error_counts.get(tc.name, 0) + 1
                                if tool_error_counts[tc.name] > 2:
                                    # Too many retries, give up on this tool
                                    result = f"CRITICAL ERROR: Tool '{tc.name}' failed 3 times. STOP calling this tool. Error: {error_msg}"
                                else:
                                    # Guide the weaker model to fix the issue
                                    result = f"ERROR: '{error_msg}'. Please fix the parameters and retry, or try a different approach."
                                
                                tool_response_parts.append({
                                    "function_response": {
                                        "name": tc.name,
                                        "response": {"error": result}
                                    }
                                })
            except Exception as e:
                import traceback
                traceback.print_exc()
                full_response.error = str(e)

            t_llm = _time.time() - t_iter
            print(f"[Agent] ⏱ Stream completed in {t_llm:.1f}s")

            # Handle errors
            if full_response.error:
                print(f"[Agent] Error: {full_response.error}")
                # Auto-retry on 503 Unavailable or 404 Not Found using the fallback model
                error_upper = full_response.error.upper()
                if "503" in error_upper or "UNAVAILABLE" in error_upper or "404" in error_upper or "NOT_FOUND" in error_upper:
                    if provider.name != getattr(self.router.fallback, "name", "") and self.router.fallback:
                        print(f"[Agent] 🔄 503/404 Detected. Escalating from {provider.name} to Fallback Model: {self.router.fallback.name}")
                        provider = self.router.fallback
                        continue # Retry the exact same messages queue with the new provider
                
                final_response = "Sorry, I had trouble processing that due to high server demand. Please try again."
                break

            # ── Append model's raw thought/tool-call parts to chat history ──
            if full_response.has_tool_calls:
                if full_response.raw_model_parts:
                    messages.append({
                        "role": "model",
                        "parts": full_response.raw_model_parts,
                    })

                # Fetch updated fast context immediately after tool execution
                fast_ctx = None
                try:
                    from perception import get_minimal_context
                    fast_ctx = await get_minimal_context()
                except Exception:
                    pass

                # Inject fast context into the *last* tool's result to avoid breaking Gemini's strict Parts schema
                if tool_response_parts and fast_ctx:
                    last_tr = tool_response_parts[-1]["function_response"]["response"]["result"]
                    if isinstance(last_tr, str):
                        tool_response_parts[-1]["function_response"]["response"]["result"] = last_tr + f"\n\n[Post-Tool Desktop Context]\n{fast_ctx}"  # type: ignore
                    elif isinstance(last_tr, dict):
                        tool_response_parts[-1]["function_response"]["response"]["result"]["_post_tool_context"] = fast_ctx  # type: ignore

                # Batch all function responses into a SINGLE message
                if tool_response_parts:
                    messages.append({"role": "user", "parts": tool_response_parts})

                print(f"[Agent] ⏱ Iteration {iteration + 1} total: {_time.time() - t_iter:.1f}s")

                if got_final:
                    break
                continue

            # ── Text response (no tool calls) → done ──
            if full_response.text:
                final_response = full_response.text
                
                # Safety net: If the model fell back to raw text instead of using await_reply,
                # but it clearly asked a question, lock the provider to preserve context!
                if final_response.strip().endswith("?"):
                    awaiting_reply = True
                    self._pending_reply_provider = provider
                    print(f"[Agent] ⏳ Fallback: Text ended in '?', locking provider {provider.name}")
                    
                break

            # No text and no tool calls — unexpected
            break

        print(f"[Agent] ⏱ TOTAL: {_time.time() - t_start:.1f}s ({iteration + 1} iterations)")

        # ── Send final response ──
        if final_response:
            self.conversation.add_model(final_response)
            if ws_callback:
                # Determine display mode:
                # - "pill" for short tool confirmations (from send_response tool)
                # - "card" for conversational/informational answers
                is_tool_confirmation = any(
                    tc.name != "send_response"
                    for tc in (full_response.tool_calls if full_response else [])
                )
                display = "pill" if len(final_response) <= 40 else "card"
                
                # Get icon url for pill display — base it on the last returned tool in the loop if any
                icon_url = ""
                # We can try to use the most recent original tool call (from messages history) if applicable or just current app
                icon_url = self._get_icon_url("send_response", {}, context.active_app) # Default to active app

                await ws_callback({
                    "type": "response",
                    "payload": {
                        "text": final_response,
                        "app": context.active_app.lower() if context.active_app else "",
                        "display": display,
                        "icon_url": icon_url
                    }
                })

        # Cleanup screenshot
        if context.screenshot_path and os.path.exists(context.screenshot_path):
            try:
                os.remove(context.screenshot_path)
            except Exception:
                pass

        # For pill-mode responses, reset to idle after delay
        # (card-mode handles its own timing in the frontend)
        if final_response and len(final_response) <= 40 and ws_callback:
            await asyncio.sleep(6)
            await ws_callback({"type": "status", "state": "state-idle"})

        return (final_response, awaiting_reply)

    def _tool_display_text(self, tool_name: str, args: dict) -> str:
        """Human-friendly, SHORT status for the pill (must fit ~240px)."""
        displays = {
            "open_app": f"Opening {args.get('app_name', 'app')}",
            "close_window": "Closing window",
            "quit_app": f"Quitting {args.get('app_name', 'app')}",
            "play_media": "Playing media…",
            "web_search": "Searching…",
            "type_text": "Typing…",
            "run_shortcut": "Running shortcut…",
            "open_url": "Opening link…",
            "get_running_apps": "Checking apps…",
            "set_volume": f"Volume → {args.get('level', '')}%",
            "send_response": "Responding…",
            "await_reply": "Thinking…",
            "run_shell": "Running command…",
            "read_file": "Reading file…",
            "write_file": "Writing file…",
            "read_screen": "Looking at screen…",
            "get_ui_tree": "Scanning UI…",
            "click_element": "Clicking…",
            "press_key": f"Pressing '{args.get('key', 'key')}'…",
            "mouse_action": f"{str(args.get('action', 'moving')).capitalize()} mouse…",
            "window_manager": "Managing window…",
            "clipboard_ops": "Clipboard…",
        }
        return displays.get(tool_name, f"Working…")

    def _get_icon_url(self, tool_name: str, args: dict, context_app: str) -> str:
        """Determines the best URL to fetch a favicon/icon for the UI."""
        if tool_name == "open_url" and "url" in args:
            try:
                domain = urllib.parse.urlparse(args["url"]).hostname
                if domain: return f"https://icon.horse/icon/{domain}"
            except Exception:
                pass
        elif tool_name == "play_media":
            return "https://icon.horse/icon/youtube.com"
        elif tool_name == "run_shell":
            return "https://icon.horse/icon/github.com"  # terminal icon
        elif tool_name == "read_file" or tool_name == "write_file":
            return ""  # no icon for file ops
        elif tool_name in ["read_screen", "get_ui_tree", "click_element", "press_key", "mouse_action", "window_manager", "clipboard_ops"]:
            return ""  # no icon for these OS ops
            return ""  # no icon for screen ops
        elif tool_name == "clipboard_ops":
            return ""  # no icon for clipboard
        elif tool_name == "open_app" and "app_name" in args:
            app_name = args["app_name"].lower()
            if "spotify" in app_name: return "https://icon.horse/icon/spotify.com"
            if "music" in app_name: return "https://icon.horse/icon/music.apple.com"
            if "mail" in app_name: return "https://icon.horse/icon/icloud.com"
            if "safari" in app_name: return "https://icon.horse/icon/apple.com"
            if "chrome" in app_name: return "https://icon.horse/icon/google.com"
            if "slack" in app_name: return "https://icon.horse/icon/slack.com"
            if "notion" in app_name: return "https://icon.horse/icon/notion.so"
            if "discord" in app_name: return "https://icon.horse/icon/discord.com"
            if "code" in app_name or "cursor" in app_name: return "https://icon.horse/icon/github.com"
            domain = f"{args['app_name'].replace(' ', '')}.com"
            return f"https://icon.horse/icon/{domain}"
            
        # Default fallback to current active app if available
        if context_app:
            app_name = context_app.lower()
            if "spotify" in app_name: return "https://icon.horse/icon/spotify.com"
            if "safari" in app_name: return "https://icon.horse/icon/apple.com"
            if "chrome" in app_name: return "https://icon.horse/icon/google.com"
            if "electron" in app_name or "moonwalk" in app_name: return "" # hide our own icon
            domain = f"{context_app.replace(' ', '')}.com"
            return f"https://icon.horse/icon/{domain}"
            
        return ""
