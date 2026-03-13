"""
Test V2 Agent Components
=========================
Quick sanity tests for the new agent architecture.
"""

import asyncio
import sys
import os

# Add backend to path
backend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, backend_path)

from agent.world_state import WorldState, UserIntent, IntentParser, IntentAction, TargetType
from agent.legacy_planner import ExecutionPlan, PlanBuilder, PlanTemplates
from agent.planner import ExecutionStep, StepStatus
from agent.task_planner import TaskPlanner
from agent.verifier import ToolVerifier, get_verifier
from agent.browser_intent_utils import is_browser_chrome_action, looks_like_browser_ui_shell_command
from tools.selector import ToolSelector, get_tool_selector


def test_intent_parser():
    """Test intent parsing for various requests."""
    print("\n=== Testing Intent Parser ===")
    parser = IntentParser()
    
    test_cases = [
        ("Open Spotify", IntentAction.OPEN, TargetType.APP, "Spotify"),
        ("open youtube", IntentAction.OPEN, TargetType.URL, "https://youtube.com"),
        ("close the window", IntentAction.CLOSE, TargetType.UNKNOWN, ""),
        ("delete it", IntentAction.DELETE, TargetType.UNKNOWN, ""),  # Should be ambiguous
        ("search for cats", IntentAction.SEARCH, TargetType.UNKNOWN, ""),
        ("play music", IntentAction.PLAY, TargetType.APP, "Music"),  # Music is a valid app
        ("quit Safari", IntentAction.CLOSE, TargetType.APP, "Safari"),
    ]
    
    passed = 0
    for text, expected_action, expected_target_type, expected_value in test_cases:
        intent = parser.parse(text)
        
        action_ok = intent.action == expected_action
        target_type_ok = intent.target_type == expected_target_type
        value_ok = expected_value in intent.target_value if expected_value else True
        
        status = "✓" if (action_ok and target_type_ok) else "✗"
        print(f"  {status} '{text}' → {intent.action.value}:{intent.target_type.value}:{intent.target_value}")
        
        if action_ok and target_type_ok:
            passed += 1
    
    print(f"  Result: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


def test_ambiguity_detection():
    """Test that ambiguous requests are detected."""
    print("\n=== Testing Ambiguity Detection ===")
    parser = IntentParser()
    
    ambiguous_requests = [
        "delete it",
        "open it",
        "make it bigger",
        "close that",
    ]
    
    unambiguous_requests = [
        "open Spotify",
        "open youtube.com",
        "play music",
        "search for cats",
    ]
    
    passed = 0
    
    for text in ambiguous_requests:
        intent = parser.parse(text)
        if intent.ambiguous:
            print(f"  ✓ '{text}' → ambiguous (correct)")
            passed += 1
        else:
            print(f"  ✗ '{text}' → not ambiguous (should be)")
    
    for text in unambiguous_requests:
        intent = parser.parse(text)
        if not intent.ambiguous:
            print(f"  ✓ '{text}' → not ambiguous (correct)")
            passed += 1
        else:
            print(f"  ✗ '{text}' → ambiguous (should not be)")
    
    total = len(ambiguous_requests) + len(unambiguous_requests)
    print(f"  Result: {passed}/{total} passed")
    return passed == total


def test_plan_templates():
    """Test pre-built plan templates."""
    print("\n=== Testing Plan Templates ===")
    
    # Test open_app template
    plan = PlanTemplates.open_app("Spotify")
    assert plan.task_summary == "Open Spotify"
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == "open_app"
    assert plan.steps[0].args["app_name"] == "Spotify"
    assert plan.final_response == "Opening Spotify!"
    print("  ✓ open_app template")
    
    # Test open_url template
    plan = PlanTemplates.open_url("https://youtube.com")
    assert plan.task_summary == "Open https://youtube.com"
    assert plan.steps[0].tool == "open_url"
    print("  ✓ open_url template")
    
    # Test search_web template
    plan = PlanTemplates.search_web("lofi music")
    assert "google.com/search" in plan.steps[0].args["url"]
    assert "lofi" in plan.steps[0].args["url"]
    print("  ✓ search_web template")
    
    # Test clarification template
    plan = PlanTemplates.clarification_needed("What would you like me to delete?")
    assert plan.needs_clarification
    assert "delete" in plan.clarification_prompt
    print("  ✓ clarification_needed template")
    
    print("  Result: 4/4 passed")
    return True


def test_plan_builder():
    """Test fluent plan builder."""
    print("\n=== Testing Plan Builder ===")
    
    plan = (PlanBuilder("Test multi-step task")
        .add_step(
            description="Step 1",
            tool="open_app",
            args={"app_name": "Safari"},
            success_criteria="Safari is active"
        )
        .add_step(
            description="Step 2",
            tool="open_url",
            args={"url": "https://youtube.com"},
            depends_on=[1],
            wait_after=1.0
        )
        .with_response("Done!")
        .with_confidence(0.9)
        .build())
    
    assert len(plan.steps) == 2
    assert plan.steps[0].id == 1
    assert plan.steps[1].id == 2
    assert plan.steps[1].depends_on == [1]
    assert plan.steps[1].wait_after == 1.0
    assert plan.final_response == "Done!"
    assert plan.confidence == 0.9
    print("  ✓ Built 2-step plan with dependencies")
    
    # Test step status tracking
    assert not plan.is_complete()
    plan.mark_step_complete(1, "Success")
    assert plan.steps[0].status == StepStatus.COMPLETED
    assert plan.progress_percentage() == 50.0
    plan.mark_step_complete(2, "Success")
    assert plan.is_complete()
    print("  ✓ Step status tracking works")
    
    print("  Result: 2/2 passed")
    return True


def test_tool_selector():
    """Test intelligent tool selection."""
    print("\n=== Testing Tool Selector ===")
    selector = ToolSelector()
    
    # Test app-related request
    tools = selector.select("open Spotify")
    assert "open_app" in tools
    assert "send_response" in tools  # Core tools always included
    print(f"  ✓ 'open Spotify' → {len(tools)} tools including open_app")
    
    # Test web-related request
    tools = selector.select("go to youtube")
    assert "open_url" in tools
    print(f"  ✓ 'go to youtube' → {len(tools)} tools including open_url")

    # Test browser interaction request prefers extension DOM tools over screen clicking
    tools = selector.select(
        "play this video on YouTube",
        context_app="Google Chrome",
        context_url="https://www.youtube.com/watch?v=test"
    )
    assert "browser_snapshot" in tools
    assert "browser_click_match" in tools
    assert "browser_find" in tools
    assert "browser_click_ref" in tools
    assert "read_screen" not in tools
    assert "click_element" not in tools
    print("  ✓ 'play this video on YouTube' prefers browser DOM tools without read_screen")
    
    # Test file-related request
    tools = selector.select("create a new file")
    assert "write_file" in tools or "run_shell" in tools
    print(f"  ✓ 'create a new file' → {len(tools)} tools for file ops")
    
    print("  Result: 4/4 passed")
    return True


def test_browser_ui_shell_guard():
    """Test shell-command guard for browser/UI automation bypasses."""
    print("\n=== Testing Browser UI Shell Guard ===")

    blocked = looks_like_browser_ui_shell_command(
        "osascript -e 'tell application \"System Events\" to tell process \"Google Chrome\" to get name of every UI element of window 1'"
    )
    allowed = looks_like_browser_ui_shell_command("ls -la ~/Downloads")

    assert blocked is True
    assert allowed is False
    print("  ✓ Browser AppleScript shell command is blocked")
    print("  ✓ Normal shell command is allowed")
    print("  Result: 2/2 passed")
    return True


def test_browser_chrome_action_detection():
    """Test browser chrome actions are not treated as page-content DOM interactions."""
    print("\n=== Testing Browser Chrome Action Detection ===")

    assert is_browser_chrome_action("switch to my third tab in chrome") is True
    assert is_browser_chrome_action("go to the next tab") is True
    assert is_browser_chrome_action("click the youtube video title") is False

    selector = ToolSelector()
    tools = selector.select(
        "switch to my third tab",
        context_app="Google Chrome",
        context_url="https://www.instagram.com/"
    )
    assert "press_key" in tools
    assert "browser_find" not in tools
    assert "browser_click_match" not in tools
    print("  ✓ Browser chrome actions keep keyboard tools available")
    print("  ✓ Browser chrome actions do not force browser DOM tools")
    print("  Result: 2/2 passed")
    return True


def test_verifier():
    """Test tool verification."""
    print("\n=== Testing Tool Verifier ===")
    verifier = get_verifier()
    
    async def run_tests():
        # Test successful result
        result = await verifier.verify(
            tool_name="open_app",
            tool_args={"app_name": "Spotify"},
            tool_result="Successfully opened Spotify.",
            success_criteria="Spotify becomes active"
        )
        assert result.success
        print(f"  ✓ Successful result verified: {result.message}")
        
        # Test error detection
        result = await verifier.verify(
            tool_name="run_shell",
            tool_args={"command": "invalid_command"},
            tool_result="Error: command not found",
            success_criteria=""
        )
        assert not result.success
        print(f"  ✓ Error detected: {result.message}")
        
        # Test shell-specific verification
        result = await verifier.verify(
            tool_name="run_shell",
            tool_args={"command": "ls"},
            tool_result="file1.txt\nfile2.txt",
            success_criteria=""
        )
        assert result.success
        print(f"  ✓ Shell success verified: {result.message}")
    
    asyncio.run(run_tests())
    print("  Result: 3/3 passed")
    return True


def test_task_planner_sync():
    """Test synchronous planning compatibility (milestone fallback)."""
    print("\n=== Testing Task Planner (Sync) ===")
    planner = TaskPlanner()
    
    # Create a simple world state
    world_state = WorldState(
        active_app="Finder",
        window_title="Desktop"
    )
    
    # Test simple app open
    plan = planner.create_plan_sync("open spotify", world_state)
    assert not plan.needs_clarification
    assert len(plan.milestones) == 1
    assert plan.milestones[0].hint_tools == ["open_app"]
    print(f"  ✓ 'open spotify' → {plan.task_summary}")
    
    # Test ambiguous request
    plan = planner.create_plan_sync("delete it", world_state)
    assert plan.needs_clarification
    print(f"  ✓ 'delete it' → clarification: {plan.clarification_prompt}")
    
    # Test URL open
    plan = planner.create_plan_sync("open youtube", world_state)
    assert not plan.needs_clarification
    assert plan.milestones[0].hint_tools == ["open_url"]
    print(f"  ✓ 'open youtube' → {plan.milestones[0].hint_tools[0]}")
    
    print("  Result: 3/3 passed")


def test_antigravity_searcher():
    """Test the high-performance DOM searcher."""
    print("\n=== Testing Antigravity Searcher ===")
    from browser.search import search_engine
    
    # Mock DOM
    dom = [
        {"tagName": "button", "text": "Sign Up", "attributes": {"id": "1"}},
        {"tagName": "div", "text": "Sign Up for our newsletter", "attributes": {"id": "2"}},
        {"tagName": "input", "attributes": {"placeholder": "Search...", "id": "3"}},
        {"tagName": "a", "text": "Login", "attributes": {"href": "/login", "id": "4"}},
    ]
    
    # Test 1: Exact match button preference
    results = search_engine.search("Sign Up", dom)
    assert results[0]["attributes"]["id"] == "1"
    print("  ✓ 'Sign Up' prefers button over div")
    
    # Test 2: Placeholder match
    results = search_engine.search("search box", dom)
    assert results[0]["attributes"]["id"] == "3"
    print("  ✓ 'search box' finds input by placeholder")
    
    # Test 3: Fuzzy match
    results = search_engine.search("Log in", dom) # "Log in" vs "Login"
    assert results[0]["attributes"]["id"] == "4"
    print("  ✓ 'Log in' fuzzy matches 'Login'")
    
    print("  Result: 3/3 passed")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("  MOONWALK AGENT V2 — COMPONENT TESTS")
    print("=" * 60)
    
    results = []
    results.append(("Intent Parser", test_intent_parser()))
    results.append(("Ambiguity Detection", test_ambiguity_detection()))
    results.append(("Plan Templates", test_plan_templates()))
    results.append(("Plan Builder", test_plan_builder()))
    results.append(("Tool Selector", test_tool_selector()))
    results.append(("Browser Chrome Actions", test_browser_chrome_action_detection()))
    results.append(("Verifier", test_verifier()))
    results.append(("Task Planner (Sync)", test_task_planner_sync()))
    results.append(("Antigravity Searcher", test_antigravity_searcher()))
    
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    for name, ok in results:
        status = "✓" if ok else "✗"
        print(f"  {status} {name}")
    
    print(f"\n  Total: {passed}/{total} test suites passed")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
