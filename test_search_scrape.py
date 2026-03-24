#!/usr/bin/env python3
"""
Test: DDG HTML scraping via https://html.duckduckgo.com/html/

DuckDuckGo has a no-JS HTML version that returns real organic search results.
This is fundamentally different from the Instant Answer API (which returns only
definitions/facts). The HTML version returns real web snippets.

Usage:
    python3 test_search_scrape.py
"""

import json
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import List, Dict


# ---------------------------------------------------------------------------
# HTML parser for DDG results
# ---------------------------------------------------------------------------

class DDGResultParser(HTMLParser):
    """
    Parse DuckDuckGo HTML results page.
    
    Each result is a <div class="result"> containing:
      - <a class="result__a">  → title + URL
      - <a class="result__snippet"> → snippet text
    """

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current: Dict[str, str] = {}
        self._capture_title = False
        self._capture_snippet = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")

        if tag == "div" and "result" in classes.split() and "result--more" not in classes:
            self._current = {}

        elif tag == "a" and "result__a" in classes.split():
            href = attrs_dict.get("href", "")
            # DDG wraps real URLs — extract from uddg= param
            if "uddg=" in href:
                try:
                    parsed = urllib.parse.urlparse(href)
                    params = urllib.parse.parse_qs(parsed.query)
                    self._current["url"] = urllib.parse.unquote(params["uddg"][0])
                except Exception:
                    self._current["url"] = href
            else:
                self._current["url"] = href
            self._capture_title = True

        elif tag == "a" and "result__snippet" in classes.split():
            self._capture_snippet = True

    def handle_endtag(self, tag):
        if tag == "a":
            if self._capture_title:
                self._capture_title = False
            if self._capture_snippet:
                self._capture_snippet = False
                # Save result when snippet ends
                if self._current.get("title") and self._current.get("snippet"):
                    self.results.append(dict(self._current))
                    self._current = {}

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._capture_title:
            self._current["title"] = self._current.get("title", "") + text
        elif self._capture_snippet:
            self._current["snippet"] = self._current.get("snippet", "") + text


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def ddg_html_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Scrape DuckDuckGo HTML version for organic search results.
    Returns list of {title, url, snippet} dicts.
    """
    url = "https://html.duckduckgo.com/html/"
    data = urllib.parse.urlencode({"q": query, "b": "", "kl": "wt-wt"}).encode()
    
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Accept": "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    
    parser = DDGResultParser()
    parser.feed(html)
    return parser.results[:max_results]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    queries = [
        "Claude Sonnet 4 release date 2025",
        "OpenRouter pricing per token 2025",
    ]
    
    all_results = {}
    
    for query in queries:
        print(f"\n{'='*60}")
        print(f"Query: {query}")
        print('='*60)
        
        try:
            results = ddg_html_search(query, max_results=5)
            all_results[query] = results
            
            if not results:
                print("  [!] No results parsed — DDG may have changed HTML structure")
                continue
            
            for i, r in enumerate(results, 1):
                print(f"\n  [{i}] {r.get('title', '(no title)')}")
                print(f"      URL: {r.get('url', '(no url)')}")
                snippet = r.get('snippet', '(no snippet)')
                # Wrap long snippets
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                print(f"      Snippet: {snippet}")
                
        except Exception as e:
            print(f"  [ERROR] {e}")
            all_results[query] = {"error": str(e)}
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
    total = sum(len(v) if isinstance(v, list) else 0 for v in all_results.values())
    print(f"Total results fetched: {total}")
    print(f"Queries tested: {len(queries)}")
    
    if total > 0:
        print("\n✅ DDG HTML scraping WORKS — real organic results available!")
        print("   This can replace the limited Instant Answer API as provider 3.")
    else:
        print("\n⚠️  No results — DDG may have changed structure or is blocking.")
    
    # Save raw JSON for inspection
    output_path = "/tmp/ddg_scrape_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved to: {output_path}")


if __name__ == "__main__":
    main()
