"""
Google Sheets module: append book catalog entries using OAuth credentials.

Creates the sheet if it doesn't exist. Appends rows on each new photo.
Handles duplicate sheet names gracefully.
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
    "Date Added",         # When the photo was processed
    "Title (VLM)",        # Title as read by AI
    "Author (VLM)",       # Author as read by AI
    "Title (Confirmed)",  # Title from Google Books
    "Author (Confirmed)", # Author from Google Books
    "Year",               # Publication year
    "ISBN",               # ISBN-13 or ISBN-10
    "Categories",         # Book genres/categories
    "Language",           # Book language code (en, ru, etc.)
    "Pages",              # Page count
    "Description",        # Short description
    "Google Books Link",  # Link to Google Books entry
]


def _load_credentials(token_path: str) -> Credentials:
    """Load and refresh OAuth credentials from token.json."""
    path = Path(token_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Google token not found at {token_path}. "
            "Run 'python auth_google.py' to authenticate."
        )

    creds = Credentials.from_authorized_user_file(str(path), SCOPES)

    # Refresh if expired
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google OAuth token...")
            creds.refresh(Request())
            # Save refreshed token
            path.write_text(creds.to_json())
            logger.info("Token refreshed and saved.")
        else:
            raise RuntimeError(
                "Google credentials are invalid and cannot be refreshed. "
                "Run 'python auth_google.py' to re-authenticate."
            )

    return creds


def _get_or_create_sheet(client: gspread.Client, sheet_name: str) -> gspread.Spreadsheet:
    """Get existing spreadsheet by name or create a new one."""
    try:
        spreadsheet = client.open(sheet_name)
        logger.info(f"Found existing spreadsheet: {sheet_name}")
        return spreadsheet
    except gspread.SpreadsheetNotFound:
        logger.info(f"Creating new spreadsheet: {sheet_name}")
        spreadsheet = client.create(sheet_name)
        # Make it accessible (optional: share with yourself)
        # spreadsheet.share(your_email, perm_type='user', role='writer')
        return spreadsheet


def _ensure_header(worksheet: gspread.Worksheet) -> None:
    """Add header row if the sheet is empty."""
    all_values = worksheet.get_all_values()
    if not all_values:
        worksheet.append_row(COLUMNS, value_input_option="RAW")
        logger.info("Added header row to sheet.")
    elif all_values[0] != COLUMNS:
        # Sheet has data but wrong header — insert header at top
        logger.warning("Sheet has data but no matching header — skipping header insert.")


def append_books(
    books: list[dict],
    sheet_name: str,
    token_path: str,
) -> str:
    """
    Append enriched book data to a Google Sheet.

    Args:
        books: List of enriched book dicts (from enrich_books)
        sheet_name: Name of the Google Sheet to create/update
        token_path: Path to OAuth token.json file

    Returns:
        URL of the Google Sheet
    """
    if not books:
        logger.warning("No books to append.")
        return ""

    creds = _load_credentials(token_path)
    client = gspread.authorize(creds)

    spreadsheet = _get_or_create_sheet(client, sheet_name)
    worksheet = spreadsheet.sheet1

    _ensure_header(worksheet)

    # Build rows to append
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []

    for book in books:
        row = [
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
        ]
        rows.append(row)

    # Batch append all rows at once
    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info(f"Appended {len(rows)} rows to '{sheet_name}'")

    sheet_url = spreadsheet.url
    logger.info(f"Sheet URL: {sheet_url}")
    return sheet_url


def get_sheet_url(sheet_name: str, token_path: str) -> str:
    """Get the URL of the catalog sheet without modifying it."""
    creds = _load_credentials(token_path)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open(sheet_name)
        return spreadsheet.url
    except gspread.SpreadsheetNotFound:
        return ""
