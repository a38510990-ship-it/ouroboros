"""
books_api.py — Google Books API integration

Enriches raw book data (title + author from VLM) with detailed information:
    - Full title, author, year
    - ISBN (prefers ISBN-13)
    - Genre / subject categories
    - Page count
    - Cover image URL

Uses the Google Books API (free, no authentication required).
Enriches all books concurrently for speed.

API docs: https://developers.google.com/books/docs/v1/reference/volumes/list
"""

import asyncio
import logging
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
REQUEST_TIMEOUT = 15.0
MAX_CONCURRENT = 5  # Parallel requests limit


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw books with Google Books API data.

    Args:
        raw_books: List of dicts with 'title' and 'author' keys (from VLM)

    Returns:
        List of enriched book dicts with all catalog fields.
        Books that couldn't be found still appear with partial data.
    """
    if not raw_books:
        return []

    # Limit concurrency to avoid rate limiting
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            _enrich_one(client, semaphore, book)
            for book in raw_books
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for raw, result in zip(raw_books, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to enrich '{raw.get('title', '?')}': {result}")
            # Use raw data as fallback
            enriched.append(_raw_to_catalog(raw))
        else:
            enriched.append(result)

    return enriched


async def _enrich_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    raw_book: dict,
) -> dict:
    """
    Enrich a single book via Google Books API.

    Search strategy:
    1. Search by title + author (most specific)
    2. If no results, search by title only
    3. If still no results, return raw data as-is
    """
    title = raw_book.get("title", "").strip()
    author = raw_book.get("author", "").strip()

    if not title:
        return _raw_to_catalog(raw_book)

    async with semaphore:
        # Try title + author first
        if author:
            result = await _search(client, title=title, author=author)
            if result:
                logger.info(f"Found: '{title}' by '{author}'")
                return result

        # Fallback: title only
        result = await _search(client, title=title)
        if result:
            logger.info(f"Found (title only): '{title}'")
            return result

        logger.info(f"Not found in Google Books: '{title}'")
        return _raw_to_catalog(raw_book)


async def _search(
    client: httpx.AsyncClient,
    title: str,
    author: str = "",
) -> dict | None:
    """
    Search Google Books API and return the best matching book.

    Returns None if no match is found.
    """
    # Build query
    query_parts = [f'intitle:"{title}"']
    if author:
        query_parts.append(f'inauthor:"{author}"')
    query = " ".join(query_parts)

    params = {
        "q": query,
        "maxResults": 5,
        "fields": "items(volumeInfo)",
        "langRestrict": "",  # All languages
    }

    try:
        response = await client.get(BOOKS_API_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(f"Google Books API error for '{title}': {e}")
        return None

    data = response.json()
    items = data.get("items", [])

    if not items:
        return None

    # Pick the best matching item
    best = _pick_best_match(items, title=title, author=author)
    if best is None:
        return None

    return _parse_volume(best["volumeInfo"])


def _pick_best_match(items: list[dict], title: str, author: str) -> dict | None:
    """
    Score and rank API results, return the best match.

    Scoring:
    - +2 if title contains the search title (case-insensitive)
    - +1 if authors field contains the search author
    - Prefer items with ISBN

    Returns the highest-scoring item, or the first item if no scores.
    """
    if not items:
        return None

    title_lower = title.lower()
    author_lower = author.lower() if author else ""

    scored = []
    for item in items:
        info = item.get("volumeInfo", {})
        score = 0

        # Title match
        api_title = info.get("title", "").lower()
        if title_lower in api_title or api_title in title_lower:
            score += 2

        # Author match
        if author_lower:
            api_authors = " ".join(info.get("authors", [])).lower()
            if author_lower in api_authors:
                score += 1

        # Prefer items with ISBN
        identifiers = info.get("industryIdentifiers", [])
        if any(x.get("type") in ("ISBN_13", "ISBN_10") for x in identifiers):
            score += 1

        scored.append((score, item))

    # Sort by score descending, return best
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _parse_volume(info: dict) -> dict:
    """
    Parse a Google Books volumeInfo dict into our catalog format.

    Output fields: Title, Author, Year, ISBN, Genre, Pages, Cover URL
    """
    # Title
    title = info.get("title", "")
    subtitle = info.get("subtitle", "")
    full_title = f"{title}: {subtitle}" if subtitle else title

    # Authors
    authors = info.get("authors", [])
    author = ", ".join(authors) if authors else ""

    # Year (published date: "YYYY", "YYYY-MM", or "YYYY-MM-DD")
    published = info.get("publishedDate", "")
    year = published[:4] if published else ""

    # ISBN — prefer ISBN-13, fall back to ISBN-10
    isbn = ""
    identifiers = info.get("industryIdentifiers", [])
    isbn_13 = next((x["identifier"] for x in identifiers if x.get("type") == "ISBN_13"), "")
    isbn_10 = next((x["identifier"] for x in identifiers if x.get("type") == "ISBN_10"), "")
    isbn = isbn_13 or isbn_10

    # Genre / categories
    categories = info.get("categories", [])
    genre = ", ".join(categories) if categories else ""

    # Pages
    pages = str(info.get("pageCount", "")) if info.get("pageCount") else ""

    # Cover URL
    image_links = info.get("imageLinks", {})
    cover_url = (
        image_links.get("thumbnail")
        or image_links.get("smallThumbnail")
        or ""
    )
    # Use HTTPS
    cover_url = cover_url.replace("http://", "https://")

    return {
        "Title": full_title,
        "Author": author,
        "Year": year,
        "ISBN": isbn,
        "Genre": genre,
        "Pages": pages,
        "Cover URL": cover_url,
    }


def _raw_to_catalog(raw: dict) -> dict:
    """Convert a raw VLM book (title + author) to catalog format with empty fields."""
    return {
        "Title": raw.get("title", ""),
        "Author": raw.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }
