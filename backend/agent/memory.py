"""
Moonwalk — Memory System
=========================
Short-term: conversation turns (in-memory + persisted sessions)
Working:    action log, entity ledger, session goal (current task context)
Long-term:  user profile with auto-extracted facts
Background: recurring tasks (persisted JSON)
"""

import json
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List


# ── Storage directory ──
MOONWALK_DIR = os.path.expanduser("~/.moonwalk")
SESSIONS_DIR = os.path.join(MOONWALK_DIR, "sessions")


def _ensure_dir():
    os.makedirs(MOONWALK_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  Short-Term Memory (conversation history)
# ═══════════════════════════════════════════════════════════════

class ConversationMemory:
    """Keeps the last N conversation turns in memory with optional disk persistence."""

    def __init__(self, max_turns: int = 20, idle_timeout: float = 300.0, persist: bool = True):
        self._turns: list[dict] = []
        self._max_turns = max_turns
        self._idle_timeout = idle_timeout  # seconds before auto-clear
        self._last_activity: float = time.time()
        self._persist = persist
        self._session_id: str = uuid.uuid4().hex[:12]
        self._session_summary: str = ""

        # Try to resume a recent session
        if persist:
            _ensure_dir()
            self._try_resume_session()

    def _try_resume_session(self, resume_window: float = 1800.0):
        """Load the most recent session if it's within the resume window."""
        try:
            sessions = []
            for fname in os.listdir(SESSIONS_DIR):
                if fname.endswith(".json"):
                    fpath = os.path.join(SESSIONS_DIR, fname)
                    mtime = os.path.getmtime(fpath)
                    sessions.append((mtime, fpath, fname))
            if not sessions:
                return
            sessions.sort(reverse=True)
            most_recent_time, most_recent_path, fname = sessions[0]

            if time.time() - most_recent_time <= resume_window:
                with open(most_recent_path, "r") as f:
                    data = json.load(f)
                self._turns = data.get("turns", [])
                self._session_id = data.get("session_id", fname.replace(".json", ""))
                self._session_summary = data.get("summary", "")
                self._last_activity = most_recent_time
        except Exception:
            pass  # Start fresh if anything fails

    def _save_session(self):
        """Persist current session to disk."""
        if not self._persist or not self._turns:
            return
        try:
            _ensure_dir()
            path = os.path.join(SESSIONS_DIR, f"{self._session_id}.json")
            data = {
                "session_id": self._session_id,
                "turns": self._turns,
                "summary": self._session_summary,
                "updated_at": time.time(),
            }
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def add_user(self, text: str, context_summary: str = ""):
        """Add a user turn."""
        self._check_timeout()
        self._last_activity = time.time()
        content = text
        if context_summary:
            content = f"{text}\n\n{context_summary}"
        self._turns.append({"role": "user", "parts": [{"text": content}]})
        self._trim()
        self._save_session()

    def add_model(self, text: str):
        """Add a model response turn."""
        self._last_activity = time.time()
        self._turns.append({"role": "model", "parts": [{"text": text}]})
        self._trim()
        self._save_session()

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

    def get_session_summary(self) -> str:
        """Get the summary of the current or previous session."""
        return self._session_summary

    def set_session_summary(self, summary: str):
        """Set a session summary (called by the summarizer)."""
        self._session_summary = summary
        self._save_session()

    def clear(self):
        """Clear all conversation history."""
        self._turns.clear()

    def start_new_session(self):
        """Force start a new session (e.g., user explicitly says 'new conversation')."""
        self._session_id = uuid.uuid4().hex[:12]
        self._turns.clear()
        self._session_summary = ""
        self._last_activity = time.time()

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
#  Working Memory (current-session context)
# ═══════════════════════════════════════════════════════════════

@dataclass
class ActionEntry:
    """One tool call + result logged in working memory."""
    tool: str
    args_summary: str  # short human-readable args
    result_summary: str  # short result
    timestamp: float = 0.0
    success: bool = True


class WorkingMemory:
    """
    Tracks the *current session's* operational context — what the agent has
    done, what it has seen, and what it's working toward.  Unlike conversation
    memory (which stores raw turns for the LLM), working memory is a
    *structured* layer that feeds compact, high-signal context into the
    system prompt so the agent never repeats or forgets mid-session work.
    """

    def __init__(self, max_actions: int = 40, max_entities: int = 60):
        # Ordered log of recent tool calls
        self._actions: deque[ActionEntry] = deque(maxlen=max_actions)
        # Entity ledger: URLs visited, docs created, files opened, etc.
        self._entities: dict[str, dict] = {}  # key → {type, value, detail, ts}
        self._max_entities = max_entities
        # Session goal — high-level description of what we're doing
        self._session_goal: str = ""
        # Open tabs ledger (populated by browser tools)
        self._opened_urls: list[str] = []
        # Research snippets — content extracted during research tasks
        self._research_snippets: list[dict] = []  # [{source, title, content, ts}]
        self._max_research_snippets: int = 12
        # Search leads — candidate sources discovered from search results
        self._search_leads: list[dict] = []  # [{query, title, url, domain, snippet, ts, opened}]
        self._max_search_leads: int = 20
        # Last successful text the agent typed into the UI
        self._last_typed_text: str = ""

    # ── Action log ──

    def log_action(self, tool: str, args: dict, result: str, success: bool = True):
        """Log a tool call and its result."""
        args_summary = self._summarize_args(tool, args)
        result_summary = self._summarize_result(tool, result)
        self._actions.append(ActionEntry(
            tool=tool, args_summary=args_summary,
            result_summary=result_summary,
            timestamp=time.time(), success=success,
        ))
        if success and tool in {"type_text", "type_in_field"}:
            typed_text = str((args or {}).get("text", "")).strip()
            if typed_text:
                self._last_typed_text = typed_text[:500]
        # Auto-extract entities from the action
        self._extract_entities_from_action(tool, args, result)

    def _summarize_args(self, tool: str, args: dict) -> str:
        """Create a compact human-readable summary of tool args."""
        if not args:
            return ""
        # For known tools, pick the most important arg
        key_args = {
            "open_url": "url", "open_app": "app_name", "run_shell": "command",
            "browser_click_match": "query", "browser_type_ref": "text",
            "browser_read_page": "query", "type_text": "text",
            "gdocs_create": "title", "gdocs_append": "text",
            "gsheets_create": "title", "gsheets_write": "values",
            "gmail_send": "to", "web_search": "query",
            "send_response": "message", "await_reply": "message",
        }
        primary_key = key_args.get(tool)
        if primary_key and primary_key in args:
            val = str(args[primary_key])
            return val[:120] if len(val) > 120 else val
        # Generic: take first 2 args
        parts = []
        for k, v in list(args.items())[:2]:
            vs = str(v)[:80]
            parts.append(f"{k}={vs}")
        return ", ".join(parts)

    def _summarize_result(self, tool: str, result: str) -> str:
        """Create a compact summary of a tool result."""
        if not result:
            return ""
        # Strip RESPONSE:/AWAIT: prefixes
        for prefix in ("RESPONSE:", "AWAIT:"):
            if result.startswith(prefix):
                result = result[len(prefix):]
        # Try to parse JSON and extract message
        try:
            data = json.loads(result)
            if isinstance(data, dict):
                msg = data.get("message", data.get("text", data.get("ok", "")))
                return str(msg)[:150]
        except (json.JSONDecodeError, TypeError):
            pass
        return result[:150]

    def _extract_entities_from_action(self, tool: str, args: dict, result: str):
        """Auto-extract notable entities from a tool call."""
        now = time.time()

        # URLs
        if tool == "open_url" and "url" in args:
            url = args["url"]
            self._record_entity(f"url:{url}", "url_opened", url, now=now)
            if url not in self._opened_urls:
                self._opened_urls.append(url)
            self._mark_search_lead_opened(url)

        # Documents created
        if tool in ("gdocs_create", "gsheets_create", "gslides_create"):
            title = args.get("title", "untitled")
            try:
                data = json.loads(result)
                doc_url = data.get("url", data.get("spreadsheet_url", ""))
                self._record_entity(f"doc:{title}", "doc_created", title, detail=doc_url, now=now)
            except Exception:
                self._record_entity(f"doc:{title}", "doc_created", title, now=now)

        # Files
        if tool in ("read_file", "write_file", "create_file"):
            path = args.get("path", args.get("file_path", ""))
            if path:
                self._record_entity(f"file:{path}", "file_touched", path, now=now)

        # Browser pages read
        if tool == "browser_read_page":
            try:
                data = json.loads(result)
                page_url = data.get("url", "")
                page_title = data.get("title", "")
                if page_url:
                    self._record_entity(f"page:{page_url}", "page_read", page_title or page_url, detail=page_url, now=now)
            except Exception:
                pass

        # Web searches
        if tool == "web_search":
            query = args.get("query", "")
            if query:
                self._record_entity(f"search:{query}", "web_search", query, now=now)

        # Search result leads surfaced through the gateway
        if tool == "get_web_information":
            try:
                data = json.loads(result)
            except Exception:
                data = {}
            if isinstance(data, dict):
                target_type = str(data.get("target_type", "")).strip().lower()
                if target_type == "search_results" and isinstance(data.get("items"), list):
                    self.log_search_leads(
                        query=str(data.get("query") or args.get("query") or "").strip(),
                        items=data.get("items", []),
                    )

        # Tab switching
        if tool == "browser_switch_tab":
            url = args.get("url", "")
            if url:
                self._record_entity(f"tab_switch:{url}", "tab_switch", url, now=now)

    def _record_entity(self, key: str, etype: str, value: str, detail: str = "", now: float = 0.0):
        """Record an entity in the ledger."""
        self._entities[key] = {
            "type": etype, "value": value, "detail": detail,
            "ts": now or time.time(),
        }
        # Evict oldest if over limit
        if len(self._entities) > self._max_entities:
            oldest_key = min(self._entities, key=lambda k: self._entities[k]["ts"])
            del self._entities[oldest_key]

    # ── Session goal ──

    def set_session_goal(self, goal: str):
        self._session_goal = goal

    def get_session_goal(self) -> str:
        return self._session_goal

    # ── Queries ──

    def get_recent_actions(self, n: int = 10) -> List[ActionEntry]:
        """Return the N most recent actions."""
        return list(self._actions)[-n:]

    def get_opened_urls(self) -> list[str]:
        return list(self._opened_urls)

    def get_last_typed_text(self) -> str:
        return self._last_typed_text

    def has_visited_url(self, url: str) -> bool:
        """Check if we've already opened or read this URL in this session."""
        url_lower = url.lower().rstrip("/")
        for key, entity in self._entities.items():
            if entity["type"] in ("url_opened", "page_read", "tab_switch"):
                if url_lower in entity["value"].lower() or entity["value"].lower() in url_lower:
                    return True
        return False

    def get_entities_by_type(self, etype: str) -> list[dict]:
        return [v for v in self._entities.values() if v["type"] == etype]

    # ── Research snippet tracking ──

    def log_research_snippet(self, source: str, title: str, content: str, tool: str = ""):
        """Store a research snippet extracted during browsing/reading."""
        if not content or len(content.strip()) < 40:
            return  # Too short to be useful research

        # Skip search engine pages and homepages — not real research
        _src = (source or "").lower()
        _junk_patterns = (
            "google.com/webhp", "google.com/search", "bing.com/search",
            "duckduckgo.com/", "yahoo.com/search", "about:blank", "newtab",
        )
        _junk_exact = (
            "https://www.google.com", "http://www.google.com",
            "https://google.com", "https://www.bing.com",
        )
        if any(p in _src for p in _junk_patterns) or _src.rstrip("/") in _junk_exact:
            return  # Skip search engine pages

        # Skip content that's mostly browser element references (not readable text)
        if content.count("[mw_") > 3:
            return  # Browser chrome, not research content

        normalized_source = (source or "").strip().rstrip("/")
        normalized_title = (title or "").strip().lower()
        normalized_content = " ".join(content.split()).strip().lower()
        content_key = normalized_content[:600]

        for existing in self._research_snippets:
            existing_source = str(existing.get("source", "")).strip().rstrip("/")
            existing_title = str(existing.get("title", "")).strip().lower()
            existing_content = " ".join(str(existing.get("content", "")).split()).strip().lower()[:600]

            same_source = bool(normalized_source and existing_source and existing_source == normalized_source)
            same_title = bool(normalized_title and existing_title and existing_title == normalized_title)
            same_content = bool(content_key and existing_content and existing_content == content_key)
            if same_content or (same_source and (same_title or content_key == existing_content)):
                # Prefer the richer version of the same source/snippet.
                if len(content) > len(str(existing.get("content", ""))):
                    existing["content"] = content.strip()[:2000]
                    existing["title"] = title or existing.get("title", "")
                    existing["tool"] = tool or existing.get("tool", "")
                    existing["ts"] = time.time()
                return

        snippet = {
            "source": source or "unknown",
            "title": title or "",
            "content": content.strip()[:2000],  # Cap per-snippet
            "tool": tool,
            "ts": time.time(),
        }
        self._research_snippets.append(snippet)
        # Evict oldest if over limit
        while len(self._research_snippets) > self._max_research_snippets:
            self._research_snippets.pop(0)
        print(f"[WorkingMemory] 📚 Stored research snippet from '{source}' ({len(content)} chars)")

    def get_research_snippets(self) -> list[dict]:
        """Return all stored research snippets."""
        return list(self._research_snippets)

    def log_search_leads(self, query: str, items: list[dict]):
        """Store canonical search-result leads without treating them as research snippets."""
        if not isinstance(items, list):
            return
        query_text = (query or "").strip()
        existing_urls = {str(lead.get("url", "")).strip() for lead in self._search_leads}
        added = 0
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("href", "")).strip()
            title = str(item.get("label", "")).strip()
            if not url or not title:
                continue
            if url in existing_urls:
                continue
            domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url)).split("/", 1)[0].strip().lower()
            snippet = str(item.get("context", "")).strip()[:240]
            lead = {
                "query": query_text,
                "title": title[:220],
                "url": url,
                "domain": domain,
                "snippet": snippet,
                "ts": time.time(),
                "opened": False,
            }
            self._search_leads.append(lead)
            existing_urls.add(url)
            added += 1
        while len(self._search_leads) > self._max_search_leads:
            self._search_leads.pop(0)
        if added:
            print(f"[WorkingMemory] 🔎 Stored {added} search lead(s) for query '{query_text[:80]}'")

    def _mark_search_lead_opened(self, url: str):
        target = (url or "").strip().rstrip("/")
        if not target:
            return
        for lead in self._search_leads:
            lead_url = str(lead.get("url", "")).strip().rstrip("/")
            if lead_url and (lead_url == target or target.startswith(lead_url) or lead_url.startswith(target)):
                lead["opened"] = True

    def get_search_leads(self) -> list[dict]:
        return list(self._search_leads)

    def get_search_lead_summary(self, limit: int = 5) -> str:
        leads = self._search_leads[-max(1, limit):]
        if not leads:
            return ""
        lines = [f"[Search Leads — {len(self._search_leads)} total]"]
        for idx, lead in enumerate(leads, 1):
            title = str(lead.get("title", "")).strip()
            domain = str(lead.get("domain", "")).strip()
            query = str(lead.get("query", "")).strip()
            status = "opened" if lead.get("opened") else "unopened"
            row = " | ".join(part for part in (title, domain, query, status) if part)
            if row:
                lines.append(f"  [{idx}] {row[:220]}")
        return "\n".join(lines)

    def get_research_summary(self) -> str:
        """Return a compact summary of stored research for logging."""
        if not self._research_snippets:
            return ""
        lines = [f"[Research Collected — {len(self._research_snippets)} snippet(s)]"]
        for i, snippet in enumerate(self._research_snippets, 1):
            src = snippet.get("source", "?")
            title = snippet.get("title", "")
            content = snippet.get("content", "")
            preview = content[:150].replace("\n", " ") + ("..." if len(content) > 150 else "")
            header = f"  [{i}] {title}" if title else f"  [{i}] {src}"
            lines.append(f"{header}")
            lines.append(f"      Source: {src}")
            lines.append(f"      Preview: {preview}")
        return "\n".join(lines)

    # ── Prompt injection ──

    def to_prompt_string(self) -> str:
        """Format working memory as a compact context block for the system prompt."""
        sections = []

        # Session goal
        if self._session_goal:
            sections.append(f"[Session Goal] {self._session_goal}")

        # Recent actions (last 8)
        recent = self.get_recent_actions(8)
        if recent:
            lines = ["[Recent Actions — what you already did this session]"]
            for a in recent:
                status = "✓" if a.success else "✗"
                summary = a.args_summary
                if a.result_summary and a.result_summary != summary:
                    summary += f" → {a.result_summary}"
                lines.append(f"  {status} {a.tool}({summary})")
            sections.append("\n".join(lines))

        # Key entities (URLs opened, docs created, files touched)
        doc_entities = [e for e in self._entities.values() if e["type"] == "doc_created"]
        url_entities = [e for e in self._entities.values() if e["type"] == "url_opened"]
        search_entities = [e for e in self._entities.values() if e["type"] == "web_search"]

        entity_lines = []
        if doc_entities:
            entity_lines.append("  Documents created: " + ", ".join(e["value"] for e in doc_entities))
        if url_entities:
            entity_lines.append("  URLs opened: " + ", ".join(e["value"] for e in url_entities[-5:]))
        if search_entities:
            entity_lines.append("  Searches: " + ", ".join(e["value"] for e in search_entities[-3:]))

        if entity_lines:
            sections.append("[Session Entities]\n" + "\n".join(entity_lines))

        # Research snippets (if any)
        research_summary = self.get_research_summary()
        if research_summary:
            sections.append(research_summary)

        lead_summary = self.get_search_lead_summary()
        if lead_summary:
            sections.append(lead_summary)

        return "\n\n".join(sections) if sections else ""

    def clear(self):
        """Clear all working memory."""
        self._actions.clear()
        self._entities.clear()
        self._session_goal = ""
        self._last_typed_text = ""
        self._opened_urls.clear()
        self._research_snippets.clear()


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
#  User Profile (auto-extracted facts from conversations)
# ═══════════════════════════════════════════════════════════════

