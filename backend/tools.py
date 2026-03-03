"""
Moonwalk — Tool Registry
=========================
Decorated Python functions that the LLM can invoke by name.
Each tool auto-registers into the global registry and exports
its schema in Gemini function_declarations format.
"""

import asyncio
import subprocess
import webbrowser
import urllib.parse
import os
import tempfile
import time
import base64
import hashlib
from dataclasses import dataclass, field
from typing import Callable, Any, Optional


# Well-known services that are websites, not native macOS apps
KNOWN_URLS: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "google docs": "https://docs.google.com",
    "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "google sheets": "https://sheets.google.com",
    "google slides": "https://slides.google.com",
    "github": "https://github.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "reddit": "https://www.reddit.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "twitch": "https://www.twitch.tv",
    "tiktok": "https://www.tiktok.com",
    "whatsapp": "https://web.whatsapp.com",
    "chatgpt": "https://chat.openai.com",
    "notion": "https://www.notion.so",
    "figma": "https://www.figma.com",
    "canva": "https://www.canva.com",
    "wikipedia": "https://www.wikipedia.org",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
}


# ═══════════════════════════════════════════════════════════════
#  Tool Registry Infrastructure
# ═══════════════════════════════════════════════════════════════

@dataclass
class ToolDef:
    """Metadata for a registered tool."""
    name: str
    description: str
    parameters: dict          # JSON-schema style
    func: Callable            # The actual async function


class ToolRegistry:
    """Holds all available tools and serializes them for Gemini."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, name: str, description: str, parameters: dict):
        """Decorator to register a tool function."""
        def decorator(func: Callable):
            self._tools[name] = ToolDef(
                name=name,
                description=description,
                parameters=parameters,
                func=func,
            )
            return func
        return decorator

    async def execute(self, name: str, args: dict) -> str:
        """Execute a tool by name with given arguments. Returns result string."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"
        try:
            result = await tool.func(**args)
            return str(result)
        except Exception as e:
            return f"Error executing {name}: {e}"

    def declarations(self) -> list[dict]:
        """Export tools in Gemini function_declarations format."""
        decls = []
        for t in self._tools.values():
            decls.append({
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            })
        return decls

    def list_names(self) -> list[str]:
        return list(self._tools.keys())


# ── Global registry instance ──
registry = ToolRegistry()


# ═══════════════════════════════════════════════════════════════
#  Helper: run AppleScript
# ═══════════════════════════════════════════════════════════════

async def _osascript(script: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        return f"AppleScript error: {err}"
    return stdout.decode("utf-8", errors="replace").strip()


# ═══════════════════════════════════════════════════════════════
#  Tool Definitions
# ═══════════════════════════════════════════════════════════════

# ── 1. open_app ──
@registry.register(
    name="open_app",
    description="Launch or bring a macOS application to the foreground. Use the official app name (e.g. 'Google Chrome', 'Spotify', 'Finder').",
    parameters={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the application to open"
            }
        },
        "required": ["app_name"]
    }
)
async def open_app(app_name: str) -> str:
    # Check if it's actually a well-known website, not a native app
    url = KNOWN_URLS.get(app_name.lower())
    if url:
        webbrowser.open(url)
        return f"Opened {app_name} in browser"

    # Try launching as a macOS app
    proc = await asyncio.create_subprocess_exec(
        "open", "-a", app_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    
    if proc.returncode != 0:
        # Fallback: try opening as a URL (user might mean a website)
        guess_url = f"https://www.{app_name.lower().replace(' ', '')}.com"
        webbrowser.open(guess_url)
        return f"Couldn't find '{app_name}' as an app, opened {guess_url} instead"
    return f"Opened {app_name}"


# ── 2. close_window ──
@registry.register(
    name="close_window",
    description="Close the frontmost window of the currently active application.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
async def close_window() -> str:
    result = await _osascript(
        'tell application "System Events" to keystroke "w" using command down'
    )
    if "error" in result.lower():
        return f"Failed to close window: {result}"
    return "Closed the frontmost window"


# ── 3. quit_app ──
@registry.register(
    name="quit_app",
    description="Quit (fully close) a running macOS application.",
    parameters={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the application to quit"
            }
        },
        "required": ["app_name"]
    }
)
async def quit_app(app_name: str) -> str:
    result = await _osascript(f'tell application "{app_name}" to quit')
    if "error" in result.lower():
        return f"Failed to quit {app_name}: {result}"
    return f"Quit {app_name}"


# ── 4. play_media ──
@registry.register(
    name="play_media",
    description="Play a song, video, or media by searching YouTube. Great for music requests like 'play astronaut'.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for and play"
            }
        },
        "required": ["query"]
    }
)
async def play_media(query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={encoded}"
    webbrowser.open(url)
    return f"Opened YouTube search for '{query}'"


# ── 5. web_search ──
@registry.register(
    name="web_search",
    description="Search the web using the default browser. Useful for research, homework help, finding information.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            }
        },
        "required": ["query"]
    }
)
async def web_search(query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}"
    webbrowser.open(url)
    return f"Opened web search for '{query}'"


# ── 6. type_text ──
@registry.register(
    name="type_text",
    description="Type text into the currently focused input field or text area on the user's screen.",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to type"
            }
        },
        "required": ["text"]
    }
)
async def type_text(text: str) -> str:
    # Escape special characters for AppleScript
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    result = await _osascript(
        f'tell application "System Events" to keystroke "{escaped}"'
    )
    if "error" in result.lower():
        return f"Failed to type text: {result}"
    return f"Typed text into the active field"


# ── 7. run_shortcut ──
@registry.register(
    name="run_shortcut",
    description="Press a keyboard shortcut. Use modifier names: 'command', 'shift', 'option', 'control'. Example: 'command+c' for copy, 'command+v' for paste.",
    parameters={
        "type": "object",
        "properties": {
            "keys": {
                "type": "string",
                "description": "The shortcut to press, e.g. 'command+c', 'command+shift+n'"
            }
        },
        "required": ["keys"]
    }
)
async def run_shortcut(keys: str) -> str:
    parts = [p.strip().lower() for p in keys.split("+")]
    key_char = parts[-1]
    modifiers = parts[:-1]

    modifier_map = {
        "command": "command down",
        "cmd": "command down",
        "shift": "shift down",
        "option": "option down",
        "alt": "option down",
        "control": "control down",
        "ctrl": "control down",
    }

    using_parts = []
    for mod in modifiers:
        mapped = modifier_map.get(mod)
        if mapped:
            using_parts.append(mapped)

    using_clause = ""
    if using_parts:
        using_clause = " using {" + ", ".join(using_parts) + "}"

    script = f'tell application "System Events" to keystroke "{key_char}"{using_clause}'
    result = await _osascript(script)
    if "error" in result.lower():
        return f"Failed to run shortcut: {result}"
    return f"Pressed {keys}"


# ── 8. open_url ──
@registry.register(
    name="open_url",
    description="Open a specific URL in the default web browser.",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to open"
            }
        },
        "required": ["url"]
    }
)
async def open_url(url: str) -> str:
    webbrowser.open(url)
    return f"Opened {url}"


# ── 9. get_running_apps ──
@registry.register(
    name="get_running_apps",
    description="List all currently running applications on the Mac.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
async def get_running_apps() -> str:
    result = await _osascript(
        'tell application "System Events" to get name of every process whose background only is false'
    )
    return f"Running apps: {result}"


# ── 10. set_volume ──
@registry.register(
    name="set_volume",
    description="Set the system volume level (0-100).",
    parameters={
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "description": "Volume level from 0 (mute) to 100 (max)"
            }
        },
        "required": ["level"]
    }
)
async def set_volume(level: int) -> str:
    # macOS volume is 0-7 internally, scale from 0-100
    scaled = max(0, min(7, round(level * 7 / 100)))
    result = await _osascript(f"set volume output volume {level}")
    return f"Set volume to {level}%"


# ── 11. send_response (final answer to user) ──
@registry.register(
    name="send_response",
    description="Send a FINAL text response to the user. Use this when you have a complete answer and DON'T need further input. The conversation ends after this.",
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The final message to display to the user"
            }
        },
        "required": ["message"]
    }
)
async def send_response(message: str) -> str:
    return f"RESPONSE:{message}"


# ── 12. await_reply (ask user and wait for their response) ──
@registry.register(
    name="await_reply",
    description="Send a message to the user AND wait for their spoken response. Use this for interactive conversations: asking questions, joke setups (tell just the setup, wait for the user to guess), clarifying questions, or any time you need the user to respond before continuing.",
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to show while waiting for the user's reply"
            }
        },
        "required": ["message"]
    }
)
async def await_reply(message: str) -> str:
    return f"AWAIT:{message}"


# ── 12b. wait (pause execution briefly) ──
@registry.register(
    name="wait",
    description="Pause for a specified number of seconds. Use this when you need to wait for a dialog to appear after clicking a button, for an app to finish loading, or between UI interactions that need time to settle. E.g. after clicking 'Import', wait 1 second before pressing keyboard shortcuts.",
    parameters={
        "type": "object",
        "properties": {
            "seconds": {
                "type": "number",
                "description": "Number of seconds to wait (0.5 to 5, default 1)"
            }
        },
        "required": []
    }
)
async def wait(seconds: float = 1.0) -> str:
    seconds = max(0.2, min(5.0, seconds))
    await asyncio.sleep(seconds)
    
    # After waiting, fetch the minimal context to return to the model
    try:
        from perception import get_active_app, get_window_title
        app_name, window_title = await asyncio.gather(
            get_active_app(),
            get_window_title()
        )
        return f"Waited {seconds}s. Currently active app: '{app_name}', window: '{window_title}'"
    except Exception:
        return f"Waited {seconds}s"


# ── 13. run_shell (execute terminal commands) ──

# Safety blocklist — reject commands containing these patterns
SHELL_BLOCKLIST = [
    "rm -rf /", "rm -rf ~", "rm -rf /*",
    "sudo rm", "sudo shutdown", "sudo reboot", "sudo halt",
    "shutdown", "reboot", "halt",
    "mkfs", "dd if=", ":(){ :|:& };:",
    "mv / ", "chmod -R 777 /",
    "> /dev/sda", "fork bomb",
]

@registry.register(
    name="run_shell",
    description="Execute a shell command in the macOS terminal and return its output. Use for: checking disk space, listing files, installing packages (pip/brew), running scripts, git operations, system info, file management (mkdir, mv, cp), and any task achievable via command line. Output is truncated to 2000 chars.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute (e.g. 'ls -la ~/Desktop', 'df -h', 'python3 script.py')"
            }
        },
        "required": ["command"]
    }
)
async def run_shell(command: str) -> str:
    # Safety check
    cmd_lower = command.lower().strip()
    for blocked in SHELL_BLOCKLIST:
        if blocked in cmd_lower:
            return f"BLOCKED: Command contains dangerous pattern '{blocked}'. Refusing to execute."

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~"),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        
        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()
        
        result = ""
        if output:
            result += output[:2000]
        if errors:
            result += f"\n[STDERR]: {errors[:500]}"
        if not result:
            result = f"Command completed (exit code {proc.returncode})"
            
        return result
    except asyncio.TimeoutError:
        return "ERROR: Command timed out after 30 seconds"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


# ── 14. read_file ──
@registry.register(
    name="read_file",
    description="Read the contents of a file on the user's Mac. Supports any text file. Path supports ~ for home directory. Returns first 3000 characters.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file (e.g. '~/Desktop/notes.txt', '/Users/john/config.json')"
            }
        },
        "required": ["path"]
    }
)
async def read_file(path: str) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return f"ERROR: File not found: {expanded}"
    if os.path.isdir(expanded):
        return f"ERROR: '{expanded}' is a directory, not a file. Use run_shell('ls {path}') to list contents."
    try:
        with open(expanded, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(3000)
        size = os.path.getsize(expanded)
        truncated = " (truncated)" if size > 3000 else ""
        return f"[{os.path.basename(expanded)}, {size} bytes{truncated}]\n{content}"
    except Exception as e:
        return f"ERROR reading file: {str(e)[:200]}"


# ── 15. write_file ──
@registry.register(
    name="write_file",
    description="Create or overwrite a file with the given content. Parent directories are created automatically. Use for: creating scripts, notes, config files, code, or any text file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to write (e.g. '~/Desktop/hello.py', '~/notes.txt')"
            },
            "content": {
                "type": "string",
                "description": "The full text content to write to the file"
            }
        },
        "required": ["path", "content"]
    }
)
async def write_file(path: str, content: str) -> str:
    expanded = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(expanded), exist_ok=True)
        with open(expanded, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {expanded}"
    except Exception as e:
        return f"ERROR writing file: {str(e)[:200]}"


# ── 16. read_screen (Gemini Vision OCR) ──
# Screen cache: always capture fresh, but skip Vision API if pixels haven't changed
_screen_cache = {"hash": None, "result": None}

@registry.register(
    name="read_screen",
    description="Take a screenshot and analyze what's visible on screen using AI vision. Returns a description of the screen contents, including text, UI elements, buttons, and layout. Use when you need to understand what the user is looking at, read error messages, or identify clickable elements.",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Optional question about the screen, e.g. 'What error message is shown?' or 'What buttons are visible?'"
            }
        },
        "required": []
    }
)
async def read_screen(question: str = "") -> str:
    # Capture screenshot
    screenshot_dir = os.path.join(tempfile.gettempdir(), "moonwalk")
    os.makedirs(screenshot_dir, exist_ok=True)
    filepath = os.path.join(screenshot_dir, f"screen_{int(time.time())}.png")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "screencapture", "-x", "-t", "png", filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5.0)
        
        if not os.path.exists(filepath):
            return "ERROR: Failed to capture screenshot"
            
        # Get LOGICAL screen resolution (what CGEvent/click uses) — NOT Retina physical pixels
        # On Retina Macs, system_profiler returns 2560x1664 but CGEvent uses ~1470x956 logical points
        res_proc = await asyncio.create_subprocess_exec(
            "osascript", "-e",
            'tell application "Finder" to get bounds of window of desktop',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        res_stdout, _ = await res_proc.communicate()
        resolution_hint = ""
        if res_stdout:
            # Returns something like "0, 0, 1470, 956"
            bounds = res_stdout.decode("utf-8").strip()
            parts = [p.strip() for p in bounds.split(",")]
            if len(parts) == 4:
                w, h = parts[2], parts[3]
                resolution_hint = f" The screen logical resolution is {w} x {h} points (this is the coordinate system used for clicking)."

        # Read screenshot bytes
        with open(filepath, "rb") as f:
            img_bytes = f.read()
        
        # Fast hash comparison — did the screen actually change?
        img_hash = hashlib.md5(img_bytes).hexdigest()
        global _screen_cache
        if img_hash == _screen_cache["hash"] and _screen_cache["result"]:
            os.remove(filepath)
            return _screen_cache["result"]  # Screen unchanged, skip Vision API (~3s saved)

        img_data = base64.b64encode(img_bytes).decode("utf-8")
        
        # Clean up
        os.remove(filepath)
        
        # Use Gemini Vision to analyze
        from google import genai
        from google.genai import types
        
        client = genai.Client()
        prompt = question or "Describe what's on this screen. Include any visible text, buttons, UI elements, error messages, and the overall layout. Be concise but thorough."
        prompt += f"{resolution_hint} CRITICAL INSTRUCTION: When providing coordinates, output them as absolute (X, Y) values in the LOGICAL screen coordinate system. Do NOT use the image pixel dimensions or a normalized 1-1000 scale."
        if resolution_hint:
            prompt += f" Scale your coordinates to fit within the logical resolution mentioned above."
        
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_FAST_MODEL", "gemini-2.5-flash"),
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(
                            data=base64.b64decode(img_data),
                            mime_type="image/png"
                        )
                    ]
                )
            ]
        )
        
        # Parse response parts manually to avoid warnings about thought_signature
        output_text = ""
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.text:
                    output_text += part.text + "\n"
        
        result = output_text.strip()[:3000] if output_text else "Could not analyze screenshot"
        _screen_cache = {"hash": img_hash, "result": result}
        return result
        
    except Exception as e:
        return f"ERROR analyzing screen: {str(e)[:200]}"


# ── 17. click_element (GUI automation) ──
@registry.register(
    name="click_element",
    description="Click at specific screen coordinates (x, y). Use with read_screen to first identify where elements are, then click them. Coordinates are in pixels from top-left corner. Optionally double-click or right-click.",
    parameters={
        "type": "object",
        "properties": {
            "x": {
                "type": "integer",
                "description": "X coordinate (pixels from left edge)"
            },
            "y": {
                "type": "integer",
                "description": "Y coordinate (pixels from top edge)"
            },
            "click_type": {
                "type": "string",
                "description": "Type of click: 'single' (default), 'double', or 'right'",
                "enum": ["single", "double", "right"]
            }
        },
        "required": ["x", "y"]
    }
)
async def click_element(x: int, y: int, click_type: str = "single") -> str:
    try:
        # We run a small inline python script using Quartz for 100% reliable native clicks
        # This completely avoids the AppleScript "System Events" focus-stealing bugs.
        python_script = f"""
import time
import Quartz

x, y = {x}, {y}
click_type = "{click_type}"

def mouse_event(type, x, y, button=Quartz.kCGMouseButtonLeft):
    event = Quartz.CGEventCreateMouseEvent(None, type, (x, y), button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

if click_type == "right":
    mouse_event(Quartz.kCGEventRightMouseDown, x, y, Quartz.kCGMouseButtonRight)
    time.sleep(0.05)
    mouse_event(Quartz.kCGEventRightMouseUp, x, y, Quartz.kCGMouseButtonRight)
elif click_type == "double":
    mouse_event(Quartz.kCGEventLeftMouseDown, x, y)
    mouse_event(Quartz.kCGEventLeftMouseUp, x, y)
    time.sleep(0.05)
    # Important: Double click requires specifying it's the second click
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (x, y), Quartz.kCGMouseButtonLeft)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 2)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, (x, y), Quartz.kCGMouseButtonLeft)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 2)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
else:
    # Single click
    mouse_event(Quartz.kCGEventLeftMouseDown, x, y)
    time.sleep(0.05)
    mouse_event(Quartz.kCGEventLeftMouseUp, x, y)
"""
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", python_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            if "ModuleNotFoundError" in err and "Quartz" in err:
                return "ERROR: pyobjc-framework-Quartz is not installed. Run: pip install pyobjc-core pyobjc-framework-Quartz"
            return f"Click failed: {err[:200]}"
        
        return f"Clicked at ({x}, {y}) [{click_type}]"
    except Exception as e:
        return f"ERROR clicking: {str(e)[:200]}"


# ── 18. clipboard_ops ──
@registry.register(
    name="clipboard_ops",
    description="Interact with the macOS clipboard. Operations: 'get' reads current clipboard, 'set' writes text to clipboard, 'paste' triggers Cmd+V to paste at cursor. Use for copy-paste workflows between apps.",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "The clipboard operation: 'get', 'set', or 'paste'",
                "enum": ["get", "set", "paste"]
            },
            "text": {
                "type": "string",
                "description": "Text to copy to clipboard (required for 'set' action)"
            }
        },
        "required": ["action"]
    }
)
async def clipboard_ops(action: str, text: str = "") -> str:
    try:
        if action == "get":
            proc = await asyncio.create_subprocess_exec(
                "pbpaste",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            content = stdout.decode("utf-8", errors="replace").strip()
            if content:
                return f"Clipboard contents:\n{content[:2000]}"
            return "Clipboard is empty"
            
        elif action == "set":
            if not text:
                return "ERROR: 'text' parameter required for 'set' action"
            proc = await asyncio.create_subprocess_exec(
                "pbcopy",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(
                proc.communicate(input=text.encode("utf-8")), timeout=3.0
            )
            return f"Copied {len(text)} characters to clipboard"
            
        elif action == "paste":
            # Trigger Cmd+V
            script = '''
            tell application "System Events"
                keystroke "v" using command down
            end tell
            '''
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=3.0)
            return "Pasted clipboard contents"
        else:
            return f"ERROR: Unknown action '{action}'. Use 'get', 'set', or 'paste'."
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


# ── 19. get_ui_tree (Accessibility UI Dump) ──
@registry.register(
    name="get_ui_tree",
    description="Dump the macOS Accessibility UI element tree for a window. This tells you EXACTLY what buttons, text fields, checkboxes, and menus exist on screen, along with their exact (x, y) coordinates. Highly recommended to use this before clicking to avoid guessing coordinates. Requires Accessibility permissions.",
    parameters={
        "type": "object", 
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Optional application name to target (e.g., 'CapCut'). If empty, uses the frontmost app."
            },
            "search_term": {
                "type": "string",
                "description": "Optional text to search for within the UI. If provided, only elements matching this term (case-insensitive) or their parents are returned. This prevents truncation when looking for specific buttons."
            }
        }
    }
)
async def get_ui_tree(app_name: str = "", search_term: str = "") -> str:
    # Build the AppleScript to get the target process
    target_block = 'set targetApp to first process whose frontmost is true'
    if app_name:
        target_block = f'set targetApp to process "{app_name}"'

    search_filter = ""
    if search_term:
        search_filter = f'set searchTerm to "{search_term.lower()}"\n'
    else:
        search_filter = 'set searchTerm to ""\n'

    script = f'''
    on run
        try
            tell application "System Events"
                {target_block}
                set targetWindow to front window of targetApp
                {search_filter}
                -- Helper to recursively dump UI elements
                return my dumpUI(targetWindow, "", searchTerm)
            end tell
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end run
    
    on dumpUI(uiElem, indent, searchTerm)
        set theResult to ""
        try
            tell application "System Events"
                set eClass to class of uiElem as string
                set eRole to role of uiElem as string
                
                set eName to ""
                try
                    set eName to name of uiElem
                end try
                if eName is missing value then set eName to "unnamed"
                
                set ePos to {{0, 0}}
                try
                    set ePos to position of uiElem
                end try
                
                set eSize to {{0, 0}}
                try
                    set eSize to size of uiElem
                end try
                
                set eLine to "- [" & eRole & "] \\"" & eName & "\\" at " & (item 1 of ePos as string) & "," & (item 2 of ePos as string) & " (size: " & (item 1 of eSize as string) & "x" & (item 2 of eSize as string) & ")" & "\\n"
                
                set uiChildren to UI elements of uiElem
                set childResults to ""
                set hasMatchingChild to false
                
                repeat with childElem in uiChildren
                    set childOut to my dumpUI(childElem, indent & "  ", searchTerm)
                    if childOut is not "" then
                        set childResults to childResults & childOut
                        set hasMatchingChild to true
                    end if
                end repeat
                
                -- Filter logic: if search term is empty, always include.
                -- Otherwise, include if the element's name matches, or if any child matched.
                if searchTerm is "" then
                    set theResult to indent & eLine & childResults
                else
                    ignoring case
                        set isMatch to (eName contains searchTerm) or (eRole contains searchTerm)
                    end ignoring
                    if isMatch or hasMatchingChild then
                        set theResult to indent & eLine & childResults
                    end if
                end if
            end tell
        end try
        return theResult
    end dumpUI
    '''
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        out = stdout.decode("utf-8").strip()
        err = stderr.decode("utf-8").strip()
        
        if proc.returncode != 0:
            if "not allowed" in err.lower() or "accessibility" in err.lower():
                return "ERROR: Accessibility permission required (System Settings → Privacy & Security → Accessibility)."
            return f"ERROR dumping UI tree: {err[:200]}"
            
        return out[:10000] if out else ("No elements found matching search." if search_term else "Window has no accessible UI elements.")
    except asyncio.TimeoutError:
        return "ERROR: Timed out getting UI tree"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


# ── 20. press_key ──
@registry.register(
    name="press_key",
    description="Press a raw system key (like Tab, Enter, Escape, Arrows). Extremely useful for navigating forms, menus, and UIs without using the mouse or coordinates. Supported: 'return', 'tab', 'space', 'escape', 'up', 'down', 'left', 'right', 'delete'.",
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The key to press",
                "enum": ["return", "tab", "space", "escape", "up", "down", "left", "right", "delete"]
            },
            "times": {
                "type": "integer",
                "description": "Number of times to press the key (default: 1, max: 20)"
            }
        },
        "required": ["key"]
    }
)
async def press_key(key: str, times: int = 1) -> str:
    times = max(1, min(20, times))
    valid_keys = {
        "return": "return", "tab": "tab", "space": "space", "escape": "escape",
        "up": "126", "down": "125", "left": "123", "right": "124", "delete": "51"
    }
    
    if key not in valid_keys:
        return f"ERROR: Unsupported key '{key}'. Use tab, return, escape, etc."
        
    kcode = valid_keys[key]
    
    # Arrows and delete require key code, others use keystroke
    if key in ["up", "down", "left", "right", "delete"]:
        action = f"key code {kcode}"
    else:
        action = f"keystroke {kcode}"
        
    script = f'''
    tell application "System Events"
        repeat {times} times
            {action}
            delay 0.05
        end repeat
    end tell
    '''
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        if proc.returncode != 0:
            return f"ERROR pressing key: {stderr.decode('utf-8')[:200]}"
        return f"Pressed '{key}' {times} time(s)"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


# ── 21. mouse_action ──
@registry.register(
    name="mouse_action",
    description="Perform advanced mouse actions: 'move' (hover over coordinates without clicking), 'scroll' (scroll up/down), or 'drag' (click and drag from x1,y1 to x2,y2).",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action type: 'move', 'scroll', or 'drag'",
                "enum": ["move", "scroll", "drag"]
            },
            "x": {"type": "integer", "description": "X coordinate (for move/drag start)"},
            "y": {"type": "integer", "description": "Y coordinate (for move/drag start)"},
            "x2": {"type": "integer", "description": "Destination X (for drag only)"},
            "y2": {"type": "integer", "description": "Destination Y (for drag only)"},
            "lines": {"type": "integer", "description": "Lines to scroll (positive=up, negative=down)"}
        },
        "required": ["action"]
    }
)
async def mouse_action(action: str, x: int = 0, y: int = 0, x2: int = 0, y2: int = 0, lines: int = 5) -> str:
    try:
        if action == "move":
            # Just verify python can run a quick python script using Quartz
            # Moving purely via AppleScript is hard, so we use Python's Quartz module which is built-in on macOS
            py_script = f"""
import Quartz
moveEvent = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, ({x}, {y}), Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, moveEvent)
            """
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", py_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=2.0)
            return f"Moved mouse to ({x}, {y})"
            
        elif action == "scroll":
            # AppleScript can scroll simply
            script = f'''
            tell application "System Events"
                scroll {"up" if lines > 0 else "down"} {abs(lines)}
            end tell
            '''
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=2.0)
            return f"Scrolled {'up' if lines > 0 else 'down'} {abs(lines)} lines"
            
        elif action == "drag":
            # Drag via python Quartz
            py_script = f"""
import Quartz
import time

start_pos = ({x}, {y})
end_pos = ({x2}, {y2})

# Move to start
moveEvent = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, start_pos, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, moveEvent)
time.sleep(0.1)

# Mouse down
downEvent = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, start_pos, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, downEvent)
time.sleep(0.1)

# Mouse drag
dragEvent = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, end_pos, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, dragEvent)
time.sleep(0.1)

# Mouse up
upEvent = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, end_pos, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, upEvent)
            """
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", py_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=3.0)
            return f"Dragged from ({x}, {y}) to ({x2}, {y2})"
            
        return f"Unknown action '{action}'"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


