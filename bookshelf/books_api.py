"""
books_api.py — Google Books API integration

Enriches raw book data (title/author from VLM) with:
    - Full title
    - Confirmed author
    - Publication year
    - ISBN-13 (or ISBN-10)
    - Genre / categories
    - Page count
    - Cover image URL

Uses the free Google Books API (no API key required).
Documentation: https://developers.google.com/books/docs/v1/using
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
REQUEST_TIMEOUT = 10.0
MAX_CONCURRENT = 5  # Limit concurrent requests to be polite


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw books with data from Google Books API.

    Args:
        raw_books: List of dicts with 'title' and 'author' keys
                   (as returned by vision.recognize_books)

    Returns:
        List of enriched book dicts with all catalog fields.
        Each dict has: Title, Author, Year, ISBN, Genre, Pages, Cover URL
    """
    if not raw_books:
        return []

    # Use semaphore to limit concurrent API calls
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            _enrich_one(book, client, semaphore)
            for book in raw_books
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to enrich '{raw_books[i].get('title')}': {result}")
            # Fall back to raw data from VLM
            enriched.append(_fallback(raw_books[i]))
        else:
            enriched.append(result)

    return enriched


async def _enrich_one(
    book: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Enrich a single book via Google Books API."""
    async with semaphore:
        query = _build_query(book)
        logger.debug(f"Querying Google Books: {query}")

        try:
            response = await client.get(
                BOOKS_API_URL,
                params={"q": query, "maxResults": 1, "langRestrict": ""},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Google Books API error {e.response.status_code} for '{query}'")
            return _fallback(book)
        except httpx.RequestError as e:
            logger.warning(f"Network error for '{query}': {e}")
            return _fallback(book)

        items = data.get("items", [])
        if not items:
            logger.debug(f"No results for: {query}")
            return _fallback(book)

        return _parse_volume(items[0], book)


def _build_query(book: dict) -> str:
    """Build a Google Books search query from raw book data."""
    title = book.get("title", "").strip()
    author = book.get("author", "").strip()

    if title and author:
        return f'intitle:"{title}" inauthor:"{author}"'
    elif title:
        return f'intitle:"{title}"'
    else:
        return title  # Fallback (shouldn't happen)


def _parse_volume(volume: dict, original: dict) -> dict:
    """
    Parse a Google Books API volume into our catalog format.

    Prefers API data but falls back to original VLM data if API returns
    empty fields.
    """
    info = volume.get("volumeInfo", {})

    # Title — prefer API, fall back to VLM
    title = info.get("title", "").strip() or original.get("title", "")

    # Author — join multiple authors; fall back to VLM
    api_authors = info.get("authors", [])
    author = ", ".join(api_authors) if api_authors else original.get("author", "")

    # Year — extract from publishedDate (can be "2001", "2001-06", or "2001-06-15")
    published = info.get("publishedDate", "")
    year = published[:4] if published else ""

    # ISBN — prefer ISBN-13, fall back to ISBN-10
    isbn = _extract_isbn(info.get("industryIdentifiers", []))

    # Genre — first category
    categories = info.get("categories", [])
    genre = categories[0] if categories else ""

    # Pages
    pages = str(info.get("pageCount", "")) if info.get("pageCount") else ""

    # Cover image
    image_links = info.get("imageLinks", {})
    cover_url = (
        image_links.get("thumbnail", "")
        or image_links.get("smallThumbnail", "")
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


def _extract_isbn(identifiers: list[dict]) -> str:
    """Extract ISBN-13 (preferred) or ISBN-10 from industry identifiers."""
    isbn13 = ""
    isbn10 = ""

    for identifier in identifiers:
        id_type = identifier.get("type", "")
        id_value = identifier.get("identifier", "")
        if id_type == "ISBN_13":
            isbn13 = id_value
        elif id_type == "ISBN_10":
            isbn10 = id_value

    return isbn13 or isbn10


def _fallback(book: dict) -> dict:
    """
    Create a catalog entry from raw VLM data (when API enrichment fails).
    Fields that Google Books would have filled are left empty.
    """
    return {
        "Title": book.get("title", ""),
        "Author": book.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }
