"""
books_api.py — Google Books API integration

Enriches a list of books (from VLM recognition) with detailed metadata
from the Google Books API (no API key required).

For each recognized book (title + author), queries the API and selects
the best match based on title and author similarity.

Enriched fields:
    Title, Author, Year, ISBN, Genre, Pages, Cover URL
"""

import asyncio
import logging
import re
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
MAX_CONCURRENT_REQUESTS = 3
REQUEST_TIMEOUT = 10.0


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw books with Google Books metadata.

    Args:
        raw_books: List of dicts with 'title' and 'author' keys
                   (as returned by vision.recognize_books)

    Returns:
        List of enriched book dicts with keys matching the spreadsheet headers:
        Title, Author, Year, ISBN, Genre, Pages, Cover URL
    """
    if not raw_books:
        return []

    # Use a semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            _enrich_single(client, semaphore, book)
            for book in raw_books
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for raw_book, result in zip(raw_books, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to enrich '{raw_book.get('title', '?')}': {result}")
            # Use raw VLM data as fallback
            enriched.append(_make_fallback(raw_book))
        else:
            enriched.append(result)

    return enriched


async def _enrich_single(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    raw_book: dict,
) -> dict:
    """
    Enrich a single book with Google Books data.

    Falls back to raw VLM data if the API returns no results.
    """
    title = raw_book.get("title", "")
    author = raw_book.get("author", "")

    async with semaphore:
        # Build query: title + author (if available)
        query = f"intitle:{title}"
        if author:
            query += f" inauthor:{author}"

        params = {
            "q": query,
            "maxResults": 5,
            "fields": "items(volumeInfo)",
        }

        try:
            response = await client.get(GOOGLE_BOOKS_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            logger.warning(f"Timeout for: {title}")
            return _make_fallback(raw_book)
        except httpx.HTTPStatusError as e:
            logger.warning(f"API error {e.response.status_code} for: {title}")
            return _make_fallback(raw_book)

    items = data.get("items", [])
    if not items:
        logger.info(f"No results for: {title}")
        return _make_fallback(raw_book)

    # Pick the best match among top results
    best = _pick_best_match(items, title, author)

    if best:
        return _extract_book_info(best)
    else:
        return _make_fallback(raw_book)


def _pick_best_match(
    items: list[dict],
    target_title: str,
    target_author: str,
) -> dict | None:
    """
    Select the best matching book from API results.

    Scoring:
    - Title similarity (primary)
    - Author similarity (secondary)
    - Prefers results with more complete data
    """
    if not items:
        return None

    best_item = None
    best_score = -1

    target_title_norm = _normalize(target_title)
    target_author_norm = _normalize(target_author)

    for item in items:
        info = item.get("volumeInfo", {})
        api_title = _normalize(info.get("title", ""))
        api_authors = [_normalize(a) for a in info.get("authors", [])]

        # Title score: higher if target title is contained in API title or vice versa
        title_score = _text_similarity(target_title_norm, api_title)

        # Author score (if we have a target author)
        author_score = 0.0
        if target_author_norm and api_authors:
            author_score = max(
                _text_similarity(target_author_norm, a) for a in api_authors
            )

        # Completeness bonus: award points for having ISBN, description, etc.
        completeness = 0
        if info.get("industryIdentifiers"):
            completeness += 0.1
        if info.get("description"):
            completeness += 0.05
        if info.get("pageCount"):
            completeness += 0.05

        total_score = title_score * 0.6 + author_score * 0.3 + completeness

        if total_score > best_score:
            best_score = total_score
            best_item = item

    # Only use API result if title matches reasonably well
    if best_score < 0.3:
        logger.debug(f"Best score {best_score:.2f} too low for: {target_title}")
        return None

    return best_item


def _extract_book_info(item: dict) -> dict:
    """Extract enriched book info from a Google Books API item."""
    info = item.get("volumeInfo", {})

    # Title
    title = info.get("title", "")
    subtitle = info.get("subtitle", "")
    if subtitle:
        title = f"{title}: {subtitle}"

    # Author(s)
    authors = info.get("authors", [])
    author = ", ".join(authors) if authors else ""

    # Publication year
    published_date = info.get("publishedDate", "")
    year = _extract_year(published_date)

    # ISBN (prefer ISBN-13)
    isbn = _extract_isbn(info.get("industryIdentifiers", []))

    # Genre (categories)
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
    if cover_url.startswith("http://"):
        cover_url = cover_url.replace("http://", "https://", 1)

    return {
        "Title": title,
        "Author": author,
        "Year": year,
        "ISBN": isbn,
        "Genre": genre,
        "Pages": pages,
        "Cover URL": cover_url,
    }


def _make_fallback(raw_book: dict) -> dict:
    """
    Create an enriched book dict from raw VLM data (no API data available).

    Uses the VLM-extracted title and author, leaves other fields empty.
    """
    return {
        "Title": raw_book.get("title", ""),
        "Author": raw_book.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }


# ── Text utilities ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation and articles."""
    if not text:
        return ""
    # Lowercase
    text = text.lower()
    # Remove punctuation
    text = re.sub(r"[^\w\s]", " ", text)
    # Remove articles
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
    # Collapse whitespace
    return " ".join(text.split())


def _text_similarity(a: str, b: str) -> float:
    """
    Simple text similarity score between 0 and 1.

    Uses character-level overlap (intersection over union of words).
    """
    if not a or not b:
        return 0.0

    words_a = set(a.split())
    words_b = set(b.split())

    intersection = words_a & words_b
    union = words_a | words_b

    if not union:
        return 0.0

    return len(intersection) / len(union)


def _extract_year(date_str: str) -> str:
    """Extract 4-digit year from a date string like '2019', '2019-03', '2019-03-15'."""
    if not date_str:
        return ""
    match = re.search(r"\b(19|20)\d{2}\b", date_str)
    return match.group(0) if match else ""


def _extract_isbn(identifiers: list[dict]) -> str:
    """
    Extract the best ISBN from the identifiers list.

    Prefers ISBN_13 over ISBN_10.
    """
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
