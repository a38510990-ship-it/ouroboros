"""
Google Sheets module: append book catalog entries using OAuth credentials.

Requires token.json (created by oauth_setup.py).
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column order in the spreadsheet
COLUMNS = [
    "Date Added",
    "Title (VLM)",
    "Author (VLM)",
    "Title (Confirmed)",
    "Author (Confirmed)",
    "Year",
    "ISBN",
    "Categories",
    "Language",
    "Pages",
    "Description",
    "Google Books Link",
]


def _get_client(token_path: str = "token.json") -> gspread.Client:
    """
    Get authenticated gspread client using OAuth token.json.
    Automatically refreshes expired tokens.
    """
    token_file = Path(token_path)
    if not token_file.exists():
        raise FileNotFoundError(
            f"token.json not found at '{token_path}'.\n"
            "Run `python oauth_setup.py` first to authenticate with Google."
        )

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google OAuth token...")
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
            logger.info("Token refreshed and saved.")
        else:
            raise RuntimeError(
                "Google credentials are invalid and cannot be refreshed.\n"
                "Run `python oauth_setup.py` again to re-authenticate."
            )

    return gspread.authorize(creds)


def _get_or_create_sheet(client: gspread.Client, sheet_name: str) -> gspread.Spreadsheet:
    """Get existing spreadsheet by name or create a new one."""
    try:
        spreadsheet = client.open(sheet_name)
        logger.info(f"Found existing spreadsheet: '{sheet_name}'")
        return spreadsheet
    except gspread.SpreadsheetNotFound:
        logger.info(f"Creating new spreadsheet: '{sheet_name}'")
        spreadsheet = client.create(sheet_name)
        return spreadsheet


def _ensure_header(worksheet: gspread.Worksheet) -> None:
    """Add header row if the sheet is empty."""
    existing = worksheet.get_all_values()
    if not existing:
        worksheet.append_row(COLUMNS, value_input_option="RAW")
        worksheet.format("A1:L1", {"textFormat": {"bold": True}})
        logger.info("Added header row.")


def _get_existing_keys(worksheet: gspread.Worksheet) -> set:
    """
    Return a set of deduplication keys from existing rows.
    Key = lowercase "title_confirmed|authors_confirmed" or "title_vlm|author_vlm".
    """
    rows = worksheet.get_all_values()
    if not rows or len(rows) < 2:
        return set()

    keys = set()
    for row in rows[1:]:  # skip header
        if len(row) < 5:
            continue
        title_confirmed = row[3].strip().lower()
        author_confirmed = row[4].strip().lower()
        title_vlm = row[1].strip().lower()
        author_vlm = row[2].strip().lower()

        if title_confirmed:
            keys.add(f"{title_confirmed}|{author_confirmed}")
        elif title_vlm:
            keys.add(f"{title_vlm}|{author_vlm}")

    return keys


def _book_key(book: dict) -> str:
    """Compute deduplication key for a book dict."""
    title = (book.get("title_confirmed") or book.get("title", "")).strip().lower()
    author = (book.get("authors_confirmed") or book.get("author", "")).strip().lower()
    return f"{title}|{author}"


def append_books(
    books: list,
    sheet_name: str,
    token_path: str = "token.json",
    **kwargs,
) -> tuple:
    """
    Append enriched book data to a Google Sheet, with deduplication.

    Args:
        books:      List of enriched book dicts (from books_api.enrich_books)
        sheet_name: Name of the Google Sheet to create/update
        token_path: Path to token.json

    Returns:
        Tuple of (sheet_url: str, added_count: int, skipped_count: int)
    """
    if not books:
        logger.warning("No books to append.")
        return "", 0, 0

    client = _get_client(token_path)
    spreadsheet = _get_or_create_sheet(client, sheet_name)
    worksheet = spreadsheet.sheet1
    _ensure_header(worksheet)

    # Get existing keys for deduplication
    existing_keys = _get_existing_keys(worksheet)
    logger.info(f"Found {len(existing_keys)} existing books in sheet (for dedup).")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows_to_add = []
    skipped_count = 0

    for book in books:
        key = _book_key(book)
        if key in existing_keys:
            title = book.get("title_confirmed") or book.get("title", "?")
            logger.info(f"Skipping duplicate: {title}")
            skipped_count += 1
            continue

        rows_to_add.append([
            now,
            book.get("title", ""),
            book.get("author", ""),
            book.get("title_confirmed", ""),
            book.get("authors_confirmed", ""),
            book.get("year", ""),
            book.get("isbn", ""),
            book.get("categories", ""),
            book.get("language", ""),
            book.get("page_count", ""),
            book.get("description", ""),
            book.get("google_books_link", ""),
        ])
        existing_keys.add(key)  # prevent duplicates within this batch

    if rows_to_add:
        worksheet.append_rows(rows_to_add, value_input_option="USER_ENTERED")
        logger.info(f"Appended {len(rows_to_add)} rows to '{sheet_name}'.")
    else:
        logger.info("All books were duplicates — nothing added.")

    url = spreadsheet.url
    return url, len(rows_to_add), skipped_count


def get_sheet_url(sheet_name: str, token_path: str = "token.json", **kwargs) -> str:
    """Get the URL of the catalog sheet (without modifying it)."""
    try:
        client = _get_client(token_path)
        return client.open(sheet_name).url
    except gspread.SpreadsheetNotFound:
        return ""
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning(f"Could not get sheet URL: {e}")
        return ""
