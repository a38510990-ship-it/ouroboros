"""
books_api.py — Google Books API enrichment

Looks up books by title/author and returns enriched metadata:
Title, Author, Year, ISBN, Genre, Pages, Cover URL

Uses the free public Google Books API (no API key required):
    https://developers.google.com/books/docs/v1/reference/volumes/list
"""

import asyncio
import logging
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
REQUEST_TIMEOUT = 10.0
MAX_CONCURRENT = 5  # Max parallel API requests


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw {title, author} dicts with Google Books metadata.

    Args:
        raw_books: List of dicts with 'title' and 'author' keys.

    Returns:
        List of enriched book dicts with all catalog fields.
        Falls back to raw data if lookup fails.
    """
    if not raw_books:
        return []

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            _enrich_one(book, client, semaphore)
            for book in raw_books
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for raw, result in zip(raw_books, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to enrich '{raw.get('title', '?')}': {result}")
            # Fallback: use raw data with empty fields
            enriched.append(_fallback(raw))
        else:
            enriched.append(result)

    return enriched


async def _enrich_one(
    raw_book: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Look up a single book and return enriched metadata."""
    async with semaphore:
        title = raw_book.get("title", "").strip()
        author = raw_book.get("author", "").strip()

        if not title:
            return _fallback(raw_book)

        # Build search query
        query_parts = [f'intitle:"{title}"']
        if author:
            query_parts.append(f'inauthor:"{author}"')
        query = " ".join(query_parts)

        params = {
            "q": query,
            "maxResults": 5,
            "orderBy": "relevance",
            "langRestrict": "en",
            "printType": "books",
        }

        try:
            response = await client.get(
                BOOKS_API_URL,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.debug(f"API error for '{title}': {e}")
            return _fallback(raw_book)

        items = data.get("items", [])
        if not items:
            logger.debug(f"No results for '{title}'")
            return _fallback(raw_book)

        # Pick the best match
        best = _pick_best(items, title, author)
        return _extract_fields(best, raw_book)


def _pick_best(items: list[dict], title: str, author: str) -> dict:
    """
    Score and pick the best matching result.

    Scoring:
    +2 if title matches (case-insensitive)
    +1 if author matches (case-insensitive)
    +1 if has ISBN
    """
    title_lower = title.lower()
    author_lower = author.lower()

    best_item = items[0]
    best_score = -1

    for item in items:
        info = item.get("volumeInfo", {})
        score = 0

        item_title = info.get("title", "").lower()
        if title_lower in item_title or item_title in title_lower:
            score += 2

        item_authors = [a.lower() for a in info.get("authors", [])]
        if author_lower and any(author_lower in a or a in author_lower for a in item_authors):
            score += 1

        identifiers = info.get("industryIdentifiers", [])
        if any(i.get("type") in ("ISBN_13", "ISBN_10") for i in identifiers):
            score += 1

        if score > best_score:
            best_score = score
            best_item = item

    return best_item


def _extract_fields(item: dict, raw_book: dict) -> dict:
    """Extract catalog fields from a Google Books API item."""
    info = item.get("volumeInfo", {})

    # Title (prefer API result, fallback to raw)
    title = info.get("title") or raw_book.get("title", "")

    # Author (prefer API result, fallback to raw)
    authors = info.get("authors", [])
    author = ", ".join(authors) if authors else raw_book.get("author", "")

    # Year
    published = info.get("publishedDate", "")
    year = published[:4] if published else ""  # "2023-04-15" → "2023"

    # ISBN — prefer ISBN-13
    isbn = ""
    for identifier in info.get("industryIdentifiers", []):
        id_type = identifier.get("type", "")
        if id_type == "ISBN_13":
            isbn = identifier.get("identifier", "")
            break
        elif id_type == "ISBN_10" and not isbn:
            isbn = identifier.get("identifier", "")

    # Genre
    categories = info.get("categories", [])
    genre = categories[0] if categories else ""

    # Pages
    pages = str(info.get("pageCount", "")) if info.get("pageCount") else ""

    # Cover URL (use large thumbnail if available)
    image_links = info.get("imageLinks", {})
    cover_url = (
        image_links.get("large")
        or image_links.get("medium")
        or image_links.get("thumbnail")
        or ""
    )

    return {
        "Title": title,
        "Author": author,
        "Year": year,
        "ISBN": isbn,
        "Genre": genre,
        "Pages": pages,
        "Cover URL": cover_url,
    }


def _fallback(raw_book: dict) -> dict:
    """Create a catalog entry from raw VLM data (no enrichment)."""
    return {
        "Title": raw_book.get("title", ""),
        "Author": raw_book.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }
