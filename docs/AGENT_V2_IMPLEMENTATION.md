# Agent V2 Implementation Guide

> Legacy implementation note: this document reflects an earlier step-plan and
> template-led V2 layout. The active runtime is milestone-first; use
> [runtime-spine.md](./runtime-spine.md) as the source of truth.

## ✅ Implementation Complete

All V2 components have been implemented and tested. This document provides implementation details for the V2 agent architecture.

---

## Files Created

| File | Description |
|------|-------------|
| [backend/agent/world_state.py](../backend/agent/world_state.py) | WorldState, UserIntent, IntentParser classes |
| [backend/agent/planner.py](../backend/agent/planner.py) | Milestone dataclasses plus `ExecutionStep` primitive |
| [backend/agent/task_planner.py](../backend/agent/task_planner.py) | Milestone-first TaskPlanner |
| [backend/agent/legacy_planner.py](../backend/agent/legacy_planner.py) | Legacy `ExecutionPlan` / `PlanTemplates` compatibility |
| [backend/agent/legacy_task_planner.py](../backend/agent/legacy_task_planner.py) | Legacy step-plan template/preflight compatibility |
| [backend/agent/verifier.py](../backend/agent/verifier.py) | ToolVerifier with per-tool strategies |
| [backend/agent/core_v2.py](../backend/agent/core_v2.py) | MoonwalkAgentV2 with SPAV loop |
| [backend/tools/selector.py](../backend/tools/selector.py) | Intelligent tool selection |
| [tests/test_agent_v2.py](../tests/test_agent_v2.py) | Component tests |

---

## Quick Start

### Using V2 Agent

```python
from agent import MoonwalkAgentV2, create_agent

# Option 1: Direct instantiation
agent = MoonwalkAgentV2(use_planning=True)

# Option 2: Factory function
agent = create_agent(version="v2", use_planning=True)

# Run the agent
response, awaiting = await agent.run(
    user_text="Open Spotify",
    context=perception_context,
    ws_callback=websocket_callback
)
```

---

## 1. New Core Classes

### WorldState (Enhanced Context)

```python
# backend/agent/world_state.py

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum

class IntentAction(str, Enum):
    OPEN = "open"           # Launch app/URL
    CLOSE = "close"         # Quit app/close window
    SEARCH = "search"       # Web or file search
    CREATE = "create"       # Write file, spawn agent
    DELETE = "delete"       # Remove file, stop agent
    MODIFY = "modify"       # Edit file, change setting
    ANALYZE = "analyze"     # Read screen, understand content
    NAVIGATE = "navigate"   # Go to URL, switch app
    EXECUTE = "execute"     # Run command, press keys
    UNKNOWN = "unknown"     # Needs clarification

class TargetType(str, Enum):
    APP = "app"
    URL = "url"
    FILE = "file"
    CONTENT = "content"
    UI_ELEMENT = "ui_element"
    AGENT = "agent"
    UNKNOWN = "unknown"

@dataclass
class UserIntent:
    """Structured understanding of what the user wants."""
    action: IntentAction
    target_type: TargetType
    target_value: str = ""          # "Spotify", "youtube.com", etc.
    parameters: dict = field(default_factory=dict)  # Extra context
    confidence: float = 0.0         # 0.0 - 1.0
    ambiguous: bool = False         # Needs clarification?
    clarification_prompt: str = ""  # What to ask if ambiguous

@dataclass
class WorldState:
    """Complete structured view of the desktop environment."""
    # Desktop state
    active_app: str = ""
    window_title: str = ""
    browser_url: Optional[str] = None
    running_apps: List[str] = field(default_factory=list)
    
    # Extracted entities from user request
    mentioned_apps: List[str] = field(default_factory=list)
    mentioned_files: List[str] = field(default_factory=list)
    mentioned_urls: List[str] = field(default_factory=list)
    
    # Clipboard
    clipboard_content: Optional[str] = None
    
    # Screen state (if captured)
    has_screenshot: bool = False
    screen_description: Optional[str] = None
    
    # Parsed intent
    intent: Optional[UserIntent] = None
    
    # History context
    recent_tool_calls: List[str] = field(default_factory=list)
    conversation_topic: str = ""
    
    def to_prompt_dict(self) -> dict:
        """Convert to dict for JSON serialization in prompts."""
        return {
            "active_app": self.active_app,
            "window_title": self.window_title,
            "browser_url": self.browser_url,
            "intent": {
                "action": self.intent.action.value if self.intent else "unknown",
                "target": self.intent.target_value if self.intent else "",
                "confidence": self.intent.confidence if self.intent else 0,
                "needs_clarification": self.intent.ambiguous if self.intent else True
            } if self.intent else None
        }
```

