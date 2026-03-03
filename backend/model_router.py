"""
Moonwalk — Intelligent Model Router
=====================================
Two-phase LLM routing — Flash acts as a cheap, fast routing agent:

  Phase 1 — ROUTE (Gemini 3 Flash)
    Flash receives the request + desktop context and decides:
    • Handle it directly with tool calls (simple tasks)
    • Escalate to Pro for complex reasoning / multimodal analysis

  Phase 2 — EXECUTE (Gemini 3.1 Pro, only when needed)
    Pro handles deep reasoning, screen analysis, multi-step planning

This replaces brittle regex matching with genuine intelligence.
"""

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from functools import partial

print = partial(print, flush=True)

from providers import LLMProvider, GeminiProvider


# ═══════════════════════════════════════════════════════════════
#  Routing Tiers
# ═══════════════════════════════════════════════════════════════

class Tier(Enum):
    FAST = 1       # Gemini 3 Flash — cheap router + simple executor
    POWERFUL = 2   # Gemini 3.1 Pro — complex reasoning, multimodal


@dataclass
class RouteDecision:
    """Result of the routing agent's classification."""
    tier: Tier
    provider: LLMProvider
    reason: str
    model_name: str = ""


# ═══════════════════════════════════════════════════════════════
#  Model Config
# ═══════════════════════════════════════════════════════════════

FAST_MODEL = "gemini-3-flash-preview"
POWERFUL_MODEL = "gemini-3.1-pro-preview-customtools"
ROUTING_MODEL = "gemini-2.5-flash"

# The routing prompt that Flash uses to classify requests
ROUTING_PROMPT = """You are a routing classifier for a desktop AI assistant called Moonwalk.

Your job: decide which AI model should handle this user request.

## Available Models
- **FAST**: Cheap, fast. Good for: direct actions, opening apps/websites, simple Q&A, single-step tasks, basic commands.
- **POWERFUL**: Gemini Pro. Expensive, slow but brilliant. Good for: multi-step reasoning, analyzing screenshots, writing essays, debugging code, homework help, complex context-dependent tasks, and SPAWNING BACKGROUND AGENTS.

## Rules
1. Most requests should go to FAST. Only escalate if the task genuinely needs deep reasoning.
2. Opening apps, websites, playing media, simple questions → FAST
3. "Help me with this", "explain this code", "analyze my screen", writing tasks → POWERFUL
4. If a screenshot is attached → POWERFUL (it needs vision analysis)
5. **CRITICAL:** If the user mentions creating, spawning, or managing a "background agent" or "sub-agent" → POWERFUL (agent orchestration requires complex parameter planning).
6. When unsure, choose FAST — it's better to try and escalate later than waste time.

Respond with EXACTLY one word: either FAST or POWERFUL"""


# ═══════════════════════════════════════════════════════════════
#  Model Router
# ═══════════════════════════════════════════════════════════════

class ModelRouter:
    """
    Flash-powered routing agent.
    Flash classifies requests and either handles them or escalates to Pro.
    """

    def __init__(self):
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

        self._router: Optional[GeminiProvider] = None
        self._fast: Optional[GeminiProvider] = None
        self._powerful: Optional[GeminiProvider] = None
        self._initialized = False

    async def initialize(self):
        """Lazily init Gemini providers."""
        if self._initialized:
            return
        self._initialized = True

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            print("[Router] ✗ No GEMINI_API_KEY set — agent cannot function.")
            return

        def get_provider(model_name: str) -> GeminiProvider:
            return GeminiProvider(api_key=api_key, model=model_name)

        # Routing model (ultra-cheap, ultra-fast tier 0)
        routing_name = os.environ.get("GEMINI_ROUTING_MODEL", ROUTING_MODEL)
        self._router = get_provider(routing_name)
        if self._router and await self._router.is_available():
            print(f"[Router] ✓ ROUTER (classifier): {routing_name}")
        else:
            print(f"[Router] ✗ ROUTER failed: {routing_name}")
            self._router = None

        # Fast model (cheap, simple tasks)
        fast_name = os.environ.get("GEMINI_FAST_MODEL", FAST_MODEL)
        self._fast = get_provider(fast_name)
        if self._fast and await self._fast.is_available():
            print(f"[Router] ✓ FAST (simple tasks): {fast_name}")
        else:
            print(f"[Router] ✗ FAST failed: {fast_name}")
            self._fast = None

        # Powerful model (complex/multimodal)
        powerful_name = os.environ.get("GEMINI_POWERFUL_MODEL", POWERFUL_MODEL)
        self._powerful = get_provider(powerful_name)
        if self._powerful and await self._powerful.is_available():
            print(f"[Router] ✓ POWERFUL (escalation): {powerful_name}")
        else:
            print(f"[Router] ✗ POWERFUL failed: {powerful_name}")
            self._powerful = None

        # Fallback model (for when primary models fail)
        fallback_name = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-pro")
        self._fallback = get_provider(fallback_name)
        if self._fallback and await self._fallback.is_available():
            print(f"[Router] ✓ FALLBACK (emergency): {fallback_name}")
        else:
            print(f"[Router] ✗ FALLBACK failed: {fallback_name}")
            self._fallback = None

        # Summary
        print(f"[Router] Ready: Using dedicated routing model")

    @property
    def fallback(self):
        return self._fallback

    async def route(
        self,
        text: str,
        context_summary: str = "",
        has_screenshot: bool = False,
    ) -> RouteDecision:
        """
        Use the routing model to classify the request.
        """
        await self.initialize()
        start = time.time()

        # If screenshot is attached, always use Pro (needs vision analysis)
        if has_screenshot and self._powerful:
            ms = (time.time() - start) * 1000
            print(f"[Router] → POWERFUL ({ms:.0f}ms): Screenshot attached, needs vision")
            return RouteDecision(
                tier=Tier.POWERFUL,
                provider=self._powerful,
                reason="Screenshot attached → needs vision analysis",
                model_name=self._powerful._model,
            )

        # If only Flash is available, use Flash for everything
        if not self._powerful and self._fast:
            ms = (time.time() - start) * 1000
            print(f"[Router] → FAST ({ms:.0f}ms): Pro unavailable, Flash handles all")
            return RouteDecision(
                tier=Tier.FAST,
                provider=self._fast,
                reason="Pro unavailable → Flash handles everything",
                model_name=self._fast._model,
            )

        # If both available, use routing model to classify
        if self._fast and self._powerful:
            tier = await self._classify_with_router(text, context_summary)
            ms = (time.time() - start) * 1000

            if tier == Tier.POWERFUL:
                print(f"[Router] → POWERFUL ({ms:.0f}ms): Classified as complex")
                return RouteDecision(
                    tier=Tier.POWERFUL,
                    provider=self._powerful,
                    reason="Classified as complex → Pro",
                    model_name=self._powerful._model,
                )
            else:
                print(f"[Router] → FAST ({ms:.0f}ms): Classified as simple/direct action")
                return RouteDecision(
                    tier=Tier.FAST,
                    provider=self._fast,
                    reason="Classified as simple request",
                    model_name=self._fast._model,
                )

        # No models available at all
        raise RuntimeError("No Gemini models available. Set GEMINI_API_KEY in .env")

    async def _classify_with_router(self, text: str, context_summary: str) -> Tier:
        """
        Ask Router model to classify: should this be FAST or POWERFUL?
        """
        router = self._router or self._fast
        if not router:
            return Tier.FAST

        classify_input = f"User request: \"{text}\""
        if context_summary:
            classify_input += f"\nDesktop context: {context_summary}"

        try:
            from google.genai import types as genai_types
            response = await router._client.aio.models.generate_content(
                model=router._model,
                contents=[{"role": "user", "parts": [{"text": classify_input}]}],
                config=genai_types.GenerateContentConfig(
                    system_instruction=ROUTING_PROMPT,
                    temperature=0.0,
                    max_output_tokens=2,  # Just need "FAST" or "POWERFUL"
                )
            )

            if response.text:
                answer = response.text.strip().upper()
                if "POWERFUL" in answer:
                    return Tier.POWERFUL
                return Tier.FAST
        except Exception as e:
            print(f"[Router] Classification error: {e}, defaulting to FAST")

        return Tier.FAST

    @property
    def fast(self) -> Optional[GeminiProvider]:
        return self._fast

    @property
    def powerful(self) -> Optional[GeminiProvider]:
        return self._powerful

    def status(self) -> dict:
        """Current router status for debugging."""
        return {
            "fast": self._fast.name if self._fast else "unavailable",
            "powerful": self._powerful.name if self._powerful else "unavailable",
        }
