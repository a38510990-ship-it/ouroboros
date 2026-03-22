"""Web search tool — multi-provider with graceful fallback.

Provider priority:
  1. OpenAI Responses API  (requires OPENAI_API_KEY)
  2. Google Custom Search  (requires GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX)
  3. DuckDuckGo Instant Answer API  (no key, always available, limited results)

The first provider that has its prerequisites set is used.
If all fail, a structured error is returned instead of raising.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------

def _try_openai(query: str) -> Optional[str]:
    """Search via OpenAI Responses API. Returns JSON string or None if key missing."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(
            model=os.environ.get("OUROBOROS_WEBSEARCH_MODEL", "gpt-4o-mini"),
            tools=[{"type": "web_search"}],
            tool_choice="auto",
            input=query,
        )
        d = resp.model_dump()
        text = ""
        for item in d.get("output", []) or []:
            if item.get("type") == "message":
                for block in item.get("content", []) or []:
                    if block.get("type") in ("output_text", "text"):
                        text += block.get("text", "")
        return json.dumps(
            {"answer": text or "(no answer)", "provider": "openai"},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": repr(e), "provider": "openai"}, ensure_ascii=False)


def _try_google_cse(query: str) -> Optional[str]:
    """Search via Google Custom Search JSON API.

    Requires env vars:
      GOOGLE_CSE_API_KEY  — your Google API key
      GOOGLE_CSE_CX       — your Custom Search Engine ID
    """
    api_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    cx = os.environ.get("GOOGLE_CSE_CX", "")
    if not api_key or not cx:
        return None
    try:
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode({
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": 5,
        })
        url = f"https://www.googleapis.com/customsearch/v1?{params}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        items = data.get("items", [])
        if not items:
            return json.dumps(
                {"answer": "(no results)", "provider": "google_cse"},
                ensure_ascii=False,
            )

        snippets = [
            f"**{it.get('title', '')}**\n{it.get('snippet', '')}\n{it.get('link', '')}"
            for it in items
        ]
        answer = "\n\n".join(snippets)
        return json.dumps(
            {"answer": answer, "provider": "google_cse"},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": repr(e), "provider": "google_cse"}, ensure_ascii=False)


def _try_duckduckgo(query: str) -> str:
    """Search via DuckDuckGo Instant Answer API (no key required).

    Best-effort fallback — DDG instant answers are concise but may return
    empty results for highly specific queries. Always returns a result.
    """
    try:
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
            "skip_disambig": "1",
        })
        url = f"https://api.duckduckgo.com/?{params}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        abstract = data.get("Abstract", "")
        answer = data.get("Answer", "")
        related = [r.get("Text", "") for r in (data.get("RelatedTopics") or [])[:3]]

        parts: list[str] = []
        if answer:
            parts.append(answer)
        if abstract:
            parts.append(abstract)
        if related:
            parts.append("Related:\n" + "\n".join(f"• {r}" for r in related if r))

        text = (
            "\n\n".join(parts)
            if parts
            else "(no instant answer available — try a different query)"
        )
        return json.dumps(
            {"answer": text, "provider": "duckduckgo"},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return json.dumps(
            {"answer": "(search unavailable)", "error": repr(e), "provider": "duckduckgo"},
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

def _web_search(ctx: ToolContext, query: str) -> str:
    """Try providers in priority order; return the first successful result."""
    # Provider 1: OpenAI (best quality, needs OPENAI_API_KEY)
    result = _try_openai(query)
    if result is not None:
        return result

    # Provider 2: Google Custom Search (good quality, needs GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX)
    result = _try_google_cse(query)
    if result is not None:
        return result

    # Provider 3: DuckDuckGo (no key, always available, limited results)
    return _try_duckduckgo(query)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "web_search",
            {
                "name": "web_search",
                "description": (
                    "Search the web via OpenAI Responses API. Returns JSON with answer + sources."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
            _web_search,
        ),
    ]
