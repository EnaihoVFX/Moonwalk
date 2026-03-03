"""
Moonwalk — Cloud Orchestrator (Google Cloud Run)
=================================================
WebSocket server deployed on GCP that:
  1. Accepts connections from the Electron Mac Client
  2. Runs the LLM agent loop (agent.py, providers.py)
  3. Proxies macOS tool executions down to the connected Mac Client via WS
  4. Executes cloud-safe tools (fetch_web_content, run_python) locally on GCP
  5. Can spawn background sub-agents for long-running cloud tasks
"""

import asyncio
import websockets
import json
import os
import uuid
import time
from functools import partial
from typing import Optional
from enum import Enum

# Force print to flush immediately
print = partial(print, flush=True)

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Moonwalk Agent & Providers (the Brain — runs on cloud)
from agent import MoonwalkAgent
from model_router import ModelRouter
from providers import LLMResponse

# Cloud-safe tools that can execute directly on GCP
from tools import registry as tool_registry, set_sub_agent_manager

class AgentState(str, Enum):
    RUNNING = "running"
    WAITING_ON_CHILD = "waiting_on_child"
    PAUSED_FOR_REVIEW = "paused_for_review"
    COMPLETED = "completed"
    FAILED = "error"
    STOPPED = "stopped"
    STOPPING = "stopping"

# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════

PORT = int(os.environ.get("PORT", 8080))  # Cloud Run uses PORT env var

# Tools that can execute ON the cloud server (no macOS dependency)
CLOUD_TOOLS = {
    "send_response", "await_reply",
    "fetch_web_content", "run_python", "run_shell",
    "read_file", "write_file", "replace_in_file", "list_directory",
    "think",
    # Sub-agent management (always runs on cloud)
    "spawn_agent", "list_agents", "get_agent_output", "stop_agent",
    # Sub-agent internal tools
    "sub_agent_log", "sub_agent_complete",
}

# Everything else must be proxied to the Mac Client
# (open_app, click_element, run_shell, read_screen, etc.)


# ═══════════════════════════════════════════════════════════════
#  Remote Tool Executor — Proxies tool calls to Mac Client
# ═══════════════════════════════════════════════════════════════

class RemoteToolExecutor:
    """
    Bridges the agent's tool_registry.execute() calls to the Mac Client.
    For cloud-safe tools, executes locally.
    For macOS tools, sends a JSON RPC over the WebSocket and awaits the result.
    """

    def __init__(self, websocket):
        self.ws = websocket
        self._pending: dict[str, asyncio.Future] = {}

    async def execute(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool — either locally on cloud or remotely on Mac."""
        if tool_name in CLOUD_TOOLS:
            # Execute directly on the cloud server
            return await tool_registry.execute(tool_name, tool_args)

        # Proxy to the Mac Client via WebSocket RPC
        call_id = str(uuid.uuid4())[:8]
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[call_id] = future

        # Send the tool invocation to the Mac Client
        await self.ws.send(json.dumps({
            "type": "tool_request",
            "call_id": call_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
        }))

        # Wait for the Mac Client to return the result (with timeout)
        try:
            result = await asyncio.wait_for(future, timeout=120.0)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(call_id, None)
            return f"Error: Tool '{tool_name}' timed out waiting for Mac Client response."

    def resolve(self, call_id: str, result: str):
        """Called when the Mac Client sends back a tool_response."""
        future = self._pending.pop(call_id, None)
        if future and not future.done():
            future.set_result(result)


# ═══════════════════════════════════════════════════════════════
#  Background Sub-Agent Manager
# ═══════════════════════════════════════════════════════════════

class SubAgentManager:
    """
    Manages cloud-native background agents that run 24/7 on GCP.
    Each sub-agent gets its own MoonwalkAgent instance and runs an LLM loop
    using only cloud-safe tools (fetch_web_content, run_python).
    
    Sub-agents are fire-and-forget: the user says "create an agent to monitor
    Bitcoin prices every hour" and the agent runs in the background, logging
    its outputs. The user can check on it anytime via the main agent.
    """

    # Two-tier system prompt base
    SUB_AGENT_PROMPT_TEMPLATE = """You are a Moonwalk Agent executing a background task.
{tier_description}

## Your Reasoning Protocol (CRITICAL)
You must follow this exact 4-phase protocol to complete your task:
1. **Planning Phase**: First, YOU MUST call the `generate_checklist` tool to break down the task into an explicit, sequential array of steps. You cannot do anything else until you generate a checklist.
2. **Execution Phase**: Use your tools to execute the steps. Do one step at a time.
3. **Verification Phase**: Check your work. Did the code run? Did you find the answer?
4. **Delivery Phase**: Once fully verified, log the final result using `sub_agent_log` and mark the task done with `sub_agent_complete(summary)`.

## Rules
1. Work autonomously. You cannot ask the user questions.
2. Log important findings and progress using `sub_agent_log` so the user can see your progress.
3. If you encounter an error, use `think` to analyze it, then retry or try an alternative approach.
4. If you need to spawn parallel workers, use `spawn_agent`.
5. If you need a human to approve a plan, review an output, or make a decision, use `request_human_review(summary, review_topic)`.
6. Be incredibly thorough and write production-ready code.
7. You may receive messages from other agents or the human. Use `check_messages` to read them.
"""

    def __init__(self, notify_callback=None):
        self.agents: dict = {}  # id -> agent state dict
        self._tasks: dict = {}  # id -> asyncio.Task
        self._notify = notify_callback  # async func to notify the Mac Client
        
        self._state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".moonwalk", "swarm_state.json")
        self._load_state()

    def _save_state(self):
        """Serialize swarm tree to disk so agents survive reboots."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump(self.agents, f, indent=2)
        except Exception as e:
            print(f"[SubAgentManager] Warning: failed to save state: {e}")

    def _load_state(self):
        """Load swarm tree from disk and resume paused/running agents."""
        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, "r") as f:
                    self.agents = json.load(f)
                print(f"[SubAgentManager] Loaded {len(self.agents)} agents from state file.")
                
                # Mark previously running/waiting agents as stopped since we rebooted
                for aid, state in self.agents.items():
                    if state.get("status") in [AgentState.RUNNING.value, AgentState.WAITING_ON_CHILD.value]:
                        state["status"] = AgentState.STOPPED.value
                        state["error"] = "Server rebooted during execution. Task stopped."
                self._save_state()
            except Exception as e:
                print(f"[SubAgentManager] Warning: failed to load state: {e}")

    async def spawn(self, task_description: str, intrusive: bool = False,
                    system_prompt: Optional[str] = None,
                    allowed_tools: Optional[list[str]] = None,
                    deliverable_format: Optional[str] = None,
                    ws_callback=None) -> str:
        """Spawn a new background sub-agent with a specific task."""
        agent_id = str(uuid.uuid4())[:8]

        # Create a dedicated agent instance for this sub-agent
        sub_agent = MoonwalkAgent()

        self.agents[agent_id] = {
            "task": task_description,
            "intrusive": intrusive,
            "system_prompt": system_prompt,
            "allowed_tools": allowed_tools,
            "deliverable_format": deliverable_format,
            "status": AgentState.RUNNING.value,
            "created_at": time.time(),
            "completed_at": None,
            "logs": [],           # List of log messages from the sub-agent
            "checklist": [],      # Array of strings planned by the sub-agent
            "result": None,       # Final result summary
            "error": None,        # Error if crashed
            "iterations": 0,      # How many LLM loops it ran
        }
        self._save_state()

        print(f"[SubAgent:{agent_id}] ═══ SPAWNED ═══")
        print(f"[SubAgent:{agent_id}] Task: {task_description}")

        # Create the asyncio task to run the sub-agent loop
        task = asyncio.create_task(
            self._run_sub_agent(agent_id, sub_agent, task_description, ws_callback)
        )
        self._tasks[agent_id] = task

        # Handle task completion/errors
        task.add_done_callback(lambda t: self._on_task_done(agent_id, t))

        # Notify frontend immediately so the UI drawer registers the agent
        if ws_callback:
            # We must schedule it because spawn_agent is called from the synchronous tool executor loop
            asyncio.create_task(ws_callback({
                "type": "sub_agent_update",
                "agent_id": agent_id,
                "status": "spawned",
                "task": task_description,
            }))

        return json.dumps({
            "agent_id": agent_id,
            "status": "spawned",
            "task": task_description,
        })

    async def _run_sub_agent(self, agent_id: str, agent: MoonwalkAgent,
                              task_description: str, ws_callback=None):
        """Run a sub-agent's LLM loop."""
        import perception

        state = self.agents[agent_id]
        intrusive = state.get("intrusive", False)

        # Build a minimal cloud context (no macOS desktop info default)
        context = perception.ContextSnapshot(
            active_app="Mac OS" if intrusive else "Google Cloud",
            window_title=f"Background Agent: {task_description[:50]}",
            browser_url=None,
        )

        # Create sub-agent-specific tools that write to this agent's log
        sub_tools = self._create_sub_agent_tools(agent_id, ws_callback=ws_callback)

        # Build the dynamic prompt based on tier
        custom_prompt = state.get("system_prompt")
        deliverable_format = state.get("deliverable_format")
        allowed_tools = state.get("allowed_tools")
        
        if custom_prompt:
            tier_description = custom_prompt
        else:
            tier_description = (
                "You are a HIGHLY CAPABLE LOCAL AGENT. You have FULL ACCESS to the user's Mac screen, mouse, and local apps (like Mail, Safari). You can open apps, click elements, read the screen, and run shell commands."
                if intrusive else
                "You are a CLOUD AGENT running headlessly on Google Cloud Run. You DO NOT have access to the user's Mac screen or local UI apps. You ONLY have access to file I/O, Python, shell commands, and web scraping."
            )
            
        if deliverable_format:
            tier_description += f"\n\n## Deliverable Format\nYou must format your final output strictly as follows: {deliverable_format}"

        original_prompt = agent._build_system_prompt
        agent._build_system_prompt = lambda: self.SUB_AGENT_PROMPT_TEMPLATE.format(tier_description=tier_description)  # type: ignore

        # Override the tool executor based on tier and allowed_tools
        class ScopedExecutor:
            async def execute(self, tool_name: str, tool_args: dict) -> str:
                # 1. Enforce allowed_tools whitelist if specified
                if allowed_tools is not None:
                    if tool_name not in allowed_tools and tool_name not in ["sub_agent_log", "sub_agent_complete", "think", "generate_checklist", "request_human_review", "check_messages"]:
                        return f"ERROR: Tool '{tool_name}' is not allowed by your current Blueprint overlay. Expected exactly one of: {allowed_tools}"
                
                # 2. Enforce Cloud-only tier if not intrusive
                if not intrusive and tool_name not in CLOUD_TOOLS and tool_name not in ["sub_agent_log", "sub_agent_complete", "generate_checklist", "request_human_review", "check_messages"]:
                    return f"ERROR: Tool '{tool_name}' requires Mac UI access. You are a background cloud agent. Only use file I/O, scraping, or python."
                
                return await tool_registry.execute(tool_name, tool_args)
                
        agent._remote_executor = ScopedExecutor()

        # Create a no-op ws_callback (sub-agents don't stream to UI directly)
        async def sub_ws_callback(msg: dict):
            # Log thoughts for debugging
            if msg.get("type") == "thought":
                content = msg.get("content", "")
                if content:
                    state["logs"].append(f"🧠 {content[:100]}...")

            # Forward completion notifications to the main client
            if ws_callback and msg.get("type") == "response":
                await ws_callback({
                    "type": "sub_agent_update",
                    "agent_id": agent_id,
                    "status": "progress",
                    "message": msg.get("payload", {}).get("text", ""),
                })

        try:
            # Multi-iteration agent loop
            MAX_ITERATIONS = 25
            
            last_tool_count = 0
            stall_counter = 0

            for i in range(MAX_ITERATIONS):
                if state["status"] != AgentState.RUNNING.value:
                    break
                    
                state["iterations"] += 1
                print(f"[SubAgent:{agent_id}] Loop {i+1}/{MAX_ITERATIONS}")
                
                # Determine prompt for this iteration
                if i == 0:
                    iter_prompt = f"BACKGROUND TASK: {task_description}"
                elif i % 5 == 0:
                    iter_prompt = "[SYSTEM CHECKPOINT] Please review your progress. List what you have completed so far, what is remaining, and whether you are on track. If you are stuck, change your approach."
                elif stall_counter >= 3:
                    iter_prompt = "[SYSTEM WARNING] You have not made any progress in the last 3 iterations. You MUST call a tool now, or call sub_agent_complete() if you are finished."
                    stall_counter = 0 # reset after warning
                else:
                    iter_prompt = "[SYSTEM] Your previous execution completed. Resume your work or call sub_agent_complete() if you are 100% finished with the task."

                # Run one pass of the LLM tool loop
                # Check tool call count before run
                start_tools = len(agent.conversation._turns) # Hacky way to see if tools were added

                result = await agent.run(
                    iter_prompt,
                    context,
                    ws_callback=sub_ws_callback,
                )
                
                # Check if agent did anything
                end_tools = len(agent.conversation._turns)
                if end_tools == start_tools + 2: # Just the prompt and response, no tools
                    stall_counter += 1
                else:
                    stall_counter = 0

            if state["status"] == AgentState.RUNNING.value:
                # Agent exhausted all 25 iterations without calling sub_agent_complete
                state["status"] = AgentState.FAILED.value
                state["error"] = "Max iterations (25) reached without completion."
                state["completed_at"] = time.time()
                self._save_state()
                result_text = "FAILED: Max iterations reached."
            else:
                result_text = state["result"] or "Task completed"

            print(f"[SubAgent:{agent_id}] ✓ Loop ended. Final status: {state['status']}")

            # Notify the user that the background agent finished
            if ws_callback:
                await ws_callback({
                    "type": "sub_agent_update",
                    "agent_id": agent_id,
                    "status": state["status"],
                    "task": state["task"],
                    "result": str(result_text)[:500],
                })

        except asyncio.CancelledError:
            state["status"] = AgentState.STOPPED.value
            state["completed_at"] = time.time()
            self._save_state()
            print(f"[SubAgent:{agent_id}] ✕ Cancelled")
            raise

        except Exception as e:
            state["status"] = AgentState.FAILED.value
            state["error"] = str(e)
            state["completed_at"] = time.time()
            self._save_state()
            print(f"[SubAgent:{agent_id}] ✕ Error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Restore original prompt builder
            agent._build_system_prompt = original_prompt  # type: ignore

    def _create_sub_agent_tools(self, agent_id: str, ws_callback=None) -> dict:
        """Create tools specific to a sub-agent (logging, completion)."""
        state = self.agents[agent_id]

        async def sub_agent_log(message: str = "") -> str:
            """Log a message to the sub-agent's output."""
            timestamp = time.strftime("%H:%M:%S")
            entry = f"[{timestamp}] {message}"
            state["logs"].append(entry)
            self._save_state()
            print(f"[SubAgent:{agent_id}] LOG: {message[:100]}")
            return f"Logged: {message[:100]}"

        async def sub_agent_complete(summary: str = "") -> str:
            """Mark this sub-agent's task as complete."""
            state["result"] = summary
            state["status"] = AgentState.COMPLETED.value
            state["completed_at"] = time.time()
            self._save_state()
            return f"RESPONSE:{summary}"

        async def generate_checklist(tasks: list) -> str:
            """Create a concrete, sequential checklist of sub-tasks."""
            state["checklist"] = tasks
            self._save_state()
            print(f"[SubAgent:{agent_id}] CHECKLIST: {tasks}")
            
            # Send WebSocket update to UI
            if ws_callback:
                asyncio.create_task(ws_callback({
                    "type": "sub_agent_update",
                    "agent_id": agent_id,
                    "status": "checklist_updated",
                    "checklist": tasks
                }))
                
            return f"Checklist generated with {len(tasks)} items. Proceed with Execution Phase."

        async def request_human_review(summary: str, review_topic: str) -> str:
            """Pause execution and ask the user for approval or feedback."""
            state["status"] = AgentState.PAUSED_FOR_REVIEW.value
            state["review_topic"] = review_topic
            state["result"] = summary
            self._save_state()
            print(f"[SubAgent:{agent_id}] PAUSED FOR REVIEW: {review_topic}")
            
            if ws_callback:
                asyncio.create_task(ws_callback({
                    "type": "sub_agent_update",
                    "agent_id": agent_id,
                    "status": "paused_for_review",
                    "review_topic": review_topic,
                    "result": summary
                }))
                
            future = asyncio.Future()
            state["resume_future"] = future
            
            feedback = await future
            
            state["status"] = AgentState.RUNNING.value
            state.pop("review_topic", None)
            self._save_state()
            
            return f"HUMAN FEEDBACK RECEIVED: {feedback}"

        async def check_messages() -> str:
            """Check for unread messages sent by other agents or the human."""
            msgs = state.get("messages", [])
            if not msgs:
                return "No unread messages."
            
            # Read and clear
            messages_str = "\n".join([f"- {m}" for m in msgs])
            state["messages"] = []
            self._save_state()
            return f"You have {len(msgs)} new messages:\n{messages_str}"

        # We MUST re-register these tools unconditionally every time an agent runs!
        # Otherwise, the closure for `state` and `agent_id` gets locked to the very first agent forever,
        # preventing subsequent agents from ever completing their tasks.
        tool_registry.register(
            name="generate_checklist",
            description="Create the sequential checklist of sub-tasks before beginning execution.",
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of sub-tasks to complete."
                    }
                },
                "required": ["tasks"]
            }
        )(generate_checklist)

        tool_registry.register(
            name="request_human_review",
            description="Pause your execution and ask the human for approval or feedback before proceeding.",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What you have done so far."},
                    "review_topic": {"type": "string", "description": "Exactly what you need the human to review or decide."}
                },
                "required": ["summary", "review_topic"]
            }
        )(request_human_review)

        tool_registry.register(
            name="sub_agent_log",
            description="Log a progress message or finding. The user can read all logs later.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message to log"}
                },
                "required": ["message"]
            }
        )(sub_agent_log)

        tool_registry.register(
            name="check_messages",
            description="Check if any other agents (or the human) have sent you messages or feedback.",
            parameters={"type": "object", "properties": {}}
        )(check_messages)

        tool_registry.register(
            name="sub_agent_complete",
            description="Mark this background task as complete with a final summary.",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Final summary of what was accomplished"}
                },
                "required": ["summary"]
            }
        )(sub_agent_complete)

        return {"sub_agent_log": sub_agent_log, "sub_agent_complete": sub_agent_complete}

    def _on_task_done(self, agent_id: str, task: asyncio.Task):
        """Callback when an asyncio.Task finishes."""
        self._tasks.pop(agent_id, None)
        exc = task.exception() if not task.cancelled() else None
        if exc:
            self.agents[agent_id]["status"] = AgentState.FAILED.value
            self.agents[agent_id]["error"] = str(exc)
            self._save_state()
            print(f"[SubAgent:{agent_id}] Task crashed: {exc}")

    def list_agents(self) -> str:
        """List all sub-agents with their status."""
        if not self.agents:
            return "No background agents running."

        lines = []
        for aid, state in self.agents.items():
            elapsed = time.time() - state["created_at"]
            elapsed_str = f"{elapsed:.0f}s" if elapsed < 60 else f"{elapsed/60:.1f}m"
            lines.append(
                f"• [{state['status'].upper()}] {aid}: {state['task'][:60]} "
                f"({elapsed_str} elapsed, {len(state['logs'])} logs)"
            )
        return "\n".join(lines)

    def get_output(self, agent_id: str) -> str:
        """Get the full output logs and result of a sub-agent."""
        if agent_id not in self.agents:
            return f"Agent '{agent_id}' not found."

        state = self.agents[agent_id]
        parts = [
            f"Agent: {agent_id}",
            f"Task: {state['task']}",
            f"Status: {state['status']}",
            f"Iterations: {state['iterations']}",
        ]

        if state["logs"]:
            parts.append(f"\n--- Logs ({len(state['logs'])} entries) ---")
            for log in state["logs"][-20:]:  # Last 20 logs
                parts.append(log)

        if state["result"]:
            parts.append(f"\n--- Result ---")
            parts.append(state["result"])

        if state["error"]:
            parts.append(f"\n--- Error ---")
            parts.append(state["error"])

        return "\n".join(parts)

    def stop(self, agent_id: str) -> str:
        """Stop a running sub-agent."""
        if agent_id not in self.agents:
            return f"Agent '{agent_id}' not found."

        task = self._tasks.get(agent_id)
        if task and not task.done():
            task.cancel()
            self.agents[agent_id]["status"] = AgentState.STOPPING.value
            self._save_state()
            return f"Agent '{agent_id}' is being stopped."

        self.agents[agent_id]["status"] = AgentState.STOPPED.value
        self._save_state()
        return f"Agent '{agent_id}' stopped."


# ═══════════════════════════════════════════════════════════════
#  WebSocket Handler — Orchestrates Everything
# ═══════════════════════════════════════════════════════════════

class CloudOrchestrator:
    """
    Manages a single Mac Client connection.
    Handles incoming messages (audio transcriptions, tool responses)
    and runs the Moonwalk agent loop on the cloud.
    """

    def __init__(self, websocket):
        self.ws = websocket
        self.agent = MoonwalkAgent()
        self.executor = RemoteToolExecutor(websocket)
        self.sub_agents = SubAgentManager()

    async def handle_message(self, data: dict):
        """Route incoming messages from the Mac Client."""
        msg_type = data.get("type")

        if msg_type == "transcription":
            # Mac Client transcribed speech and sent us the text
            text = data.get("text", "")
            context_data = data.get("context", {})
            print(f"\n[Cloud] ═══ New Request ═══")
            print(f"[Cloud] Text: '{text}'")
            await self.run_agent(text, context_data)

        elif msg_type == "tool_response":
            # Mac Client returning a tool execution result
            call_id = data.get("call_id", "")
            result = data.get("result", "")
            self.executor.resolve(call_id, result)

        elif msg_type == "dashboard_sync":
            # Send current state back to the UI
            try:
                # Filter out un-serializable objects (like the resume_future)
                safe_agents = {}
                for aid, st in self.sub_agents.agents.items():
                    safe_st = {k: v for k, v in st.items() if k != "resume_future"}
                    safe_agents[aid] = safe_st

                await self.ws.send(json.dumps({
                    "type": "dashboard_state",
                    "agents": safe_agents
                }))
            except Exception as e:
                print(f"[Cloud] Failed to send dashboard sync: {e}")

        elif msg_type == "resume_agent":
            agent_id = data.get("agent_id")
            feedback = data.get("feedback", "No feedback provided. Approved.")
            
            state = self.sub_agents.agents.get(agent_id)
            if state and "resume_future" in state:
                future = state["resume_future"]
                if not future.done():
                    print(f"[Cloud] Resuming agent {agent_id} with feedback: {feedback[:50]}")
                    future.set_result(feedback)
            
        elif msg_type == "text_input":
            # Direct text input (from Electron text box, not voice)
            text = data.get("text", "")
            context_data = data.get("context", {})
            print(f"\n[Cloud] ═══ Text Input ═══")
            print(f"[Cloud] Text: '{text}'")
            await self.run_agent(text, context_data)

        elif msg_type == "ping":
            await self.ws.send(json.dumps({"type": "pong"}))

    async def run_agent(self, text: str, context_data: dict):
        """Run the Moonwalk agent loop with remote tool execution."""
        import perception

        # Build a ContextSnapshot from the data the Mac Client sent us
        context = perception.ContextSnapshot(
            active_app=context_data.get("active_app", "Unknown"),
            window_title=context_data.get("window_title", ""),
            browser_url=context_data.get("browser_url"),
        )

        # Define the WS callback to stream UI updates back to the Mac Client
        async def ws_callback(msg: dict):
            try:
                await self.ws.send(json.dumps(msg))
            except Exception as e:
                print(f"[Cloud WS] Error sending: {e}")

        # Inject the SubAgentManager into tools.py so spawn_agent works
        set_sub_agent_manager(self.sub_agents, ws_callback=ws_callback)

        # Monkey-patch tool_registry.execute to use our RemoteToolExecutor
        original_execute = tool_registry.execute
        tool_registry.execute = self.executor.execute  # type: ignore

        try:
            # Initialize router concurrently
            await self.agent.router.initialize()

            # Run the agent loop — it will call tools via self.executor
            result = await self.agent.run(text, context, ws_callback=ws_callback)

            if isinstance(result, tuple):
                _, awaiting_reply = result
            else:
                awaiting_reply = False

            # Tell the Mac Client whether we're awaiting a reply
            if awaiting_reply:
                await self.ws.send(json.dumps({
                    "type": "await_reply",
                }))
        except Exception as e:
            print(f"[Cloud] Agent error: {e}")
            import traceback
            traceback.print_exc()
            await self.ws.send(json.dumps({
                "type": "response",
                "payload": {"text": "Cloud agent encountered an error.", "app": ""}
            }))
        finally:
            # Restore original execute
            tool_registry.execute = original_execute  # type: ignore


async def main_handler(websocket):
    """Handle a single Mac Client WebSocket connection."""
    print(f"[Cloud] Mac Client connected from {websocket.remote_address}")
    orchestrator = CloudOrchestrator(websocket)

    try:
        await websocket.send(json.dumps({"type": "status", "state": "state-idle"}))

        async for message in websocket:
            try:
                data = json.loads(message)
                await orchestrator.handle_message(data)
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"[Cloud] Error handling message: {e}")
                import traceback
                traceback.print_exc()

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[Cloud] Mac Client disconnected: {e}")
    except Exception as e:
        print(f"[Cloud] Unexpected error: {e}")


async def main():
    """Start the Cloud Orchestrator WebSocket server."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        print("!" * 60)
        print("GEMINI_API_KEY not set. Agent will not function.")
        print("!" * 60)

    print(f"[Cloud] Moonwalk Cloud Orchestrator starting on port {PORT}...")
    async with websockets.serve(
        main_handler,
        "0.0.0.0",  # Bind to all interfaces (required for Cloud Run)
        PORT,
        origins=None,
        ping_interval=30,
        ping_timeout=120,
        max_size=10 * 1024 * 1024,  # 10MB max message size
    ):
        print(f"[Cloud] ✓ Listening on ws://0.0.0.0:{PORT}")
        print(f"[Cloud] Waiting for Mac Client connections...")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())