class UserProfile:
    """
    Persistent user profile that automatically extracts and stores facts
    from user messages. Facts are categorized and used to personalize
    agent behavior across sessions.
    """

    # Regex patterns to extract facts from user statements
    FACT_PATTERNS = [
        # "My X is Y" / "My X lives at Y"
        (r'\bmy\s+([\w\s]+?)\s+(?:is|are|lives?\s+(?:at|in))\s+(.+?)(?:\.|$)',
         lambda m: (m.group(1).strip().lower(), m.group(2).strip())),
        # "I use X" / "I prefer X"
        (r'\bi\s+(?:use|prefer|like|work with)\s+(.+?)(?:\s+for\s+(.+?))?(?:\.|$)',
         lambda m: (f"preferred_{m.group(2).strip().lower()}" if m.group(2) else "preferred_tool",
                     m.group(1).strip())),
        # "My preferred X is Y"
        (r'\bmy\s+preferred\s+([\w\s]+?)\s+is\s+(.+?)(?:\.|$)',
         lambda m: (f"preferred_{m.group(1).strip().lower()}", m.group(2).strip())),
        # "Remember that X"
        (r'\bremember\s+that\s+(.+?)(?:\.|$)',
         lambda m: ("remembered_fact", m.group(1).strip())),
        # "Projects live in X" / "projects are in X"
        (r'\bprojects?\s+(?:live|are)\s+(?:in|at)\s+([~/]\S+)',
         lambda m: ("projects_directory", m.group(1).strip())),
    ]

    def __init__(self):
        _ensure_dir()
        self._path = os.path.join(MOONWALK_DIR, "user_profile.json")
        self._profile: dict = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    return json.load(f)
            except Exception:
                return {"facts": {}, "interaction_count": 0, "first_seen": time.time()}
        return {"facts": {}, "interaction_count": 0, "first_seen": time.time()}

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._profile, f, indent=2)
        except Exception:
            pass

    def extract_facts(self, user_text: str) -> List[tuple]:
        """
        Extract facts from a user message and store them.
        Returns list of (key, value) pairs that were extracted.
        """
        extracted = []
        text_lower = user_text.lower()

        for pattern, extractor in self.FACT_PATTERNS:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                try:
                    key, value = extractor(match)
                    key = re.sub(r'\s+', '_', key)  # normalize spaces to underscores
                    self._profile["facts"][key] = {
                        "value": value,
                        "source": user_text[:100],
                        "updated_at": time.time(),
                    }
                    extracted.append((key, value))
                except Exception:
                    continue

        if extracted:
            self._save()

        # Track interaction count
        self._profile["interaction_count"] = self._profile.get("interaction_count", 0) + 1
        if self._profile["interaction_count"] % 10 == 0:
            self._save()  # Periodic save of interaction count

        return extracted

    def get_fact(self, key: str) -> Optional[str]:
        """Get a specific fact value."""
        fact = self._profile.get("facts", {}).get(key)
        return fact["value"] if fact else None

    def get_all_facts(self) -> dict:
        """Get all stored facts."""
        return {k: v["value"] for k, v in self._profile.get("facts", {}).items()}

    def to_prompt_string(self) -> str:
        """Format user profile as context for the LLM system prompt."""
        facts = self.get_all_facts()
        if not facts:
            return ""
        lines = ["[User Profile — remembered facts about this user]"]
        for key, value in facts.items():
            readable_key = key.replace("_", " ").title()
            lines.append(f"  • {readable_key}: {value}")
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