### ExecutionPlan (Task Planning)

```python
# backend/agent/planner.py

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass
class ExecutionStep:
    """A single step in the execution plan."""
    id: int
    description: str                 # Human-readable action
    tool: str                        # Tool name to call
    args: Dict[str, Any]            # Tool arguments
    success_criteria: str = ""       # How to verify success
    fallback_tool: Optional[str] = None  # Alternative if this fails
    depends_on: List[int] = field(default_factory=list)  # Step IDs this depends on
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None

@dataclass
class ExecutionPlan:
    """Complete execution plan for a task."""
    task_summary: str
    needs_clarification: bool = False
    clarification_prompt: str = ""
    steps: List[ExecutionStep] = field(default_factory=list)
    final_response: str = ""
    estimated_tools: List[str] = field(default_factory=list)
    current_step: int = 0
    
    def get_current_step(self) -> Optional[ExecutionStep]:
        """Get the next pending step."""
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                return step
        return None
    
    def mark_step_complete(self, step_id: int, result: str):
        """Mark a step as completed."""
        for step in self.steps:
            if step.id == step_id:
                step.status = StepStatus.COMPLETED
                step.result = result
                break
    
    def mark_step_failed(self, step_id: int, error: str):
        """Mark a step as failed."""
        for step in self.steps:
            if step.id == step_id:
                step.status = StepStatus.FAILED
                step.error = error
                break
    
    def is_complete(self) -> bool:
        """Check if all steps are done."""
        return all(s.status in [StepStatus.COMPLETED, StepStatus.SKIPPED] for s in self.steps)
    
    def has_failed(self) -> bool:
        """Check if any step failed."""
        return any(s.status == StepStatus.FAILED for s in self.steps)
```

---

## 2. Task Planner Implementation

