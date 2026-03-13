import asyncio
import os
import sys
sys.path.insert(0, 'backend')
from providers.gemini import GeminiProvider

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), 'backend', '.env')
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

api_key = os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    print("ERROR: No GEMINI_API_KEY found")
    sys.exit(1)

model = "gemini-3.1-pro-preview-customtools"

async def test():
    print(f"Testing model: {model}")
    p = GeminiProvider(api_key=api_key, model=model)
    
    # Test 1: Simple request
    print("\n--- Test 1: Simple JSON request ---")
    resp = await p.generate(
        messages=[{'role': 'user', 'parts': [{'text': 'Return JSON: {"greeting": "hello"}'}]}],
        system_prompt='You are a task planner. Output ONLY valid JSON.',
        tools=[],
        temperature=0.1
    )
    print(f"  text: {repr(resp.text)}")
    print(f"  error: {repr(resp.error)}")
    
    # Test 2: A planning-style request (like the benchmark uses)
    print("\n--- Test 2: Planning request ---")
    resp2 = await p.generate(
        messages=[{'role': 'user', 'parts': [{'text': '''Given this user request, create an execution plan as JSON.

User Request: "open spotify"

Return JSON:
{
  "task_summary": "Open Spotify",
  "steps": [{"id": 1, "tool": "open_app", "args": {"app_name": "Spotify"}}],
  "final_response": "Opening Spotify!"
}'''}]}],
        system_prompt='You are a task planner. Output ONLY valid JSON.',
        tools=[],
        temperature=0.1
    )
    print(f"  text: {repr(resp2.text)}")
    print(f"  error: {repr(resp2.error)}")
    
    # Test 3: Check raw parts for thinking
    print("\n--- Test 3: Check raw_model_parts ---")
    if hasattr(resp, 'raw_model_parts') and resp.raw_model_parts:
        for i, part in enumerate(resp.raw_model_parts):
            print(f"  Part {i}: text={repr(part.text)}, thought={repr(getattr(part, 'thought', None))}")
    else:
        print("  No raw_model_parts")
    
    if hasattr(resp2, 'raw_model_parts') and resp2.raw_model_parts:
        for i, part in enumerate(resp2.raw_model_parts):
            print(f"  Part {i}: text={repr(part.text)}, thought={repr(getattr(part, 'thought', None))}")
    
asyncio.run(test())
