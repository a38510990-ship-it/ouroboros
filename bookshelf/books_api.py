"""
Google Books API module: enrich book titles with metadata.

Uses the free Google Books API (no API key required for basic queries).
"""
import logging
import time
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
REQUEST_DELAY = 0.3  # seconds between requests (rate limiting)


def enrich_books(books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw books with metadata from Google Books API.

    Args:
        books: List of {title, author} dicts from VLM

    Returns:
        List of enriched dicts with additional metadata fields.
    """
    enriched = []
    total = len(books)

    with httpx.Client(timeout=10.0) as client:
        for i, book in enumerate(books):
            logger.info(f"Enriching book {i+1}/{total}: {book.get('title', '?')}")

            try:
                metadata = _fetch_book_metadata(client, book)
                enriched_book = {**book, **metadata}
                enriched.append(enriched_book)
            except Exception as e:
                logger.warning(f"Failed to enrich '{book.get('title')}': {e}")
                # Still include the book, just without extra metadata
                enriched.append({
                    **book,
                    "title_confirmed": "",
                    "authors_confirmed": "",
                    "year": "",
                    "isbn": "",
                    "categories": "",
                    "language": "",
                    "page_count": "",
                    "description": "",
                    "google_books_link": "",
                })

            if i < total - 1:
                time.sleep(REQUEST_DELAY)

    logger.info(f"Enriched {len(enriched)}/{total} books")
    return enriched


def _build_query(book: dict) -> str:
    """Build a Google Books search query from book data."""
    title = book.get("title", "").strip()
    author = book.get("author", "").strip()

    if author and author.lower() != "unknown":
        query = f'intitle:"{title}" inauthor:"{author}"'
    else:
        query = f'intitle:"{title}"'

    return query


def _fetch_book_metadata(client: httpx.Client, book: dict) -> dict:
    """
    Query Google Books API and extract metadata for a single book.

    Returns dict with metadata fields (empty strings if not found).
    """
    query = _build_query(book)

    params = {
        "q": query,
        "maxResults": 3,
        "orderBy": "relevance",
        "langRestrict": "",  # any language
        "printType": "books",
    }

    response = client.get(GOOGLE_BOOKS_URL, params=params)
    response.raise_for_status()

    data = response.json()
    total = data.get("totalItems", 0)
    items = data.get("items", [])

    if not items:
        # Try a simpler query (just title, no quotes)
        fallback_query = quote_plus(book.get("title", ""))
        params["q"] = book.get("title", "")
        response = client.get(GOOGLE_BOOKS_URL, params=params)
        data = response.json()
        items = data.get("items", [])

    if not items:
        logger.debug(f"No results for: {book.get('title')}")
        return _empty_metadata()

    # Take the best match (first result)
    volume_info = items[0].get("volumeInfo", {})
    sale_info = items[0].get("saleInfo", {})
    book_id = items[0].get("id", "")

    # Extract ISBN
    isbn = ""
    for identifier in volume_info.get("industryIdentifiers", []):
        if identifier.get("type") == "ISBN_13":
            isbn = identifier.get("identifier", "")
            break
        elif identifier.get("type") == "ISBN_10" and not isbn:
            isbn = identifier.get("identifier", "")

    # Extract publication year
    published_date = volume_info.get("publishedDate", "")
    year = published_date[:4] if published_date else ""

    # Authors list → comma separated
    authors_list = volume_info.get("authors", [])
    authors_confirmed = ", ".join(authors_list)

    # Categories
    categories = ", ".join(volume_info.get("categories", []))

    # Trim description to reasonable length
    description = volume_info.get("description", "")
    if len(description) > 300:
        description = description[:297] + "..."

    google_books_link = f"https://books.google.com/books?id={book_id}" if book_id else ""

    return {
        "title_confirmed": volume_info.get("title", ""),
        "authors_confirmed": authors_confirmed,
        "year": year,
        "isbn": isbn,
        "categories": categories,
        "language": volume_info.get("language", ""),
        "page_count": str(volume_info.get("pageCount", "")),
        "description": description,
        "google_books_link": google_books_link,
    }


def _empty_metadata() -> dict:
    """Return empty metadata dict when no match is found."""
    return {
        "title_confirmed": "",
        "authors_confirmed": "",
        "year": "",
        "isbn": "",
        "categories": "",
        "language": "",
        "page_count": "",
        "description": "",
        "google_books_link": "",
    }