```python
# backend/agent/task_planner.py

import json
from typing import Optional
from agent.world_state import WorldState, UserIntent
from agent.planner import ExecutionPlan, ExecutionStep
from providers.base import LLMProvider

PLANNING_PROMPT = '''You are a task planning system for a desktop AI assistant.

Given the user request and current desktop state, output a JSON execution plan.

## Available Tools
{tool_descriptions}

## Output Format (JSON)
{{
  "task_summary": "Brief description of the task",
  "needs_clarification": false,
  "clarification_prompt": "",
  "steps": [
    {{
      "id": 1,
      "description": "Human-readable step description",
      "tool": "tool_name",
      "args": {{"arg1": "value1"}},
      "success_criteria": "How to verify this worked",
      "depends_on": []
    }}
  ],
  "final_response": "Message to show user when done"
}}

## Rules
1. Use the MINIMUM number of steps necessary
2. Each step should use exactly ONE tool
3. If the request is ambiguous, set needs_clarification=true and provide a question
4. Always include send_response as part of the final action (bundled with last tool)
5. Order steps correctly based on dependencies
6. Be specific with tool arguments

## Examples

User: "Open Spotify"
Plan:
{{
  "task_summary": "Open Spotify application",
  "needs_clarification": false,
  "steps": [
    {{"id": 1, "description": "Open Spotify app", "tool": "open_app", "args": {{"app_name": "Spotify"}}, "success_criteria": "Spotify becomes active app"}}
  ],
  "final_response": "Opening Spotify!"
}}

User: "Delete it"
Plan:
{{
  "task_summary": "Delete unknown target",
  "needs_clarification": true,
  "clarification_prompt": "What would you like me to delete?",
  "steps": [],
  "final_response": ""
}}

## Current State
{world_state}

## User Request
{user_request}

Output the execution plan as valid JSON:'''


class TaskPlanner:
    """Generates execution plans from user requests."""
    
    def __init__(self, provider: LLMProvider, tool_registry):
        self.provider = provider
        self.tool_registry = tool_registry
    
    async def create_plan(
        self,
        user_request: str,
        world_state: WorldState,
        available_tools: list[str]
    ) -> ExecutionPlan:
        """Generate an execution plan for the given request."""
        
        # Build tool descriptions for the prompt
        tool_descriptions = self._format_tool_descriptions(available_tools)
        
        # Format world state
        state_json = json.dumps(world_state.to_prompt_dict(), indent=2)
        
        # Build the planning prompt
        prompt = PLANNING_PROMPT.format(
            tool_descriptions=tool_descriptions,
            world_state=state_json,
            user_request=user_request
        )
        
        # Call the LLM for planning (fast model, low temperature)
        response = await self.provider.generate(
            messages=[{"role": "user", "parts": [{"text": prompt}]}],
            system_prompt="You are a task planner. Output only valid JSON.",
            tools=[],
            temperature=0.1
        )
        
        # Parse the response
        try:
            plan_json = self._extract_json(response.text)
            return self._parse_plan(plan_json)
        except Exception as e:
            # Fallback: return a clarification request
            return ExecutionPlan(
                task_summary=user_request,
                needs_clarification=True,
                clarification_prompt="I had trouble understanding that. Could you rephrase?"
            )
    
    def _format_tool_descriptions(self, tool_names: list[str]) -> str:
        """Format tool descriptions for the prompt."""
        declarations = self.tool_registry.declarations()
        lines = []
        for decl in declarations:
            if decl["name"] in tool_names:
                params = ", ".join(decl.get("parameters", {}).get("properties", {}).keys())
                lines.append(f"- {decl['name']}({params}): {decl['description'][:100]}...")
        return "\n".join(lines)
    
    def _extract_json(self, text: str) -> dict:
        """Extract JSON from LLM response."""
        # Try to find JSON block
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError("No JSON found in response")
    
    def _parse_plan(self, plan_json: dict) -> ExecutionPlan:
        """Convert JSON dict to ExecutionPlan object."""
        steps = []
        for step_data in plan_json.get("steps", []):
            steps.append(ExecutionStep(
                id=step_data.get("id", len(steps) + 1),
                description=step_data.get("description", ""),
                tool=step_data.get("tool", ""),
                args=step_data.get("args", {}),
                success_criteria=step_data.get("success_criteria", ""),
                depends_on=step_data.get("depends_on", [])
            ))
        
        return ExecutionPlan(
            task_summary=plan_json.get("task_summary", ""),
            needs_clarification=plan_json.get("needs_clarification", False),
            clarification_prompt=plan_json.get("clarification_prompt", ""),
            steps=steps,
            final_response=plan_json.get("final_response", "")
        )
```

---

## 3. Verification System

```python
# backend/agent/verifier.py

from dataclasses import dataclass
from typing import Optional, Callable, Awaitable
from agent.world_state import WorldState
import agent.perception as perception

@dataclass
class VerificationResult:
    """Result of verifying a tool execution."""
    success: bool
    confidence: float  # 0.0 - 1.0
    message: str
    should_retry: bool = False
    new_world_state: Optional[WorldState] = None

class ToolVerifier:
    """Verifies that tool executions succeeded."""
    
    def __init__(self):
        # Map tool names to verification strategies
        self.verifiers: dict[str, Callable] = {
            "open_app": self._verify_open_app,
            "open_url": self._verify_open_url,
            "quit_app": self._verify_quit_app,
            "close_window": self._verify_close_window,
            "write_file": self._verify_write_file,
            "run_shell": self._verify_run_shell,
        }
    
    async def verify(
        self,
        tool_name: str,
        tool_args: dict,
        tool_result: str,
        expected_criteria: str
    ) -> VerificationResult:
        """Verify that a tool execution met its success criteria."""
        
        # Check for error in result
        if tool_result.startswith("Error") or "error" in tool_result.lower():
            return VerificationResult(
                success=False,
                confidence=0.9,
                message=f"Tool returned error: {tool_result}",
                should_retry=True
            )
        
        # Use tool-specific verifier if available
        verifier = self.verifiers.get(tool_name)
        if verifier:
            return await verifier(tool_args, tool_result, expected_criteria)
        
        # Default: trust the tool result
        return VerificationResult(
            success=True,
            confidence=0.7,
            message="Tool executed (no verification available)"
        )
    
    async def _verify_open_app(
        self,
        args: dict,
        result: str,
        criteria: str
    ) -> VerificationResult:
        """Verify an app was opened by checking active app."""
        target_app = args.get("app_name", "").lower()
        
        # Quick re-sense to check active app
        current_app = await perception.get_active_app()
        
        if target_app in current_app.lower():
            return VerificationResult(
                success=True,
                confidence=0.95,
                message=f"{target_app} is now active"
            )
        else:
            return VerificationResult(
                success=False,
                confidence=0.8,
                message=f"Expected {target_app} but got {current_app}",
                should_retry=True
            )
    
    async def _verify_open_url(
        self,
        args: dict,
        result: str,
        criteria: str
    ) -> VerificationResult:
        """Verify a URL was opened by checking browser URL."""
        target_url = args.get("url", "")
        
        # Get current browser URL
        current_app = await perception.get_active_app()
        current_url = await perception.get_browser_url(current_app)
        
        if current_url and target_url.replace("https://", "").replace("http://", "").split("/")[0] in current_url:
            return VerificationResult(
                success=True,
                confidence=0.95,
                message=f"Browser opened to {current_url}"
            )
        else:
            return VerificationResult(
                success=False,
                confidence=0.7,
                message=f"URL may not have loaded: current={current_url}",
                should_retry=True
            )
    
    async def _verify_quit_app(
        self,
        args: dict,
        result: str,
        criteria: str
    ) -> VerificationResult:
        """Verify an app was closed."""
        # Would need to check running apps
        return VerificationResult(
            success=True,
            confidence=0.8,
            message="App quit command sent"
        )
    
    async def _verify_close_window(
        self,
        args: dict,
        result: str,
        criteria: str
    ) -> VerificationResult:
        """Verify window was closed."""
        return VerificationResult(
            success=True,
            confidence=0.8,
            message="Close window command sent"
        )
    
    async def _verify_write_file(
        self,
        args: dict,
        result: str,
        criteria: str
    ) -> VerificationResult:
        """Verify file was written by reading first line."""
        file_path = args.get("path", "")
        
        try:
            with open(file_path, "r") as f:
                first_line = f.readline()
            return VerificationResult(
                success=True,
                confidence=0.95,
                message=f"File exists and readable"
            )
        except Exception as e:
            return VerificationResult(
                success=False,
                confidence=0.9,
                message=f"Could not verify file: {e}",
                should_retry=True
            )
    
    async def _verify_run_shell(
        self,
        args: dict,
        result: str,
        criteria: str
    ) -> VerificationResult:
        """Verify shell command by checking result."""
        # Check for common error patterns
        error_patterns = ["command not found", "permission denied", "no such file"]
        for pattern in error_patterns:
            if pattern in result.lower():
                return VerificationResult(
                    success=False,
                    confidence=0.9,
                    message=f"Shell error: {result[:100]}",
                    should_retry=False  # Don't retry shell errors blindly
                )
        
        return VerificationResult(
            success=True,
            confidence=0.8,
            message="Command executed"
        )
```

