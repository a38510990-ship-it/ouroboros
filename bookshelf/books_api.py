"""
books_api.py — Google Books API enrichment

Takes a list of {title, author} dicts from VLM and enriches them
with metadata from the Google Books API (free, no key required).

Output format: {Title, Author, Year, ISBN, Genre, Pages, Cover URL}
"""

import asyncio
import logging
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
REQUEST_TIMEOUT = 10.0
MAX_CONCURRENT = 5  # Parallel requests to Google Books API


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of {title, author} dicts with Google Books metadata.

    Args:
        raw_books: list of dicts with 'title' and 'author' keys

    Returns:
        list of enriched book dicts with all catalog fields
    """
    if not raw_books:
        return []

    # Use semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def enrich_one(book: dict) -> dict:
        async with semaphore:
            return await _fetch_book_metadata(
                title=book.get("title", ""),
                author=book.get("author", ""),
            )

    results = await asyncio.gather(*[enrich_one(b) for b in raw_books])
    return list(results)


async def _fetch_book_metadata(title: str, author: str) -> dict:
    """
    Query Google Books API for a single book.

    Tries two queries:
    1. title + author (if author available)
    2. title only (fallback)
    """
    base_result = {
        "Title": title,
        "Author": author,
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }

    if not title:
        return base_result

    queries = []
    if author:
        queries.append(f'intitle:{title} inauthor:{author}')
    queries.append(f'intitle:{title}')

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for query in queries:
            try:
                params = {
                    "q": query,
                    "maxResults": 1,
                    "printType": "books",
                    "langRestrict": "",  # any language
                }
                response = await client.get(GOOGLE_BOOKS_API, params=params)

                if response.status_code != 200:
                    logger.warning(f"Google Books API error {response.status_code} for: {title}")
                    continue

                data = response.json()
                items = data.get("items", [])

                if not items:
                    logger.debug(f"No results for query: {query}")
                    continue

                # Parse first result
                enriched = _parse_volume(items[0], base_result)
                logger.info(f"Enriched: {title!r} → {enriched['Title']!r} by {enriched['Author']!r}")
                return enriched

            except httpx.TimeoutException:
                logger.warning(f"Timeout fetching metadata for: {title}")
            except Exception as e:
                logger.warning(f"Error fetching metadata for {title!r}: {e}")

    # Return base result if all queries failed
    logger.info(f"No metadata found for: {title!r}")
    return base_result


def _parse_volume(item: dict, fallback: dict) -> dict:
    """
    Parse a Google Books API volume item into our catalog format.
    """
    info = item.get("volumeInfo", {})

    # Title
    title = info.get("title", fallback["Title"])
    subtitle = info.get("subtitle", "")
    if subtitle:
        title = f"{title}: {subtitle}"

    # Author(s)
    authors = info.get("authors", [])
    author = ", ".join(authors) if authors else fallback["Author"]

    # Year (from publishedDate like "2001-09-11" or "2001")
    published = info.get("publishedDate", "")
    year = published[:4] if published else ""

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

    # Genre / categories
    categories = info.get("categories", [])
    genre = categories[0] if categories else ""

    # Pages
    pages = str(info.get("pageCount", "")) if info.get("pageCount") else ""

    # Cover image
    image_links = info.get("imageLinks", {})
    cover_url = (
        image_links.get("thumbnail")
        or image_links.get("smallThumbnail")
        or ""
    )
    # Use HTTPS
    if cover_url:
        cover_url = cover_url.replace("http://", "https://")

    return {
        "Title": title,
        "Author": author,
        "Year": year,
        "ISBN": isbn,
        "Genre": genre,
        "Pages": pages,
        "Cover URL": cover_url,
    }
