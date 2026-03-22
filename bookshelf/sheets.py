"""
Google Sheets module: append book catalog entries using Colab credentials.

Uses google.auth.default() — works automatically when running in Google Colab
where Drive is already mounted. No separate OAuth setup needed.
"""
import logging
from datetime import datetime, timezone

import gspread
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column order in the spreadsheet
COLUMNS = [
    "Date Added",           # When the photo was processed
    "Title (VLM)",          # Title as read by AI
    "Author (VLM)",         # Author as read by AI
    "Title (Confirmed)",    # Title from Google Books
    "Author (Confirmed)",   # Author from Google Books
    "Year",                 # Publication year
    "ISBN",                 # ISBN-13 or ISBN-10
    "Categories",           # Book genres/categories
    "Language",             # Language code (en, ru, etc.)
    "Pages",                # Page count
    "Description",          # Short description
    "Google Books Link",    # Link to Google Books entry
]


def _get_client() -> gspread.Client:
    """
    Get authenticated gspread client using Colab's built-in credentials.

    In Colab, google.auth.default() returns the credentials of the Google
    account that mounted Google Drive — no extra setup required.
    """
    try:
        creds, project = google_auth_default(scopes=SCOPES)
        # Refresh if needed
        if not creds.valid:
            creds.refresh(Request())
        return gspread.authorize(creds)
    except Exception as e:
        raise RuntimeError(
            f"Could not get Google credentials: {e}\n\n"
            "Make sure you're running this in Google Colab with Drive mounted.\n"
            "Run this in a Colab cell first:\n"
            "  from google.colab import drive\n"
            "  drive.mount('/content/drive')"
        ) from e


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
        # Bold the header row
        worksheet.format("A1:L1", {"textFormat": {"bold": True}})
        logger.info("Added header row.")


def append_books(books: list[dict], sheet_name: str, **kwargs) -> str:
    """
    Append enriched book data to a Google Sheet.

    Args:
        books:      List of enriched book dicts (from books_api.enrich_books)
        sheet_name: Name of the Google Sheet to create/update

    Returns:
        URL of the Google Sheet
    """
    if not books:
        logger.warning("No books to append.")
        return ""

    client = _get_client()
    spreadsheet = _get_or_create_sheet(client, sheet_name)
    worksheet = spreadsheet.sheet1
    _ensure_header(worksheet)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    for book in books:
        rows.append([
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

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info(f"Appended {len(rows)} rows to '{sheet_name}'.")

    url = spreadsheet.url
    logger.info(f"Sheet URL: {url}")
    return url


def get_sheet_url(sheet_name: str, **kwargs) -> str:
    """Get the URL of the catalog sheet (without modifying it)."""
    try:
        client = _get_client()
        return client.open(sheet_name).url
    except gspread.SpreadsheetNotFound:
        return ""
    except Exception as e:
        logger.warning(f"Could not get sheet URL: {e}")
        return ""
