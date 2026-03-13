"""
Search-result extraction regressions for browser ACI tools.
"""

import asyncio
import json
import os
import sys

backend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, backend_path)

from browser.models import ElementFingerprint, ElementRef, PageSnapshot
from browser.store import browser_store
import tools.browser_aci as browser_aci
from browser.interpreter_ai import BrowserInterpretationError
from tools.browser_aci import extract_structured_data, get_page_summary


def test_extract_structured_data_normalizes_search_result_aliases(monkeypatch):
    snapshot = PageSnapshot(
        session_id="search-session",
        tab_id="tab-1",
        url="https://www.google.com/search?q=flats+in+egham",
        title="flats in egham - Google Search",
        generation=1,
        elements=[
            ElementRef(
                ref_id="nav_images",
                generation=1,
                role="link",
                tag="a",
                text="Images",
                href="https://www.google.com/search?tbm=isch&q=flats+in+egham",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Images"),
            ),
            ElementRef(
                ref_id="about_result",
                generation=1,
                role="button",
                tag="button",
                text="About this result",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="button", text="About this result"),
            ),
            ElementRef(
                ref_id="res_rightmove",
                generation=1,
                role="link",
                tag="a",
                text="Rightmove - Flats to rent in Egham",
                href="https://www.rightmove.co.uk/property-to-rent/Egham.html",
                context_text="Property listings with prices and bedroom counts.",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Rightmove - Flats to rent in Egham"),
            ),
            ElementRef(
                ref_id="res_zoopla",
                generation=1,
                role="link",
                tag="a",
                text="Zoopla - Flats and apartments to rent in Egham",
                href="https://www.zoopla.co.uk/to-rent/property/egham/",
                context_text="Latest rental listings in Egham, Surrey.",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Zoopla - Flats and apartments to rent in Egham"),
            ),
        ],
    )
    browser_store.upsert_snapshot(snapshot)

    async def fake_extract(snapshot, *, item_type: str, query: str, max_items: int):
        assert item_type == "results"
        return {
            "page_type": "search_results",
            "items": [
                {
                    "ref_id": "res_rightmove",
                    "label": "Rightmove - Flats to rent in Egham",
                    "href": "https://www.rightmove.co.uk/property-to-rent/Egham.html",
                    "context": "Property listings with prices and bedroom counts.",
                    "rank": 1,
                    "reason": "Primary rental search result",
                },
                {
                    "ref_id": "res_zoopla",
                    "label": "Zoopla - Flats and apartments to rent in Egham",
                    "href": "https://www.zoopla.co.uk/to-rent/property/egham/",
                    "context": "Latest rental listings in Egham, Surrey.",
                    "rank": 2,
                    "reason": "Secondary rental source",
                },
            ],
            "notes": "Filtered to primary property-search results.",
            "confidence": 0.94,
            "_interpreter_model": "gemini-3-flash-preview",
        }

    monkeypatch.setattr(browser_aci, "extract_structured_items_with_flash", fake_extract)

    result = asyncio.run(
        extract_structured_data(
            item_type="search results",
            max_items=10,
            session_id="search-session",
        )
    )
    payload = json.loads(result)

    assert payload["item_type"] == "results"
    assert payload["requested_item_type"] == "search results"
    assert payload["item_count"] == 2
    labels = [item["label"] for item in payload["items"]]
    assert any("Rightmove" in label for label in labels)
    assert any("Zoopla" in label for label in labels)
    assert all("Images" not in label for label in labels)
    assert all("About this result" not in label for label in labels)
    assert payload["interpreter_model"] == "gemini-3-flash-preview"
    assert payload["degraded_mode"] is False


