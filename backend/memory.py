"""
Moonwalk — Memory System
=========================
Short-term: conversation turns (in-memory, last N turns)
Long-term:  user preferences (persisted JSON)
Background: recurring tasks (persisted JSON)
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


# ── Storage directory ──
MOONWALK_DIR = os.path.expanduser("~/.moonwalk")


def _ensure_dir():
    os.makedirs(MOONWALK_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  Short-Term Memory (conversation history)
# ═══════════════════════════════════════════════════════════════

class ConversationMemory:
    """Keeps the last N conversation turns in memory."""

    def __init__(self, max_turns: int = 20, idle_timeout: float = 300.0):
        self._turns: list[dict] = []
        self._max_turns = max_turns
        self._idle_timeout = idle_timeout  # seconds before auto-clear
        self._last_activity: float = time.time()

    def add_user(self, text: str, context_summary: str = ""):
        """Add a user turn."""
        self._check_timeout()
        self._last_activity = time.time()
        content = text
        if context_summary:
            content = f"{text}\n\n{context_summary}"
        self._turns.append({"role": "user", "parts": [{"text": content}]})
        self._trim()

    def add_model(self, text: str):
        """Add a model response turn."""
        self._last_activity = time.time()
        self._turns.append({"role": "model", "parts": [{"text": text}]})
        self._trim()

    def add_function_call(self, name: str, args: dict):
        """Add a function call from the model."""
        self._last_activity = time.time()
        self._turns.append({
            "role": "model",
            "parts": [{"function_call": {"name": name, "args": args}}]
        })
        self._trim()

    def add_function_response(self, name: str, result: str):
        """Add a function response."""
        self._last_activity = time.time()
        self._turns.append({
            "role": "function",
            "parts": [{"function_response": {"name": name, "response": {"result": result}}}]
        })
        self._trim()

    def get_history(self) -> list[dict]:
        """Get conversation history for the LLM."""
        self._check_timeout()
        return list(self._turns)

    def clear(self):
        """Clear all conversation history."""
        self._turns.clear()

    def _trim(self):
        """Keep only the last N turns, but add a compression summary if we drop turns."""
        if len(self._turns) > self._max_turns:
            dropped_count = len(self._turns) - self._max_turns
            self._turns = self._turns[-self._max_turns:]
            
            # Inject a summary marker at the top to tell the model context was compressed
            # Only do this if the first message isn't already a summary
            if self._turns and self._turns[0].get("role") == "user":
                first_text = self._turns[0].get("parts", [{}])[0].get("text", "")
                if not first_text.startswith("[SYSTEM SUMMARY:"):
                    compressed_msg = f"[SYSTEM SUMMARY: {dropped_count} older turns were removed from context to save memory. Rely on your long-term memory for older details.]\n\n{first_text}"
                    self._turns[0]["parts"][0]["text"] = compressed_msg

    def _check_timeout(self):
        """Auto-clear if idle for too long."""
        if time.time() - self._last_activity > self._idle_timeout:
            self._turns.clear()


# ═══════════════════════════════════════════════════════════════
#  Long-Term Memory (user preferences)
# ═══════════════════════════════════════════════════════════════

class UserPreferences:
    """Persisted user preferences and learned behaviors."""

    def __init__(self):
        _ensure_dir()
        self._path = os.path.join(MOONWALK_DIR, "preferences.json")
        self._data: dict = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self._save()

    def get_all(self) -> dict:
        return dict(self._data)

    def to_prompt_string(self) -> str:
        """Format preferences for the LLM system prompt."""
        if not self._data:
            return ""
        lines = ["=== User Preferences ==="]
        for k, v in self._data.items():
            lines.append(f"- {k}: {v}")
        lines.append("=== End Preferences ===")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  Background Tasks (persistent recurring tasks)
# ═══════════════════════════════════════════════════════════════

@dataclass
class BackgroundTask:
    id: str
    description: str
    interval_seconds: float
    created_at: float
    last_run: float = 0.0
    active: bool = True


class TaskStore:
    """Persisted store of background/recurring tasks."""

    def __init__(self):
        _ensure_dir()
        self._path = os.path.join(MOONWALK_DIR, "tasks.json")
        self._tasks: dict[str, BackgroundTask] = self._load()

    def _load(self) -> dict[str, BackgroundTask]:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                return {
                    tid: BackgroundTask(**tdata)
                    for tid, tdata in data.items()
                }
            except Exception:
                return {}
        return {}

    def _save(self):
        data = {}
        for tid, task in self._tasks.items():
            data[tid] = {
                "id": task.id,
                "description": task.description,
                "interval_seconds": task.interval_seconds,
                "created_at": task.created_at,
                "last_run": task.last_run,
                "active": task.active,
            }
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def add(self, description: str, interval_seconds: float) -> BackgroundTask:
        """Add a new background task."""
        tid = f"task_{int(time.time())}"
        task = BackgroundTask(
            id=tid,
            description=description,
            interval_seconds=interval_seconds,
            created_at=time.time(),
        )
        self._tasks[tid] = task
        self._save()
        return task

    def get_due(self) -> list[BackgroundTask]:
        """Get tasks that are due to run."""
        now = time.time()
        due = []
        for task in self._tasks.values():
            if task.active and (now - task.last_run) >= task.interval_seconds:
                due.append(task)
        return due

    def mark_run(self, task_id: str):
        """Mark a task as just run."""
        if task_id in self._tasks:
            self._tasks[task_id].last_run = time.time()
            self._save()

    def remove(self, task_id: str):
        """Remove a background task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()

    def list_active(self) -> list[BackgroundTask]:
        return [t for t in self._tasks.values() if t.active]