# ── 22. window_manager ──
@registry.register(
    name="window_manager",
    description="Manage the active window's position and size. Actions: 'get' (returns current x, y, width, height), 'move' (moves to x, y preserving size), 'resize' (sets width and height preserving position), 'layout' (sets standard layouts like 'left_half', 'right_half', 'fullscreen').",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action type: 'get', 'move', 'resize', or 'layout'",
                "enum": ["get", "move", "resize", "layout"]
            },
            "x": {"type": "integer", "description": "X coordinate for 'move'"},
            "y": {"type": "integer", "description": "Y coordinate for 'move'"},
            "width": {"type": "integer", "description": "Width for 'resize'"},
            "height": {"type": "integer", "description": "Height for 'resize'"},
            "layout_type": {
                "type": "string",
                "description": "Preset layout for 'layout' action",
                "enum": ["left_half", "right_half", "fullscreen", "center"]
            }
        },
        "required": ["action"]
    }
)
async def window_manager(action: str, x: int = 0, y: int = 0, width: int = 800, height: int = 600, layout_type: str = "") -> str:
    try:
        if action == "get":
            script = '''
            tell application "System Events"
                set frontApp to first process whose frontmost is true
                set fw to front window of frontApp
                set p to position of fw
                set s to size of fw
                return (item 1 of p as string) & "," & (item 2 of p as string) & "," & (item 1 of s as string) & "," & (item 2 of s as string)
            end tell
            '''
            proc = await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            res = stdout.decode("utf-8").strip()
            if not res: return "Failed to get window bounds"
            p = res.split(',')
            return f"Active window is at x={p[0]}, y={p[1]} with size {p[2]}x{p[3]}"
            
        elif action == "move":
            script = f'''
            tell application "System Events"
                set position of front window of (first process whose frontmost is true) to {{{x}, {y}}}
            end tell
            '''
            await asyncio.create_subprocess_exec("osascript", "-e", script)
            return f"Moved window to ({x}, {y})"
            
        elif action == "resize":
            script = f'''
            tell application "System Events"
                set size of front window of (first process whose frontmost is true) to {{{width}, {height}}}
            end tell
            '''
            await asyncio.create_subprocess_exec("osascript", "-e", script)
            return f"Resized window to {width}x{height}"
            
        elif action == "layout":
            # We need screen bounds for this. We can pull it from system_profiler or just use AppleScript finder bounds
            bounds_script = 'tell application "Finder" to get bounds of window of desktop'
            proc = await asyncio.create_subprocess_exec("osascript", "-e", bounds_script, stdout=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            bounds = stdout.decode("utf-8").strip().replace(' ', '').split(',')  # x1,y1,x2,y2
            if len(bounds) != 4: return "Error getting screen bounds"
            
            w_total = int(bounds[2])
            h_total = int(bounds[3])
            
            if layout_type == "fullscreen":
                lx, ly, lw, lh = 0, 25, w_total, h_total - 25 # roughly account for menu bar
            elif layout_type == "left_half":
                lx, ly, lw, lh = 0, 25, w_total // 2, h_total - 25
            elif layout_type == "right_half":
                lx, ly, lw, lh = w_total // 2, 25, w_total // 2, h_total - 25
            elif layout_type == "center":
                lw, lh = int(w_total * 0.7), int(h_total * 0.8)
                lx, ly = (w_total - lw) // 2, (h_total - lh) // 2 + 10
            else:
                return f"Unknown layout {layout_type}"
                
            script = f'''
            tell application "System Events"
                set fw to front window of (first process whose frontmost is true)
                set position of fw to {{{lx}, {ly}}}
                set size of fw to {{{lw}, {lh}}}
            end tell
            '''
            await asyncio.create_subprocess_exec("osascript", "-e", script)
            return f"Applied layout '{layout_type}' (pos: {lx},{ly} size: {lw}x{lh})"
            
        return f"Unknown action: {action}"
    except Exception as e:
        return f"ERROR managing window: {str(e)[:100]}"


# ── 23. list_directory ──
@registry.register(
    name="list_directory",
    description="List the contents of a directory. Returns a JSON-like tree of files and folders (up to 300 entries). Use this instead of 'ls' shell commands for safe, structured exploration.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the directory (e.g. '~/Desktop', './src')"}
        },
        "required": ["path"]
    }
)
async def list_directory(path: str) -> str:
    try:
        full_path = os.path.expanduser(path)
        if not os.path.isdir(full_path):
            return f"ERROR: '{full_path}' is not a valid directory."
        
        items: list[dict | str] = []
        for i, entry in enumerate(os.scandir(full_path)):
            if i > 300:
                items.append("... [truncated, too many files]")
                break
            items.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size_bytes": entry.stat().st_size if not entry.is_dir() else 0
            })
            
        # Format cleanly
        out = f"Directory contents of '{full_path}':\n"
        for item in items:
            if isinstance(item, str):
                out += item + "\n"
            else:
                icon = "📁" if item["is_dir"] else "📄"
                size = f"({item['size_bytes']} bytes)" if not item["is_dir"] else ""
                out += f"{icon} {item['name']} {size}\n"
        return out
    except Exception as e:
        return f"ERROR reading directory: {str(e)}"


# ── 24. replace_in_file ──
@registry.register(
    name="replace_in_file",
    description="Surgically replace a specific block of text in a file. Must provide the EXACT old text including indentation. Far safer and faster than rewriting the entire file with write_file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"},
            "old_text": {"type": "string", "description": "The exact existing text to find and replace"},
            "new_text": {"type": "string", "description": "The new text to insert in its place"}
        },
        "required": ["path", "old_text", "new_text"]
    }
)
async def replace_in_file(path: str, old_text: str, new_text: str) -> str:
    try:
        full_path = os.path.expanduser(path)
        if not os.path.isfile(full_path):
            return f"ERROR: File not found at '{full_path}'"
            
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if old_text not in content:
            return "ERROR: `old_text` not found in the file. Ensure indentation and line breaks match exactly."
            
        occurrences = content.count(old_text)
        new_content = content.replace(old_text, new_text)
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        return f"Successfully replaced {occurrences} occurrence(s) in {full_path}"
    except Exception as e:
        return f"ERROR modifying file: {str(e)}"


# ── 25. fetch_web_content ──
@registry.register(
    name="fetch_web_content",
    description="Fetch a URL and extract its text/markdown content directly. Bypasses the GUI browser for clean, hallucination-free reading of documentation, articles, or APIs.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full HTTP/HTTPS URL"}
        },
        "required": ["url"]
    }
)
async def fetch_web_content(url: str) -> str:
    try:
        import httpx
        import re
        
        if not url.startswith("http"):
            url = "https://" + url
            
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Mask as a standard browser to avoid basic blocks
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            resp = await client.get(url, headers=headers)
            
        if resp.status_code != 200:
            return f"ERROR: Server returned status {resp.status_code}"
            
        html = resp.text
        
        # Super basic regex tag stripper since BeautifulSoup might not be installed
        # Remove scripts and styles first
        html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        if len(text) > 8000:
            return text[:8000] + "\n...[truncated due to length]"
        return text
    except Exception as e:
        return f"ERROR fetching URL: {str(e)}"


# ── 26. run_python ──
@registry.register(
    name="run_python",
    description="Execute sandboxed Python code and return the stdout/stderr. Perfect for math, data analysis, or scripting. Variables do not persist between calls unless explicitly saved to a file.",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The raw Python code to execute (e.g. `import math; print(math.pi)`)"}
        },
        "required": ["code"]
    }
)
async def run_python(code: str) -> str:
    try:
        # Create a temporary file to hold the script
        fd, temp_path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, 'w') as f:
            f.write(code)
            
        proc = await asyncio.create_subprocess_exec(
            "python3", temp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        
        # Clean up temp file
        os.remove(temp_path)
        
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        
        res = ""
        if out: res += f"[STDOUT]\n{out}\n"
        if err: res += f"[STDERR]\n{err}\n"
        
        if not res:
            res = f"Script executed successfully with exit code {proc.returncode} (No output)"
            
        return res[:4000]
    except Exception as e:
        return f"ERROR executing python: {str(e)}"


# ── 26B. think (Reasoning Scratchpad) ──
@registry.register(
    name="think",
    description="Extended thinking and planning scratchpad. Use this tool BEFORE taking action to break down complex tasks, reason about the current state, and plan your next steps.",
    parameters={
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "Your detailed chain of thought, step-by-step plan, or hypotheses."}
        },
        "required": ["reasoning"]
    }
)
async def think(reasoning: str = "") -> str:
    """A no-op tool that simply allows the LLM to output long chains of thought."""
    return f"Thought recorded: {len(reasoning)} chars."


# ═══════════════════════════════════════════════════════════════
#  Sub-Agent Management Tools (Cloud-only)
# ═══════════════════════════════════════════════════════════════

# This gets injected by cloud_server.py at runtime
_sub_agent_manager = None  # type: ignore
_sub_agent_ws_callback = None  # type: ignore


def set_sub_agent_manager(manager, ws_callback=None):
    """Called by cloud_server.py to inject the SubAgentManager instance."""
    global _sub_agent_manager, _sub_agent_ws_callback
    _sub_agent_manager = manager
    _sub_agent_ws_callback = ws_callback


# ── 27. spawn_agent ──
@registry.register(
    name="spawn_agent",
    description="Create a background cloud agent to work on a task autonomously. "
                "CRITICAL INSTRUCTION: DO NOT CALL THIS TOOL IMMEDIATELY WHEN A USER ASKS TO CREATE AN AGENT. "
                "Always use await_reply FIRST to gather specific requirements, output schemas, and target URLs. "
                "The agent runs on Google Cloud. Set intrusive=True ONLY if the agent requires "
                "access to the Mac screen, mouse, or local apps (like Email or Spotify). "
                "If intrusive=False, it runs silently with only cloud tools (file I/O, python, shell, scraping). "
                "Returns the agent ID.",
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed description of what the background agent should do. If spawning a parallel agent inside another agent, prefix the task with '[Parallel]'."
            },
            "intrusive": {
                "type": "boolean",
                "description": "If True, gives the agent full access to the Mac. Defaults to False."
            },
            "system_prompt": {
                "type": "string",
                "description": "Optional: Override the agent's base persona. Explain exactly what its job is (e.g. 'You are a Senior Frontend React Developer')."
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: A strict whitelist of tool names this agent is allowed to use. Use this to sandbox worker agents."
            },
            "deliverable_format": {
                "type": "string",
                "description": "Optional: Strict instructions on how the final sub_agent_complete output must be formatted (e.g. 'A JSON object matching Schema X')."
            }
        },
        "required": ["task"]
    }
)
async def spawn_agent(task: str = "", intrusive: bool = False,
                      system_prompt: Optional[str] = None,
                      allowed_tools: Optional[list[str]] = None,
                      deliverable_format: Optional[str] = None) -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available. This feature requires the cloud server."
    return await _sub_agent_manager.spawn(
        task, 
        intrusive=intrusive, 
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        deliverable_format=deliverable_format,
        ws_callback=_sub_agent_ws_callback
    )


# ── 28. list_agents ──
@registry.register(
    name="list_agents",
    description="List all background cloud agents and their current status (running, completed, error, stopped).",
    parameters={
        "type": "object",
        "properties": {}
    }
)
async def list_agents() -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available. This feature requires the cloud server."
    return _sub_agent_manager.list_agents()


# ── 29. open_dashboard (Agent Manager UI) ──
@registry.register(
    name="open_dashboard",
    description="Open the native Moonwalk Agent Dashboard window which shows all running and completed background agents. Use this when the user asks to see their agents, manage agents, or open the dashboard.",
    parameters={
        "type": "object",
        "properties": {}
    }
)
async def open_dashboard() -> str:
    """Special tool that returns an IPC payload to the Mac Client to open the dashboard."""
    # We return a specially formatted JSON string that the mac_client tool executor logic 
    # intercepts to trigger the 'open-dashboard' IPC event.
    return json.dumps({"_IPC_COMMAND_": "open-dashboard"})


# ── 29. get_agent_output ──
@registry.register(
    name="get_agent_output",
    description="Get the full output logs and result of a background agent. "
                "Shows the agent's progress logs, final result, and any errors.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The ID of the background agent (returned by spawn_agent)"
            }
        },
        "required": ["agent_id"]
    }
)
async def get_agent_output(agent_id: str = "") -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available. This feature requires the cloud server."
    return _sub_agent_manager.get_output(agent_id)


# ── 30. stop_agent ──
@registry.register(
    name="stop_agent",
    description="Stop a running background cloud agent by its ID.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The ID of the background agent to stop"
            }
        },
        "required": ["agent_id"]
    }
)
async def stop_agent(agent_id: str = "") -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available. This feature requires the cloud server."
    return _sub_agent_manager.stop(agent_id)

# ── 31. delegate_to_subagent ──
@registry.register(
    name="delegate_to_subagent",
    description="Spawn a new worker agent to handle a specific sub-task. "
                "Use this to delegate work down the hierarchy. Returns the new worker's Agent ID.",
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed description of the task for the worker."
            },
            "system_prompt": {
                "type": "string",
                "description": "Optional: Override the worker's base persona. Explain its exact role."
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: A strict whitelist of tool names this worker is allowed to use."
            },
            "deliverable_format": {
                "type": "string",
                "description": "Optional: Strict instructions on how the final output must be formatted."
            }
        },
        "required": ["task"]
    }
)
async def delegate_to_subagent(task: str = "", system_prompt: Optional[str] = None,
                               allowed_tools: Optional[list[str]] = None,
                               deliverable_format: Optional[str] = None) -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available. This feature requires the cloud server."
    return await _sub_agent_manager.spawn(
        task, 
        intrusive=False, 
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        deliverable_format=deliverable_format,
        ws_callback=_sub_agent_ws_callback
    )

# ── 32. wait_for_agent ──
@registry.register(
    name="wait_for_agent",
    description="Wait for a specific background agent to complete its task. "
                "This will pause your execution until the target agent is done, returning its final result.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The ID of the agent to wait for."
            }
        },
        "required": ["agent_id"]
    }
)
async def wait_for_agent(agent_id: str = "") -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available."
    import asyncio
    
    # Poll instead of blocking to prevent deadlocks
    while True:
        state = _sub_agent_manager.agents.get(agent_id)
        if not state:
            return f"ERROR: Agent '{agent_id}' not found."
            
        status = state.get("status")
        if status == "completed":
            return f"Agent {agent_id} COMPLETED. Result:\n{state.get('result')}"
        elif status == "error":
            return f"Agent {agent_id} FAILED. Error:\n{state.get('error')}"
        elif status in ["stopped", "stopping"]:
            return f"Agent {agent_id} was STOPPED."
            
        await asyncio.sleep(2)
        
    return "Unknown error."

# ── 33. message_agent ──
@registry.register(
    name="message_agent",
    description="Send a message (like feedback or new data) to another running agent.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The ID of the agent to message."
            },
            "message": {
                "type": "string",
                "description": "The message content."
            }
        },
        "required": ["agent_id", "message"]
    }
)
async def message_agent(agent_id: str = "", message: str = "") -> str:
    if not _sub_agent_manager:
        return "ERROR: Sub-agent manager not available."
    
    state = _sub_agent_manager.agents.get(agent_id)
    if not state:
        return f"ERROR: Agent '{agent_id}' not found."
    
    if "messages" not in state:
        state["messages"] = []  # type: ignore
    
    state["messages"].append(message)  # type: ignore
    _sub_agent_manager._save_state()
    return f"Message sent to {agent_id}."