def test_get_page_summary_uses_flash_interpreter(monkeypatch):
    snapshot = PageSnapshot(
        session_id="summary-session",
        tab_id="tab-1",
        url="https://www.google.com/search?q=uk+housing",
        title="uk housing - Google Search",
        generation=3,
        elements=[
            ElementRef(
                ref_id="heading_1",
                generation=3,
                role="heading",
                tag="h1",
                text="UK housing market overview",
                action_types=[],
                fingerprint=ElementFingerprint(role="heading", text="UK housing market overview"),
            ),
            ElementRef(
                ref_id="result_1",
                generation=3,
                role="link",
                tag="a",
                text="Housing market statistics 2025",
                href="https://example.com/stats",
                context_text="Official statistics and costs",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Housing market statistics 2025"),
            ),
        ],
    )
    browser_store.upsert_snapshot(snapshot)

    async def fake_summary(snapshot):
        return {
            "page_type": "search_results",
            "summary": "A search results page about UK housing market statistics.",
            "headings": [{"ref_id": "heading_1", "text": "UK housing market overview", "tag": "h1"}],
            "key_targets": [{"ref_id": "result_1", "label": "Housing market statistics 2025", "reason": "Likely authoritative source"}],
            "confidence": 0.91,
            "_interpreter_model": "gemini-3-flash-preview",
        }

    monkeypatch.setattr(browser_aci, "summarize_page_with_flash", fake_summary)

    result = asyncio.run(get_page_summary(session_id="summary-session"))
    payload = json.loads(result)

    assert payload["page_type"] == "search_results"
    assert payload["summary"].startswith("A search results page")
    assert payload["headings"][0]["ref_id"] == "heading_1"
    assert payload["key_targets"][0]["ref_id"] == "result_1"
    assert payload["interpreter_model"] == "gemini-3-flash-preview"
    assert payload["degraded_mode"] is False


def test_extract_structured_data_surfaces_flash_interpreter_errors(monkeypatch):
    snapshot = PageSnapshot(
        session_id="error-session",
        tab_id="tab-1",
        url="https://example.com/articles",
        title="Example articles",
        generation=2,
        elements=[
            ElementRef(
                ref_id="result_1",
                generation=2,
                role="link",
                tag="a",
                text="Housing market statistics 2025",
                href="https://example.com/stats",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Housing market statistics 2025"),
            ),
        ],
    )
    browser_store.upsert_snapshot(snapshot)

    async def fake_extract(snapshot, *, item_type: str, query: str, max_items: int):
        raise BrowserInterpretationError("Flash unavailable", error_code="flash_unavailable")

    monkeypatch.setattr(browser_aci, "extract_structured_items_with_flash", fake_extract)

    result = asyncio.run(extract_structured_data(item_type="search results", session_id="error-session"))
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error_code"] == "flash_unavailable"
    assert payload["degraded_mode"] is True


def test_extract_structured_data_falls_back_to_deterministic_search_results(monkeypatch):
    snapshot = PageSnapshot(
        session_id="fallback-session",
        tab_id="tab-1",
        url="https://www.google.com/search?q=best+comedy+videos",
        title="best comedy videos - Google Search",
        generation=2,
        elements=[
            ElementRef(
                ref_id="nav_videos",
                generation=2,
                role="link",
                tag="a",
                text="Videos",
                href="https://www.google.com/search?tbm=vid&q=best+comedy+videos",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Videos"),
            ),
            ElementRef(
                ref_id="result_1",
                generation=2,
                role="link",
                tag="a",
                text="Best Stand Up Comedy Specials To Watch",
                href="https://www.youtube.com/watch?v=abc123",
                context_text="A curated comedy video list on YouTube.",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Best Stand Up Comedy Specials To Watch"),
            ),
            ElementRef(
                ref_id="result_2",
                generation=2,
                role="link",
                tag="a",
                text="Classic Comedy Sketches Compilation",
                href="https://www.youtube.com/watch?v=def456",
                context_text="A second comedy recommendation.",
                action_types=["click"],
                fingerprint=ElementFingerprint(role="link", text="Classic Comedy Sketches Compilation"),
            ),
        ],
    )
    browser_store.upsert_snapshot(snapshot)

    async def fake_extract(snapshot, *, item_type: str, query: str, max_items: int):
        raise BrowserInterpretationError("Flash timed out", error_code="flash_timeout")

    monkeypatch.setattr(browser_aci, "extract_structured_items_with_flash", fake_extract)

    result = asyncio.run(
        extract_structured_data(
            item_type="search results",
            query="best comedy videos",
            session_id="fallback-session",
        )
    )
    payload = json.loads(result)

    assert payload["item_count"] == 2
    assert payload["degraded_mode"] is True
    assert payload["interpreter_model"] == "deterministic-search-resolver"
    assert all("Videos" not in item["label"] for item in payload["items"])
