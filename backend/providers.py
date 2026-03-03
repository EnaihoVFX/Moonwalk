"""
Moonwalk — LLM Providers
==========================
Abstract LLM interface with concrete implementations for:
  - Gemini (cloud, multimodal, highest quality)
  - Ollama (local, fast, free, offline)

Each provider implements the same interface so the agent loop
doesn't care which one it's talking to.
"""

import asyncio
import json
import os
import httpx
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, AsyncIterator, Any
from functools import partial

print = partial(print, flush=True)


# ═══════════════════════════════════════════════════════════════
#  Unified Response Types
# ═══════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    """A single tool/function call from the LLM."""
    name: str
    args: dict


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    error: Optional[str] = None
    # Raw model response parts — needed for Gemini 3 thought signatures
    raw_model_parts: Optional[list] = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ═══════════════════════════════════════════════════════════════
#  Abstract Provider Interface
# ═══════════════════════════════════════════════════════════════

class LLMProvider(ABC):
    """Abstract interface all LLM providers must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether this provider can handle image inputs."""
        ...

    @property
    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether this provider supports native function calling."""
        ...

    @abstractmethod
    async def generate(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        image_data: Optional[bytes] = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        ...
        
    async def generate_stream(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        image_data: Optional[bytes] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMResponse]:
        """Streaming version of generate. Yields LLMResponse chunks."""
        raise NotImplementedError("Streaming not supported by this provider.")
        yield LLMResponse() # for type checking

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this provider is ready to use."""
        ...


# ═══════════════════════════════════════════════════════════════
#  Gemini Provider (Cloud)
# ═══════════════════════════════════════════════════════════════

class GeminiProvider(LLMProvider):
    """Google Gemini — cloud, multimodal, highest quality."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self._model = model
        self._api_key = api_key
        self._client: Any = None

        if api_key:
            try:
                from google import genai
                from google.genai import types as genai_types
                self._client = genai.Client(api_key=api_key)
                self._genai_types = genai_types
                print(f"[Gemini] Client initialized (model: {model})")
            except ImportError:
                print("[Gemini] google-genai not installed")
            except Exception as e:
                print(f"[Gemini] Init error: {e}")

    @property
    def name(self) -> str:
        return f"gemini ({self._model})"

    @property
    def supports_vision(self) -> bool:
        return True

    @property
    def supports_tools(self) -> bool:
        return True

    async def is_available(self) -> bool:
        return self._client is not None

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        image_data: Optional[bytes] = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        if not self._client:
            return LLMResponse(error="Gemini client not initialized")

        types = self._genai_types

        # Build tool declarations
        tool_decls = []
        if tools:
            tool_decls = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=t["parameters"],
                )
                for t in tools
            ])]

        # Attach image to last user message if provided
        contents = [dict(m) for m in messages]  # shallow copy
        if image_data and contents and contents[-1].get("role") == "user":
            img_part = types.Part.from_bytes(data=image_data, mime_type="image/png")
            contents[-1] = dict(contents[-1])
            contents[-1]["parts"] = list(contents[-1].get("parts", [])) + [img_part]

        try:
            # Conditionally apply thinking budget if requested or on pro/thinking models
            config_kwargs = {
                "system_instruction": system_prompt,
                "tools": tool_decls if tool_decls else None,
                "temperature": temperature,
            }
            if "pro" in self._model or "thinking" in self._model:
                config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=1024)

            import asyncio
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs)
                ),
                timeout=60.0
            )
        except Exception as e:
            return LLMResponse(error=f"Gemini API error: {e}", provider=self.name)

        # Parse response
        candidate = response.candidates[0] if response.candidates else None
        if not candidate:
            return LLMResponse(text="I'm not sure how to help with that.", provider=self.name)
            
        content = getattr(candidate, "content", None)
        if not content or not getattr(content, "parts", None):
            return LLMResponse(text="I'm not sure how to help with that.", provider=self.name)

        result = LLMResponse(provider=self.name)

        # Preserve raw parts for thought signature support (Gemini 3)
        raw_parts = candidate.content.parts
        result.raw_model_parts = raw_parts

        for part in raw_parts:
            if part.function_call:
                fc = part.function_call
                result.tool_calls.append(ToolCall(
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {}
                ))
            elif part.text:
                result.text = (result.text or "") + part.text

        return result

    async def generate_stream(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        image_data: Optional[bytes] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMResponse]:
        """Yield LLMResponse 'chunks' over the generate_content_stream API."""
        if not self._client:
            yield LLMResponse(error="Gemini client not initialized", provider=self.name)
            return

        types = self._genai_types
        tool_decls = []
        if tools:
            tool_decls = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=t["parameters"],
                ) for t in tools
            ])]

        contents = []
        for m in messages:
            msg = dict(m)
            if "parts" in msg:
                new_parts = []
                for p in msg["parts"]:
                    # Convert raw dict function_response to SDK Part type
                    if isinstance(p, dict) and "function_response" in p:
                        fr = p["function_response"]
                        new_parts.append(types.Part.from_function_response(
                            name=fr["name"],
                            response=fr["response"]
                        ))
                    else:
                        new_parts.append(p)
                msg["parts"] = new_parts
            contents.append(msg)
            
        if image_data and contents and contents[-1].get("role") == "user":
            img_part = types.Part.from_bytes(data=image_data, mime_type="image/png")
            contents[-1] = dict(contents[-1])
            contents[-1]["parts"] = list(contents[-1].get("parts", [])) + [img_part]

        config_kwargs = {
            "system_instruction": system_prompt,
            "tools": tool_decls if tool_decls else None,
            "temperature": temperature,
        }
        if "pro" in self._model or "thinking" in self._model:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=1024)

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs)
            )
            
            import asyncio
            # Use a wrapper to apply a timeout to each chunk retrieval
            async def iterate_with_timeout():
                # We need to manually drive the async iterator to apply timeouts per-chunk
                stream_iter = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=60.0)
                        yield chunk
                    except StopAsyncIteration:
                        break
            
            async for chunk in iterate_with_timeout():
                if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                    continue
                
                chunk_response = LLMResponse(provider=self.name)
                chunk_response.raw_model_parts = chunk.candidates[0].content.parts
                
                for part in chunk.candidates[0].content.parts:
                    if part.function_call:
                        fc = part.function_call
                        chunk_response.tool_calls.append(ToolCall(
                            name=fc.name,
                            args=dict(fc.args) if fc.args else {}
                        ))
                    elif part.text:
                        chunk_response.text = (chunk_response.text or "") + part.text
                        
                yield chunk_response

        except Exception as e:
            yield LLMResponse(error=f"Gemini streaming error: {e}", provider=self.name)
# ═══════════════════════════════════════════════════════════════
#  Ollama Provider (Local)
# ═══════════════════════════════════════════════════════════════

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
