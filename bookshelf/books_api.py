"""
books_api.py — Google Books API integration

Enriches book data (title, author) with:
    - Year (publication date)
    - ISBN-13 (preferred) or ISBN-10
    - Genre / categories
    - Page count
    - Cover image URL
    - Cleaned title and author

Uses the free Google Books API (no API key required).
Performs concurrent requests for speed.
"""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
REQUEST_TIMEOUT = 15.0
MAX_CONCURRENT = 5  # Max simultaneous requests to avoid rate limiting


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of {title, author} dicts with Google Books data.

    Args:
        raw_books: List of dicts with 'title' and optionally 'author' keys
                   (as returned by vision.recognize_books)

    Returns:
        List of enriched dicts with keys matching Google Sheet columns:
        Title, Author, Year, ISBN, Genre, Pages, Cover URL

    Note:
        Returns original data if enrichment fails for a book.
        Never drops books — if API fails, uses VLM-recognized data.
    """
    if not raw_books:
        return []

    # Limit concurrency with semaphore
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            _enrich_one(client, semaphore, book)
            for book in raw_books
        ]
        enriched = await asyncio.gather(*tasks)

    return list(enriched)


async def _enrich_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    book: dict,
) -> dict:
    """Enrich a single book dict with Google Books data."""
    async with semaphore:
        try:
            result = await _fetch_book_data(client, book)
            if result:
                logger.debug(f"Enriched: {result.get('Title', '?')} by {result.get('Author', '?')}")
                return result
        except Exception as e:
            logger.warning(f"Failed to enrich '{book.get('title', '?')}': {e}")

    # Fallback: use VLM-recognized data
    return {
        "Title": book.get("title", ""),
        "Author": book.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }


async def _fetch_book_data(
    client: httpx.AsyncClient,
    book: dict,
) -> Optional[dict]:
    """
    Query Google Books API for a book.

    Tries queries in order:
    1. Title + Author (most specific)
    2. Title only (if author empty)
    """
    title = book.get("title", "").strip()
    author = book.get("author", "").strip()

    if not title:
        return None

    # Build query
    if author:
        query = f'intitle:"{title}" inauthor:"{author}"'
    else:
        query = f'intitle:"{title}"'

    params = {
        "q": query,
        "maxResults": 1,
        "printType": "books",
        "langRestrict": "en",  # Can be removed for multi-language support
    }

    response = await client.get(GOOGLE_BOOKS_URL, params=params)
    response.raise_for_status()
    data = response.json()

    items = data.get("items", [])

    # If no results with author restriction, retry with title only
    if not items and author:
        params["q"] = f'intitle:"{title}"'
        del params["langRestrict"]  # Broader search
        response = await client.get(GOOGLE_BOOKS_URL, params=params)
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])

    if not items:
        logger.debug(f"No Google Books results for: {title}")
        return None

    return _parse_volume(items[0], book)


def _parse_volume(item: dict, original_book: dict) -> dict:
    """Extract relevant fields from a Google Books API volume item."""
    info = item.get("volumeInfo", {})

    # Title — prefer API result, fall back to VLM
    title = info.get("title", original_book.get("title", ""))

    # Author — join multiple authors with comma
    authors = info.get("authors", [])
    if authors:
        author = ", ".join(authors)
    else:
        author = original_book.get("author", "")

    # Publication year
    published_date = info.get("publishedDate", "")
    year = published_date[:4] if published_date and len(published_date) >= 4 else ""

    # ISBN — prefer ISBN-13
    isbn = _extract_isbn(info.get("industryIdentifiers", []))

    # Categories / Genre
    categories = info.get("categories", [])
    genre = categories[0] if categories else ""

    # Page count
    pages = str(info.get("pageCount", "") or "")
    if pages == "0":
        pages = ""

    # Cover image URL (highest quality available)
    image_links = info.get("imageLinks", {})
    cover_url = (
        image_links.get("extraLarge")
        or image_links.get("large")
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


def _extract_isbn(identifiers: list[dict]) -> str:
    """Extract ISBN from Google Books industry identifiers. Prefer ISBN-13."""
    isbn13 = ""
    isbn10 = ""

    for item in identifiers:
        id_type = item.get("type", "")
        identifier = item.get("identifier", "")
        if id_type == "ISBN_13":
            isbn13 = identifier
        elif id_type == "ISBN_10":
            isbn10 = identifier

    return isbn13 or isbn10