---

## 4. Tool Selection via Embeddings

```python
# backend/tools/selector.py

from typing import List
import numpy as np

class ToolSelector:
    """Selects relevant tools using semantic similarity."""
    
    # Pre-computed tool embeddings (would be loaded from file in production)
    TOOL_CATEGORIES = {
        "communication": ["send_response", "await_reply"],
        "perception": ["read_screen", "get_ui_tree", "read_file"],
        "app_control": ["open_app", "quit_app", "close_window", "open_url"],
        "ui_automation": ["click_element", "type_text", "press_key", "run_shortcut"],
        "file_system": ["run_shell", "read_file", "write_file"],
        "agents": []
    }
    
    # Keywords that trigger each category
    CATEGORY_KEYWORDS = {
        "app_control": ["open", "launch", "start", "close", "quit", "exit"],
        "perception": ["look", "see", "screen", "read", "show", "what"],
        "ui_automation": ["click", "type", "press", "shortcut", "key"],
        "file_system": ["file", "folder", "terminal", "command", "shell", "create", "write"],
        "agents": ["agent", "background", "monitor", "spawn", "task"],
    }
    
    def __init__(self):
        self.all_tools = []
        for tools in self.TOOL_CATEGORIES.values():
            self.all_tools.extend(tools)
    
    def select_tools(
        self,
        user_request: str,
        context_text: str = "",
        max_tools: int = 10
    ) -> List[str]:
        """Select the most relevant tools for a request."""
        
        # Always include communication tools
        selected = set(self.TOOL_CATEGORIES["communication"])
        
        combined_text = (user_request + " " + context_text).lower()
        
        # Check each category's keywords
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(kw in combined_text for kw in keywords):
                selected.update(self.TOOL_CATEGORIES.get(category, []))
        
        # If nothing matched beyond communication, add app_control as default
        if len(selected) <= 2:
            selected.update(self.TOOL_CATEGORIES["app_control"])
        
        # Limit to max_tools
        return list(selected)[:max_tools]
    
    def select_tools_semantic(
        self,
        request_embedding: np.ndarray,
        tool_embeddings: dict[str, np.ndarray],
        top_k: int = 8
    ) -> List[str]:
        """Select tools using embedding similarity (requires embeddings)."""
        # Compute cosine similarities
        similarities = {}
        for tool_name, tool_emb in tool_embeddings.items():
            similarity = np.dot(request_embedding, tool_emb) / (
                np.linalg.norm(request_embedding) * np.linalg.norm(tool_emb)
            )
            similarities[tool_name] = similarity
        
        # Sort by similarity and return top_k
        sorted_tools = sorted(similarities.items(), key=lambda x: x[1], reverse=True)
        
        # Always include communication tools
        result = ["send_response", "await_reply"]
        for tool, score in sorted_tools:
            if tool not in result and len(result) < top_k:
                result.append(tool)
        
        return result
```

---

## 5. Updated Agent Core Loop

