"""
Google Books API enrichment module.
Fetches additional metadata (year, ISBN, genre, description) for each book.
No API key required — uses the public endpoint.
"""
import logging
import time
import urllib.parse
import urllib.request
import json

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


def fetch_book_metadata(title: str, author: str = "") -> dict:
    """
    Query Google Books API for a single book.
    Returns enriched metadata dict or empty dict if not found.
    """
    query = title
    if author and author.lower() != "unknown":
        query = f"{title} {author}"

    params = urllib.parse.urlencode({
        "q": query,
        "maxResults": 1,
        "langRestrict": "",  # all languages
        "printType": "books",
    })
    url = f"{GOOGLE_BOOKS_URL}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Google Books API error for '{title}': {e}")
        return {}

    items = data.get("items")
    if not items:
        logger.debug(f"No results for '{title}'")
        return {}

    info = items[0].get("volumeInfo", {})

    # Extract ISBN-13 preferably, fallback to ISBN-10
    isbn = ""
    for identifier in info.get("industryIdentifiers", []):
        if identifier["type"] == "ISBN_13":
            isbn = identifier["identifier"]
            break
        if identifier["type"] == "ISBN_10" and not isbn:
            isbn = identifier["identifier"]

    return {
        "title_confirmed": info.get("title", ""),
        "authors_confirmed": ", ".join(info.get("authors", [])),
        "year": (info.get("publishedDate") or "")[:4],
        "isbn": isbn,
        "categories": ", ".join(info.get("categories", [])),
        "language": info.get("language", ""),
        "page_count": info.get("pageCount", ""),
        "description": (info.get("description") or "")[:200],
        "google_books_link": info.get("infoLink", ""),
    }


def enrich_books(books: list[dict]) -> list[dict]:
    """
    Enrich a list of {title, author} with Google Books metadata.
    Adds a 0.3s delay between requests to be polite to the API.
    """
    enriched = []
    total = len(books)

    for i, book in enumerate(books, 1):
        title = book.get("title", "").strip()
        author = book.get("author", "").strip()

        if not title:
            continue

        logger.info(f"Enriching book {i}/{total}: '{title}'")
        metadata = fetch_book_metadata(title, author)

        row = {
            "title": title,
            "author": author,
            **metadata,
        }
        enriched.append(row)

        if i < total:
            time.sleep(0.3)  # polite delay

    return enriched
