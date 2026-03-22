"""
books_api.py — Google Books API integration

Enriches raw book data (title + author from VLM) with full metadata:
    - Confirmed title and author
    - Year
    - ISBN-13 (preferred) or ISBN-10
    - Genre (first category from Google Books)
    - Page count
    - Cover image URL

Uses the free Google Books API (no API key required).
Enrichment runs concurrently for speed.

API docs: https://developers.google.com/books/docs/v1/reference/volumes/list
"""

import asyncio
import logging
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
MAX_CONCURRENT = 5
REQUEST_TIMEOUT = 10.0


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw book dicts with metadata from Google Books API.

    Args:
        raw_books: List of dicts with 'title' and 'author' keys (from VLM).

    Returns:
        List of enriched book dicts with all catalog fields.
        Books that can't be found are included with minimal info.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def enrich_one(book: dict) -> dict:
        async with semaphore:
            return await _enrich_book(book)

    tasks = [enrich_one(book) for book in raw_books]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for book, result in zip(raw_books, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to enrich '{book.get('title')}': {result}")
            # Include book with minimal info
            enriched.append(_minimal_book(book))
        else:
            enriched.append(result)

    return enriched


async def _enrich_book(raw_book: dict) -> dict:
    """
    Look up a single book on Google Books API.

    Strategy:
    1. Search by title + author
    2. Find the best-matching result (score by title/author similarity)
    3. Extract metadata
    """
    title = raw_book.get("title", "").strip()
    author = raw_book.get("author", "").strip()

    if not title:
        return _minimal_book(raw_book)

    # Build query
    query = f'intitle:"{title}"'
    if author:
        query += f' inauthor:"{author}"'

    params = {
        "q": query,
        "maxResults": 5,
        "fields": "items(volumeInfo(title,authors,publishedDate,industryIdentifiers,categories,pageCount,imageLinks))",
        "langRestrict": "",  # No language filter
    }

    url = f"{BOOKS_API_URL}?{urlencode(params)}"
    logger.debug(f"Google Books query: {url}")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Google Books API error for '{title}': {e}")
            return _minimal_book(raw_book)

    data = response.json()
    items = data.get("items", [])

    if not items:
        logger.info(f"No Google Books results for: {title!r}")
        return _minimal_book(raw_book)

    # Find best match
    best = _find_best_match(title, author, items)
    info = best.get("volumeInfo", {})

    return _extract_metadata(info)


def _find_best_match(title: str, author: str, items: list[dict]) -> dict:
    """Score each result by how well it matches the query."""
    title_lower = title.lower()
    author_lower = author.lower()

    def score(item: dict) -> int:
        info = item.get("volumeInfo", {})
        item_title = info.get("title", "").lower()
        item_authors = " ".join(info.get("authors", [])).lower()

        s = 0
        # Title match
        if title_lower == item_title:
            s += 10
        elif title_lower in item_title or item_title in title_lower:
            s += 5
        # Author match
        if author_lower and author_lower in item_authors:
            s += 5
        # Has ISBN
        if info.get("industryIdentifiers"):
            s += 1

        return s

    return max(items, key=score)


def _extract_metadata(info: dict) -> dict:
    """Extract catalog fields from a Google Books volumeInfo dict."""
    # Title
    title = info.get("title", "")

    # Author(s)
    authors = info.get("authors", [])
    author = ", ".join(authors) if authors else ""

    # Year (publishedDate can be "2023", "2023-05", "2023-05-15")
    published_date = info.get("publishedDate", "")
    year = published_date[:4] if published_date else ""

    # ISBN (prefer ISBN-13)
    isbn = ""
    for id_info in info.get("industryIdentifiers", []):
        if id_info.get("type") == "ISBN_13":
            isbn = id_info.get("identifier", "")
            break
    if not isbn:
        for id_info in info.get("industryIdentifiers", []):
            if id_info.get("type") == "ISBN_10":
                isbn = id_info.get("identifier", "")
                break

    # Genre (first category)
    categories = info.get("categories", [])
    genre = categories[0] if categories else ""

    # Pages
    pages = info.get("pageCount", "")

    # Cover URL (prefer thumbnail, fall back to smallThumbnail)
    image_links = info.get("imageLinks", {})
    cover_url = image_links.get("thumbnail", "") or image_links.get("smallThumbnail", "")

    return {
        "Title": title,
        "Author": author,
        "Year": str(year) if year else "",
        "ISBN": isbn,
        "Genre": genre,
        "Pages": str(pages) if pages else "",
        "Cover URL": cover_url,
    }


def _minimal_book(raw_book: dict) -> dict:
    """Create a minimal catalog entry from raw VLM data."""
    return {
        "Title": raw_book.get("title", ""),
        "Author": raw_book.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }
