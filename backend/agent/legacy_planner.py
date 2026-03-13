"""
Legacy step-plan compatibility helpers.

These structures are no longer part of the active V2 runtime. They remain only
for compatibility tests, offline validation, and a shrinking set of migration
helpers inside TaskPlanner.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.planner import ExecutionStep, StepStatus


@dataclass
class ExecutionPlan:
    """Legacy step-based execution plan kept for compatibility only."""

    task_summary: str
    needs_clarification: bool = False
    clarification_prompt: str = ""
    steps: List[ExecutionStep] = field(default_factory=list)
    final_response: str = ""
    estimated_tools: List[str] = field(default_factory=list)
    skill_context: str = ""
    skills_used: List[str] = field(default_factory=list)

    current_step_index: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0

    confidence: float = 0.0
    source: str = "planner"

    def get_current_step(self) -> Optional[ExecutionStep]:
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                return step
        return None

    def get_step_by_id(self, step_id: int) -> Optional[ExecutionStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def mark_step_in_progress(self, step_id: int) -> None:
        step = self.get_step_by_id(step_id)
        if step:
            step.status = StepStatus.IN_PROGRESS

    def mark_step_complete(self, step_id: int, result: str) -> None:
        step = self.get_step_by_id(step_id)
        if step:
            step.status = StepStatus.COMPLETED
            step.result = result

    def mark_step_failed(self, step_id: int, error: str) -> None:
        step = self.get_step_by_id(step_id)
        if step:
            step.status = StepStatus.FAILED
            step.error = error

    def mark_step_retrying(self, step_id: int) -> None:
        step = self.get_step_by_id(step_id)
        if step:
            step.status = StepStatus.RETRYING
            step.retries += 1

    def skip_step(self, step_id: int, reason: str = "") -> None:
        step = self.get_step_by_id(step_id)
        if step:
            step.status = StepStatus.SKIPPED
            step.error = reason or "Skipped"

    def is_complete(self) -> bool:
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                return False
            if step.status == StepStatus.FAILED and not step.optional:
                return False
            if step.status == StepStatus.IN_PROGRESS:
                return False
        return True

    def has_failed(self) -> bool:
        return any(step.status == StepStatus.FAILED and not step.optional for step in self.steps)

    def get_failed_steps(self) -> List[ExecutionStep]:
        return [step for step in self.steps if step.status == StepStatus.FAILED]

    def get_completed_steps(self) -> List[ExecutionStep]:
        return [step for step in self.steps if step.status == StepStatus.COMPLETED]

    def progress_percentage(self) -> float:
        if not self.steps:
            return 100.0
        completed = len(
            [step for step in self.steps if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)]
        )
        return (completed / len(self.steps)) * 100

    def to_dict(self) -> dict:
        return {
            "task_summary": self.task_summary,
            "needs_clarification": self.needs_clarification,
            "clarification_prompt": self.clarification_prompt,
            "steps": [step.to_dict() for step in self.steps],
            "final_response": self.final_response,
            "skill_context": self.skill_context,
            "skills_used": list(self.skills_used),
            "progress": self.progress_percentage(),
            "is_complete": self.is_complete(),
            "has_failed": self.has_failed(),
        }

    def to_prompt_string(self) -> str:
        lines = [f"Plan: {self.task_summary}"]
        for step in self.steps:
            status_icon = {
                StepStatus.PENDING: "○",
                StepStatus.IN_PROGRESS: "●",
                StepStatus.COMPLETED: "✓",
                StepStatus.FAILED: "✗",
                StepStatus.SKIPPED: "−",
                StepStatus.RETRYING: "↻",
            }.get(step.status, "?")
            lines.append(f"  {status_icon} Step {step.id}: {step.description} [{step.tool}]")
        return "\n".join(lines)


class PlanBuilder:
    """Fluent builder for legacy execution plans."""

    def __init__(self, task_summary: str):
        self._plan = ExecutionPlan(task_summary=task_summary)
        self._step_counter = 0

    def add_step(
        self,
        description: str,
        tool: str,
        args: Dict[str, Any],
        success_criteria: str = "",
        fallback_tool: Optional[str] = None,
        fallback_args: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[int]] = None,
        optional: bool = False,
        wait_after: float = 0.0,
    ) -> "PlanBuilder":
        self._step_counter += 1
        step = ExecutionStep(
            id=self._step_counter,
            description=description,
            tool=tool,
            args=args,
            success_criteria=success_criteria,
            fallback_tool=fallback_tool,
            fallback_args=fallback_args,
            depends_on=depends_on or [],
            optional=optional,
            wait_after=wait_after,
        )
        self._plan.steps.append(step)
        return self

    def with_response(self, response: str) -> "PlanBuilder":
        self._plan.final_response = response
        return self

    def needs_clarification(self, prompt: str) -> "PlanBuilder":
        self._plan.needs_clarification = True
        self._plan.clarification_prompt = prompt
        return self

    def with_confidence(self, confidence: float) -> "PlanBuilder":
        self._plan.confidence = confidence
        return self

    def build(self) -> ExecutionPlan:
        self._plan.estimated_tools = list({step.tool for step in self._plan.steps})
        return self._plan


class PlanTemplates:
    """Pre-built legacy step-plan templates."""

    @staticmethod
    def open_app(app_name: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Open {app_name}")
            .add_step(
                description=f"Open {app_name} application",
                tool="open_app",
                args={"app_name": app_name},
                success_criteria=f"{app_name} becomes active app",
            )
            .with_response(f"Opening {app_name}!")
            .with_confidence(0.95)
            .build()
        )

    @staticmethod
    def open_url(url: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Open {url}")
            .add_step(
                description=f"Open {url} in browser",
                tool="open_url",
                args={"url": url},
                success_criteria="Browser opens to URL",
            )
            .with_response(f"Opening {url}!")
            .with_confidence(0.95)
            .build()
        )

    @staticmethod
    def search_web(query: str) -> ExecutionPlan:
        import urllib.parse

        search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        return (
            PlanBuilder(f"Search for: {query}")
            .add_step(
                description=f"Search Google for '{query}'",
                tool="open_url",
                args={"url": search_url},
                success_criteria="Search results page loads",
            )
            .with_response(f"Searching for '{query}'...")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def web_search(query: str) -> ExecutionPlan:
        return PlanTemplates.search_web(query)

    @staticmethod
    def play_media() -> ExecutionPlan:
        return (
            PlanBuilder("Play media")
            .add_step(
                description="Press play/pause key",
                tool="play_media",
                args={},
                success_criteria="Media starts playing",
            )
            .with_response("Playing!")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def find_files(directory: str, pattern: str) -> ExecutionPlan:
        command = f"find {directory} -name '{pattern}' -type f 2>/dev/null"
        return (
            PlanBuilder(f"Find {pattern} files in {directory}")
            .add_step(
                description=f"Search for {pattern} files in {directory}",
                tool="run_shell",
                args={"command": command},
                success_criteria="List of matching files returned",
            )
            .with_response(f"Found files matching {pattern}!")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def read_screen() -> ExecutionPlan:
        return (
            PlanBuilder("Analyze screen content")
            .add_step(
                description="Capture and analyze current screen",
                tool="read_screen",
                args={},
                success_criteria="Screen content is described",
            )
            .with_response("")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def click_ui_element(element_name: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Click {element_name}")
            .add_step(
                description=f"Click the {element_name} element",
                tool="click_ui",
                args={"description": element_name},
                success_criteria=f"{element_name} is clicked",
                fallback_tool="get_ui_tree",
                fallback_args={"search_term": element_name},
            )
            .with_response(f"Clicked {element_name}!")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def set_volume(level: int) -> ExecutionPlan:
        return (
            PlanBuilder(f"Set volume to {level}")
            .add_step(
                description=f"Set system volume to {level}%",
                tool="set_volume",
                args={"level": level},
                success_criteria=f"Volume set to {level}",
            )
            .with_response(f"Volume set to {level}%!")
            .with_confidence(0.95)
            .build()
        )

    @staticmethod
    def write_file(path: str, content: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Write to {path}")
            .add_step(
                description=f"Write content to {path}",
                tool="write_file",
                args={"path": path, "content": content},
                success_criteria=f"File written to {path}",
            )
            .with_response("File written!")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def read_and_modify_file(path: str, old_text: str, new_text: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Modify {path}")
            .add_step(
                description=f"Read {path}",
                tool="read_file",
                args={"path": path},
                success_criteria="File content retrieved",
            )
            .add_step(
                description="Write modified content",
                tool="write_file",
                args={
                    "path": path,
                    "content": "{step1_result}",
                    "_replace_old": old_text,
                    "_replace_new": new_text,
                },
                success_criteria="File updated",
            )
            .with_response("File updated!")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def read_file(path: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Read {path}")
            .add_step(
                description=f"Read contents of {path}",
                tool="read_file",
                args={"path": path},
                success_criteria="File contents retrieved",
            )
            .with_response("")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def run_shell_command(command: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Run: {command[:50]}...")
            .add_step(
                description="Execute shell command",
                tool="run_shell",
                args={"command": command},
                success_criteria="Command executed successfully",
            )
            .with_response("")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def fetch_and_save(url: str, save_path: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Download {url}")
            .add_step(
                description=f"Fetch content from {url}",
                tool="fetch_web_content",
                args={"url": url},
                success_criteria="Web content fetched",
            )
            .add_step(
                description=f"Save content to {save_path}",
                tool="write_file",
                args={"path": save_path, "content": "{step1_result}"},
                success_criteria="File saved",
            )
            .with_response(f"Downloaded to {save_path}!")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def create_and_run_script(path: str, content: str = "") -> ExecutionPlan:
        return (
            PlanBuilder(f"Create and run {path}")
            .add_step(
                description=f"Write script to {path}",
                tool="write_file",
                args={"path": path, "content": content},
                success_criteria="Script file created",
            )
            .add_step(
                description="Run the script",
                tool="run_shell",
                args={"command": f"python {path}"},
                success_criteria="Script executed",
            )
            .with_response("Script created and executed!")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def open_app_and_set_volume(app_name: str, volume: int) -> ExecutionPlan:
        return (
            PlanBuilder(f"Open {app_name} and set volume to {volume}")
            .add_step(
                description=f"Open {app_name}",
                tool="open_app",
                args={"app_name": app_name},
                success_criteria=f"{app_name} is open",
            )
            .add_step(
                description=f"Set volume to {volume}%",
                tool="set_volume",
                args={"level": volume},
                success_criteria=f"Volume set to {volume}",
            )
            .with_response(f"Opened {app_name} and set volume to {volume}%!")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def create_and_read_file(path: str, content: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Create and read {path}")
            .add_step(
                description=f"Create file at {path}",
                tool="write_file",
                args={"path": path, "content": content},
                success_criteria="File created",
            )
            .add_step(
                description="Read file back",
                tool="read_file",
                args={"path": path},
                success_criteria="File content retrieved",
            )
            .with_response(f"File created and contains '{content}'.")
            .with_confidence(0.90)
            .build()
        )

    @staticmethod
    def read_modify_write(path: str, field_name: str, new_value: str) -> ExecutionPlan:
        return (
            PlanBuilder(f"Modify {field_name} in {path}")
            .add_step(
                description=f"Read current {path}",
                tool="read_file",
                args={"path": path},
                success_criteria="File content retrieved",
            )
            .add_step(
                description=f"Update {field_name} to {new_value}",
                tool="write_file",
                args={"path": path, "content": f"{{updated with {field_name}={new_value}}}"},
                success_criteria=f"{field_name} updated to {new_value}",
            )
            .with_response(f"Updated {field_name} to {new_value}!")
            .with_confidence(0.85)
            .build()
        )

    @staticmethod
    def clarification_needed(prompt: str) -> ExecutionPlan:
        return PlanBuilder("Clarification needed").needs_clarification(prompt).build()
