"""
Legacy task-planner compatibility helpers.

These helpers exist for test-only step-plan validation and a very small amount
of offline compatibility. They are not part of the active milestone runtime.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from agent.legacy_planner import ExecutionPlan, PlanTemplates
from agent.planner import ExecutionStep
from agent.world_state import IntentAction, TargetType, TaskGraph, UserIntent, WorldState


class LegacyTaskPlannerCompatMixin:
    """Compatibility surface for legacy step-plan validation."""

    def _try_template(self, intent: UserIntent, world_state: WorldState) -> Optional[ExecutionPlan]:
        """
        Minimal legacy template hook kept only for compatibility tests.

        The active runtime no longer short-circuits through step templates.
        """
        if self._is_research_document_request(intent.raw_text, ""):
            return None

        if intent.action == IntentAction.OPEN and intent.target_type == TargetType.APP and intent.target_value:
            return PlanTemplates.open_app(intent.target_value)

        if intent.action in {IntentAction.OPEN, IntentAction.NAVIGATE} and intent.target_type == TargetType.URL:
            url = intent.target_value or ""
            if url and not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            return PlanTemplates.open_url(url)

        if intent.action == IntentAction.SEARCH and intent.target_type not in {
            TargetType.FILE,
            TargetType.FOLDER,
            TargetType.AGENT,
        }:
            query = intent.parameters.get("query", intent.target_value)
            if query:
                return PlanTemplates.search_web(query)

        return None

    def _get_tool_contracts(self) -> dict:
        if self._tool_contracts_cache is not None:
            return self._tool_contracts_cache
        if not self.tool_registry:
            self._tool_contracts_cache = {}
            return self._tool_contracts_cache

        contracts: dict = {}
        for decl in self.tool_registry.declarations():
            params = decl.get("parameters", {}) or {}
            properties = (params.get("properties", {}) or {}) if isinstance(params, dict) else {}
            required = set(params.get("required", []) or []) if isinstance(params, dict) else set()
            required.discard("reasoning")
            contracts[decl["name"]] = {
                "allowed": set(properties.keys()),
                "required": required,
            }

        self._tool_contracts_cache = contracts
        return contracts

    def _normalize_step_args(self, step: ExecutionStep) -> None:
        if not isinstance(step.args, dict):
            step.args = {}
            return

        if step.tool == "send_response":
            if "message" not in step.args and "response_text" in step.args:
                step.args["message"] = step.args.pop("response_text")
        elif step.tool == "await_reply":
            if "message" not in step.args:
                if "prompt" in step.args:
                    step.args["message"] = step.args.pop("prompt")
                elif "question" in step.args:
                    step.args["message"] = step.args.pop("question")
        elif step.tool == "replace_in_file":
            step.args.pop("global", None)

    def _renumber_steps(self, plan: ExecutionPlan) -> None:
        for idx, step in enumerate(plan.steps, start=1):
            step.id = idx

    def _derive_research_query(self, user_request: str, plan: ExecutionPlan) -> str:
        for step in plan.steps:
            if step.tool == "web_search":
                query = str(step.args.get("query", "")).strip()
                if query:
                    return query[:180]

        text = (user_request or "").strip()
        if not text:
            return (plan.task_summary or "research topic").strip()[:180]

        text = re.sub(r"^[\s,]*(can you|could you|please|hey moonwalk|moonwalk)\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^[\s,]*(research|investigate|study|analy[sz]e|compare|look up)\s+", "", text, flags=re.IGNORECASE)
        text = text.strip(" ?.!,:;")
        return (text or plan.task_summary or "research topic")[:180]

    def _repair_plan_structure(self, plan: ExecutionPlan, user_request: str = "") -> list[str]:
        repairs: list[str] = []
        if not plan.steps:
            return repairs

        browser_ref_tools = {"browser_click_ref", "browser_type_ref", "browser_select_ref"}
        baseline_tools = {"browser_snapshot", "browser_read_page", "browser_refresh_refs"}

        first_ref_idx = next(
            (idx for idx, step in enumerate(plan.steps) if step.tool in browser_ref_tools),
            None,
        )
        if first_ref_idx is not None:
            has_baseline = any(step.tool in baseline_tools for step in plan.steps[:first_ref_idx])
            if not has_baseline:
                plan.steps.insert(
                    first_ref_idx,
                    ExecutionStep(
                        id=0,
                        description="Capture browser snapshot baseline",
                        tool="browser_snapshot",
                        args={},
                        success_criteria="Fresh browser refs available",
                        wait_after=0,
                        optional=False,
                    ),
                )
                repairs.append("inserted browser_snapshot baseline")

        seen_reads_by_path: set[str] = set()
        idx = 0
        while idx < len(plan.steps):
            step = plan.steps[idx]
            path = str(step.args.get("path", "")).strip() if isinstance(step.args, dict) else ""

            if step.tool == "read_file" and path:
                seen_reads_by_path.add(path)
            elif step.tool == "replace_in_file" and path and path not in seen_reads_by_path:
                plan.steps.insert(
                    idx,
                    ExecutionStep(
                        id=0,
                        description=f"Read current contents of {path}",
                        tool="read_file",
                        args={"path": path},
                        success_criteria="Current file content loaded",
                        wait_after=0,
                        optional=False,
                    ),
                )
                seen_reads_by_path.add(path)
                repairs.append(f"inserted read_file before replace_in_file for {path}")
                idx += 1

            idx += 1

        if self._is_general_research_request(user_request, plan.task_summary):
            content_read_tools = {
                "browser_read_page",
                "browser_read_text",
                "read_page_content",
                "extract_structured_data",
                "get_page_summary",
                "fetch_web_content",
                "web_scrape",
                "gworkspace_analyze",
                "gdocs_read",
                "read_file",
            }
            content_reads = [step for step in plan.steps if step.tool in content_read_tools]
            if len(content_reads) < 2:
                query = self._derive_research_query(user_request, plan)
                insert_idx = next(
                    (i + 1 for i, step in enumerate(plan.steps) if step.tool in {"web_search", "open_url"}),
                    len(plan.steps),
                )
                if not content_reads:
                    plan.steps.insert(
                        insert_idx,
                        ExecutionStep(
                            id=0,
                            description="Read page content for initial research findings",
                            tool="browser_read_page",
                            args={"query": query},
                            success_criteria="Initial research content extracted",
                            wait_after=0,
                            optional=False,
                        ),
                    )
                    plan.steps.insert(
                        insert_idx + 1,
                        ExecutionStep(
                            id=0,
                            description="Scroll down for more research content",
                            tool="browser_scroll",
                            args={"direction": "down", "amount": "page"},
                            success_criteria="Scrolled for more content",
                            wait_after=0,
                            optional=True,
                        ),
                    )
                    plan.steps.insert(
                        insert_idx + 2,
                        ExecutionStep(
                            id=0,
                            description="Read additional content after scrolling",
                            tool="browser_read_page",
                            args={"query": query},
                            success_criteria="Additional research content extracted",
                            wait_after=0,
                            optional=True,
                        ),
                    )
                    repairs.append("inserted 3 research reading steps for depth")
                else:
                    read_idx = next(
                        (i for i, step in enumerate(plan.steps) if step.tool in content_read_tools),
                        insert_idx,
                    )
                    plan.steps.insert(
                        read_idx + 1,
                        ExecutionStep(
                            id=0,
                            description="Scroll down for more research content",
                            tool="browser_scroll",
                            args={"direction": "down", "amount": "page"},
                            success_criteria="Scrolled for more content",
                            wait_after=0,
                            optional=True,
                        ),
                    )
                    plan.steps.insert(
                        read_idx + 2,
                        ExecutionStep(
                            id=0,
                            description="Read additional research content",
                            tool="browser_read_page",
                            args={"query": query},
                            success_criteria="Additional research content extracted",
                            wait_after=0,
                            optional=True,
                        ),
                    )
                    repairs.append("inserted scroll+read for research depth")

        if repairs:
            self._renumber_steps(plan)
        return repairs

    def _task_graph_coverage_errors(
        self,
        plan: ExecutionPlan,
        task_graph: Optional[TaskGraph],
        user_request: str = "",
    ) -> list[str]:
        if not task_graph or not self._is_compound_task_graph(task_graph):
            return []

        errors: list[str] = []
        step_tools = [step.tool for step in plan.steps]
        args_text = " ".join(
            json.dumps(step.args, sort_keys=True).lower()
            for step in plan.steps
            if isinstance(step.args, dict)
        )
        desc_text = " ".join(
            " ".join(
                bit for bit in [step.description, step.success_criteria]
                if isinstance(bit, str) and bit
            ).lower()
            for step in plan.steps
        )
        plan_text = " ".join(
            bit for bit in [plan.task_summary, plan.final_response, desc_text, args_text]
            if bit
        ).lower()

        entity_types = task_graph.entity_types()
        has_local_entity = bool(entity_types.intersection({"file", "folder", "content"}))
        has_app_entity = "app" in entity_types

        local_resolution_tools = {"list_directory", "read_file", "run_shell", "write_file", "replace_in_file"}
        selector_resolution_tools = local_resolution_tools | {
            "browser_find",
            "browser_list_tabs",
            "browser_switch_tab",
            "read_page_content",
            "extract_structured_data",
        }
        edit_or_clarify_tools = {
            "await_reply",
            "replace_in_file",
            "write_file",
            "type_text",
            "type_in_field",
            "browser_type_ref",
            "click_ui",
            "click_element",
            "press_key",
            "run_shortcut",
            "mouse_action",
        }

        if has_app_entity and has_local_entity:
            if "open_app" not in step_tools:
                errors.append("compound app+local task missing app-opening step")
            if not any(tool in local_resolution_tools for tool in step_tools):
                errors.append("compound app+local task missing local source resolution step")

        if any(selector in {"latest", "current", "selected", "first", "last"} for selector in task_graph.selectors):
            if not any(tool in selector_resolution_tools for tool in step_tools):
                if not any(marker in plan_text for marker in ("latest", "newest", "most recent", "current", "selected", "first", "last")):
                    errors.append("selector-driven task missing item selection step")

        if "specific_edit_instructions" in task_graph.unresolved_slots and "await_reply" not in step_tools:
            errors.append("edit task missing await_reply for unresolved edit instructions")

        if any(outcome in {"apply_edit", "edit_media"} for outcome in task_graph.desired_outcomes):
            if not any(tool in edit_or_clarify_tools for tool in step_tools):
                errors.append("edit task missing edit or clarification step")

        if len(plan.steps) < 2:
            request_text = (user_request or "").lower()
            if any(marker in f" {request_text} " for marker in (" and ", " then ", " after ", " using ", " with ", " from ", " into ")):
                errors.append("compound task graph collapsed to an under-specified plan")

        return errors

    def _preflight_validate_plan(
        self,
        plan: ExecutionPlan,
        user_request: str = "",
        task_graph: Optional[TaskGraph] = None,
    ) -> tuple[bool, str]:
        contracts = self._get_tool_contracts()
        if not contracts:
            return True, ""

        request_text = (user_request or "").lower()
        repairs = self._repair_plan_structure(plan, user_request=user_request)
        if repairs:
            print(f"[Planner] Auto-repaired plan: {'; '.join(repairs)}")

        errors: list[str] = []
        for step in plan.steps:
            self._normalize_step_args(step)

            contract = contracts.get(step.tool)
            if not contract:
                errors.append(f"step {step.id}: unknown tool '{step.tool}'")
                continue

            args = step.args if isinstance(step.args, dict) else {}
            allowed = contract["allowed"]
            required = contract["required"]

            unknown_keys = sorted(k for k in args.keys() if k not in allowed)
            if unknown_keys:
                errors.append(f"step {step.id}: invalid args for {step.tool}: {', '.join(unknown_keys)}")

            missing_required = sorted(k for k in required if k not in args)
            if missing_required:
                errors.append(f"step {step.id}: missing required args for {step.tool}: {', '.join(missing_required)}")

        browser_ref_tools = {"browser_click_ref", "browser_type_ref", "browser_select_ref"}
        has_ref_action = any(step.tool in browser_ref_tools for step in plan.steps)
        if has_ref_action:
            first_ref_idx = next(
                (idx for idx, step in enumerate(plan.steps) if step.tool in browser_ref_tools),
                None,
            )
            baseline_tools = {"browser_snapshot", "browser_read_page", "browser_refresh_refs"}
            if first_ref_idx is not None:
                has_baseline = any(step.tool in baseline_tools for step in plan.steps[:first_ref_idx])
                if not has_baseline:
                    errors.append("browser ref-action missing prior browser_snapshot/browser_read_page baseline")

        seen_reads_by_path: set[str] = set()
        for step in plan.steps:
            path = str(step.args.get("path", "")).strip()
            if step.tool == "read_file" and path:
                seen_reads_by_path.add(path)
            if step.tool == "replace_in_file":
                if not path:
                    errors.append(f"step {step.id}: replace_in_file missing path")
                    continue
                if path not in seen_reads_by_path:
                    errors.append(f"step {step.id}: replace_in_file should be preceded by read_file for {path}")

        if self._is_research_document_request(user_request, plan.task_summary):
            research_tools = {
                "browser_read_page",
                "browser_read_text",
                "read_page_content",
                "extract_structured_data",
                "get_page_summary",
                "fetch_web_content",
                "web_scrape",
                "web_search",
                "gworkspace_analyze",
                "gdocs_read",
                "read_file",
            }
            has_research_step = any(step.tool in research_tools for step in plan.steps)

            has_document_write = False
            for step in plan.steps:
                if step.tool in {"gdocs_create", "gdocs_append"}:
                    has_document_write = True
                    break
                if step.tool == "write_file":
                    path = str(step.args.get("path", "")).lower()
                    if path.endswith((".md", ".txt", ".rtf", ".doc", ".docx")):
                        has_document_write = True
                        break

            if not has_research_step:
                errors.append("research-document task missing research-read step")
            if not has_document_write:
                errors.append("research-document task missing document-writing step")

            if any(term in request_text for term in ("compare", "best", "top", "rank")):
                research_depth_steps = sum(1 for step in plan.steps if step.tool in research_tools)
                if research_depth_steps < 2:
                    errors.append("comparison-style research task should include at least two research-read steps")

        is_research_doc = self._is_research_document_request(user_request, plan.task_summary)
        if self._is_general_research_request(user_request, plan.task_summary) and not is_research_doc:
            research_tools = {
                "browser_read_page",
                "browser_read_text",
                "read_page_content",
                "extract_structured_data",
                "get_page_summary",
                "fetch_web_content",
                "web_scrape",
                "web_search",
                "open_url",
                "gworkspace_analyze",
                "gdocs_read",
                "read_file",
            }
            content_read_tools = {
                "browser_read_page",
                "browser_read_text",
                "read_page_content",
                "extract_structured_data",
                "get_page_summary",
                "fetch_web_content",
                "web_scrape",
                "gworkspace_analyze",
                "gdocs_read",
                "read_file",
            }
            research_depth_steps = sum(1 for step in plan.steps if step.tool in research_tools)
            if research_depth_steps < 2:
                errors.append("research task should include at least two research steps")
            if not any(step.tool in content_read_tools for step in plan.steps):
                errors.append("research task missing content-reading step")

        errors.extend(
            self._task_graph_coverage_errors(
                plan,
                task_graph,
                user_request=user_request,
            )
        )

        if not errors:
            return True, ""
        return False, "; ".join(errors[:4])