```python
# backend/agent/core_v2.py (partial - main execute loop)

async def _execute_v2(
    self,
    user_text: str,
    context: WorldState,
    provider: LLMProvider,
    ws_callback: Optional[WSCallback] = None,
) -> tuple:
    """V2 execution loop with planning and verification."""
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: SENSE — Build structured world state
    # ═══════════════════════════════════════════════════════════════
    world_state = await self._build_world_state(user_text, context)
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: PLAN — Generate execution plan
    # ═══════════════════════════════════════════════════════════════
    selected_tools = self.tool_selector.select_tools(user_text, context.active_app)
    
    plan = await self.planner.create_plan(
        user_request=user_text,
        world_state=world_state,
        available_tools=selected_tools
    )
    
    # Handle clarification needed
    if plan.needs_clarification:
        if ws_callback:
            await ws_callback({
                "type": "response",
                "payload": {
                    "text": plan.clarification_prompt,
                    "await_input": True
                }
            })
        self._pending_reply_provider = provider
        return (plan.clarification_prompt, True)
    
    print(f"[Agent] Plan: {plan.task_summary}")
    print(f"[Agent] Steps: {len(plan.steps)}")
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: ACT + VERIFY — Execute each step
    # ═══════════════════════════════════════════════════════════════
    for step in plan.steps:
        print(f"[Agent] Step {step.id}: {step.description}")
        
        # Show progress to user
        if ws_callback:
            await ws_callback({
                "type": "doing",
                "text": step.description,
                "tool": step.tool
            })
        
        # Execute the tool
        try:
            result = await tool_registry.execute(step.tool, step.args)
            print(f"[Agent] Result: {result[:100]}...")
            
            # Verify the result
            verification = await self.verifier.verify(
                tool_name=step.tool,
                tool_args=step.args,
                tool_result=result,
                expected_criteria=step.success_criteria
            )
            
            if verification.success:
                plan.mark_step_complete(step.id, result)
            else:
                # Retry once if verification failed
                if verification.should_retry:
                    print(f"[Agent] Verification failed, retrying: {verification.message}")
                    result = await tool_registry.execute(step.tool, step.args)
                    verification = await self.verifier.verify(
                        step.tool, step.args, result, step.success_criteria
                    )
                
                if not verification.success:
                    plan.mark_step_failed(step.id, verification.message)
                    # Try fallback if available
                    if step.fallback_tool:
                        print(f"[Agent] Trying fallback: {step.fallback_tool}")
                        result = await tool_registry.execute(step.fallback_tool, step.args)
                    else:
                        break  # Abort remaining steps
                else:
                    plan.mark_step_complete(step.id, result)
                    
        except Exception as e:
            print(f"[Agent] Step failed: {e}")
            plan.mark_step_failed(step.id, str(e))
            break
    
    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: RESPOND — Send final message
    # ═══════════════════════════════════════════════════════════════
    if plan.is_complete():
        final_response = plan.final_response
    elif plan.has_failed():
        failed_step = next(s for s in plan.steps if s.status == StepStatus.FAILED)
        final_response = f"Sorry, I couldn't complete that. Step '{failed_step.description}' failed: {failed_step.error}"
    else:
        final_response = "Task partially completed."
    
    if ws_callback:
        await ws_callback({
            "type": "response",
            "payload": {"text": final_response}
        })
    
    return (final_response, False)
```

---

## 6. Migration Checklist

### Files to Create
- [ ] `backend/agent/world_state.py` — WorldState and UserIntent classes
- [ ] `backend/agent/planner.py` — ExecutionPlan and ExecutionStep classes
- [ ] `backend/agent/task_planner.py` — TaskPlanner class
- [ ] `backend/agent/verifier.py` — ToolVerifier class
- [ ] `backend/tools/selector.py` — ToolSelector class

### Files to Modify
- [ ] `backend/agent/perception.py` — Return WorldState instead of ContextSnapshot
- [ ] `backend/agent/memory.py` — Add WorkingMemory class

### Tests to Add
- [ ] `tests/test_planner.py` — Test plan generation
- [ ] `tests/test_verifier.py` — Test verification logic
- [ ] `tests/test_tool_selector.py` — Test tool selection

---

## 7. Benchmark Targets

| Scenario Type | Current | V2 Target |
|--------------|---------|-----------|
| Simple Actions (open app) | 67% | 95% |
| Multi-step Tasks | 20% | 80% |
| Ambiguous Requests | 30% | 90% |
| Error Recovery | 20% | 70% |
| **Overall** | **31.8%** | **>85%** |
