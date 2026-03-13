"""AI-assisted browser candidate selection helpers."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Tuple

from .resolver import BrowserResolver
from .store import browser_store
from providers.gemini import GeminiProvider

# Maximum time to wait for a Flash LLM selection before falling back to deterministic
_FLASH_TIMEOUT_SECONDS = 8.0


_resolver = BrowserResolver()
_provider_cache: Dict[Tuple[str, str], GeminiProvider] = {}


class FlashSelectionError(Exception):
    """Raised when the Flash selector cannot return a valid candidate."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def query_implies_field(query: str) -> bool:
    query_norm = _norm(query)
    hints = [
        "field", "input", "textbox", "text box", "search box", "searchbar",
        "search bar", "search field", "combobox", "entry", "box"
    ]
    return any(hint in query_norm for hint in hints)


def _get_gemini_provider(api_key: str, model_name: str) -> GeminiProvider:
    cache_key = (api_key, model_name)
    provider = _provider_cache.get(cache_key)
    if provider is None:
        provider = GeminiProvider(api_key=api_key, model=model_name)
        _provider_cache[cache_key] = provider
    return provider


def merge_candidate_lists(*candidate_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for candidate_list in candidate_lists:
        for candidate in candidate_list:
            ref_id = candidate.get("ref_id")
            if ref_id in seen:
                continue
            seen.add(ref_id)
            merged.append(candidate)
    merged.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return merged


def build_ranked_candidates(query: str, action: str, session_id: str = "", limit: int = 8) -> Tuple[Any, List[Dict[str, Any]], str]:
    snapshot = browser_store.get_snapshot(session_id or None)
    if not snapshot and session_id:
        snapshot = browser_store.get_snapshot(None)
    if not snapshot:
        return None, [], (
            "ERROR: No active browser snapshot is available. The Chrome extension bridge "
            "must connect and publish a page snapshot before browser ref tools can be used."
        )

    ranked = _resolver.describe_candidates(query, snapshot.elements, action=action, limit=max(1, min(limit, 10)))
    if action == "click" and query_implies_field(query):
        field_ranked = _resolver.describe_candidates(query, snapshot.elements, action="type", limit=max(1, min(limit, 10)))
        ranked = merge_candidate_lists(field_ranked, ranked)[: max(1, min(limit, 10))]
    return snapshot, ranked, ""


def _degraded_fallback(
    ranked: List[Dict[str, Any]],
    *,
    reason: str,
) -> Dict[str, Any]:
    return {
        "ref_id": ranked[0]["ref_id"],
        "reason": f"degraded_mode=true: {reason}",
        "model": "deterministic-resolver-degraded",
        "degraded_mode": True,
        "degraded_reason": reason,
        "candidates": ranked,
    }


async def select_browser_candidate_with_flash(
    query: str,
    action: str,
    session_id: str = "",
    text: str = "",
    option: str = "",
    limit: int = 8,
) -> Tuple[Dict[str, Any], str]:
    snapshot, ranked, error = build_ranked_candidates(query, action, session_id=session_id, limit=limit)
    if not snapshot:
        return {}, error
    if not ranked:
        return {}, f"No browser candidates matched query '{query}' for action '{action}'."

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model_name = os.environ.get("GEMINI_FAST_MODEL", "gemini-3-flash-preview").strip() or "gemini-3-flash-preview"

    if not api_key:
        reason = "Flash model unavailable: GEMINI_API_KEY is not configured."
        print(f"[SelectorAI] degraded_mode=true reason={reason} query='{query}'")
        return _degraded_fallback(ranked, reason=reason), ""

    provider = _get_gemini_provider(api_key=api_key, model_name=model_name)
    if not await provider.is_available():
        reason = f"Flash model unavailable: provider '{model_name}' is not available."
        print(f"[SelectorAI] degraded_mode=true reason={reason} query='{query}'")
        return _degraded_fallback(ranked, reason=reason), ""

    # ── Flash LLM selection with hard timeout ──
    t_flash = time.time()
    try:
        result = await asyncio.wait_for(
            _flash_select(provider, snapshot, query, action, text, option, ranked),
            timeout=_FLASH_TIMEOUT_SECONDS,
        )
        elapsed = time.time() - t_flash
        result["candidates"] = ranked
        result["degraded_mode"] = False
        result["degraded_reason"] = ""
        print(f"[SelectorAI] Flash selected ref '{result.get('ref_id')}' in {elapsed:.1f}s degraded_mode=false")
        return result, ""
    except asyncio.TimeoutError:
        elapsed = time.time() - t_flash
        reason = f"Flash selection timed out after {elapsed:.1f}s."
    except FlashSelectionError as e:
        reason = e.reason
    except Exception as e:
        reason = f"Flash selection error: {e}"

    print(f"[SelectorAI] degraded_mode=true reason={reason} query='{query}'")
    return _degraded_fallback(ranked, reason=reason), ""


async def _flash_select(
    provider: GeminiProvider,
    snapshot,
    query: str,
    action: str,
    text: str,
    option: str,
    ranked: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Inner Flash LLM call — separated so we can wrap it with asyncio.wait_for."""
    model_name = provider._model

    system_prompt = (
        "You are selecting the best browser DOM element candidate for a desktop automation agent. "
        "Return ONLY JSON with keys ref_id and reason. Choose exactly one ref_id from the provided candidates. "
        "Prefer visible, enabled, semantically correct matches for the requested action. "
        "If the query implies a field, input, search box, search bar, textbox, or combobox, prefer editable field roles over adjacent buttons. "
        "If the query explicitly says button, prefer button roles. If it says link, prefer links. "
        "Avoid auxiliary browser chrome or search-engine utility controls like 'About this result', 'Cached', "
        "'Images', 'Videos', or similar navigation unless the user explicitly asks for them."
    )
    user_prompt = json.dumps({
        "query": query,
        "action": action,
        "input_text": text,
        "option": option,
        "page": {
            "url": snapshot.url,
            "title": snapshot.title,
            "generation": snapshot.generation,
        },
        "candidates": ranked,
    }, ensure_ascii=False)

    response = await provider.generate(
        messages=[{"role": "user", "parts": [{"text": user_prompt}]}],
        system_prompt=system_prompt,
        tools=[],
        temperature=0.0,
    )
    if response.error:
        raise FlashSelectionError(f"Flash model returned an error: {response.error}")

    raw = (response.text or "").strip()
    if not raw:
        raise FlashSelectionError("Flash model returned an empty response.")

    try:
        start = raw.find("{")
        end = raw.rfind("}")
        payload = json.loads(raw[start:end + 1] if start != -1 and end != -1 else raw)
    except Exception:
        payload = {"ref_id": raw.strip().strip('"'), "reason": raw}

    chosen_ref = str(payload.get("ref_id", "")).strip()
    valid_ids = {candidate["ref_id"] for candidate in ranked}
    if chosen_ref not in valid_ids:
        raise FlashSelectionError(f"Flash model selected invalid ref_id '{chosen_ref}'.")

    return {
        "ref_id": chosen_ref,
        "reason": str(payload.get("reason", "")).strip(),
        "model": model_name,
    }
