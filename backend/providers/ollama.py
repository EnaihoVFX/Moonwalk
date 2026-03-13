"""
Moonwalk — Ollama Provider (Local)
=====================================
Ollama — local, fast, free, offline.
"""

import json
from typing import Optional
from functools import partial

import httpx

from providers.base import LLMProvider, LLMResponse, ToolCall

print = partial(print, flush=True)


class OllamaProvider(LLMProvider):
    """Ollama — local, fast, free, offline."""

    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str = "http://localhost:11434",
    ):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._available: Optional[bool] = None
        print(f"[Ollama] Configured (model: {model}, url: {base_url})")

    @property
    def name(self) -> str:
        return f"ollama ({self._model})"

    @property
    def supports_vision(self) -> bool:
        # Only LLaVA and similar models support vision
        return any(v in self._model.lower() for v in ["llava", "bakllava", "moondream"])

    @property
    def supports_tools(self) -> bool:
        # Ollama supports tools with compatible models
        return True

    async def is_available(self) -> bool:
        """Check if Ollama is running and the model is available."""
        if self._available is not None:
            return bool(self._available)
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_names = [m.get("name", "") for m in models]
                    # Check if our model (or a match) is available
                    self._available = any(
                        self._model in name or name.startswith(self._model.split(":")[0])
                        for name in model_names
                    )
                    if not self._available:
                        print(f"[Ollama] Model '{self._model}' not found. Available: {model_names}")
                    return bool(self._available)
        except Exception as e:
            print(f"[Ollama] Not available: {e}")
        self._available = False
        return False

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        image_data: Optional[bytes] = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Call the Ollama /api/chat endpoint."""

        # Build messages in Ollama format
        ollama_messages = [{"role": "system", "content": system_prompt}]

        for msg in messages:
            role = msg.get("role", "user")
            parts = msg.get("parts", [])

            # Map roles
            if role == "function":
                # Function responses → user message with tool context
                for p in parts:
                    if "function_response" in p:
                        fr = p["function_response"]
                        ollama_messages.append({
                            "role": "tool",
                            "content": json.dumps(fr.get("response", {})),
                        })
                continue

            content: str = ""
            for p in parts:
                if isinstance(p, dict):
                    if "text" in p:
                        content += str(p["text"])  # type: ignore
                    elif "function_call" in p:
                        # Model's tool call — already handled by Ollama natively
                        fc = p["function_call"]
                        ollama_messages.append({  # type: ignore
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{
                                "function": {
                                    "name": fc["name"],
                                    "arguments": fc.get("args", {}),
                                }
                            }]
                        })
                        continue

            if content:
                ollama_role = "assistant" if role == "model" else role
                ollama_messages.append({"role": ollama_role, "content": content})

        # Build request
        request_body = {
            "model": self._model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": temperature},
        }

        # Add tools if available
        if tools:
            ollama_tools = []
            for t in tools:
                ollama_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    }
                })
            request_body["tools"] = ollama_tools

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=request_body,
                )
                if resp.status_code != 200:
                    return LLMResponse(
                        error=f"Ollama HTTP {resp.status_code}: {resp.text}",
                        provider=self.name,
                    )
                data = resp.json()
        except httpx.TimeoutException:
            return LLMResponse(error="Ollama request timed out", provider=self.name)
        except Exception as e:
            return LLMResponse(error=f"Ollama error: {e}", provider=self.name)

        # Parse response
        result = LLMResponse(provider=self.name)
        msg = data.get("message", {})

        # Check for tool calls
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                result.tool_calls.append(ToolCall(
                    name=func.get("name", ""),
                    args=func.get("arguments", {}),
                ))

        # Text content
        content = msg.get("content", "")
        if content:
            result.text = content

        return result
