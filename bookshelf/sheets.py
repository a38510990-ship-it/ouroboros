"""
Google Sheets module: write enriched books to a spreadsheet.
Uses OAuth credentials from token.json.
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

# Column headers for the catalog sheet
HEADERS = [
    "Date Added",
    "Title (from photo)",
    "Author (from photo)",
    "Title (confirmed)",
    "Authors (confirmed)",
    "Year",
    "ISBN",
    "Categories",
    "Language",
    "Pages",
    "Description",
    "Google Books Link",
]


def _get_gspread_client(token_path: str = "token.json") -> gspread.Client:
    """Load OAuth credentials and return authorized gspread client."""
    path = Path(token_path)
    if not path.exists():
        raise FileNotFoundError(
            "token.json not found. Run `python auth_google.py` first to authenticate."
        )

    creds = Credentials.from_authorized_user_file(str(path), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth token...")
            creds.refresh(Request())
            path.write_text(creds.to_json())
        else:
            raise RuntimeError(
                "OAuth token is invalid. Run `python auth_google.py` again."
            )

    return gspread.authorize(creds)


def get_or_create_sheet(client: gspread.Client, sheet_name: str) -> gspread.Spreadsheet:
    """Open existing spreadsheet or create a new one with headers."""
    try:
        spreadsheet = client.open(sheet_name)
        logger.info(f"Opened existing sheet: {sheet_name}")
    except gspread.SpreadsheetNotFound:
        logger.info(f"Creating new sheet: {sheet_name}")
        spreadsheet = client.create(sheet_name)
        # Add headers to first worksheet
        worksheet = spreadsheet.sheet1
        worksheet.update("A1", [HEADERS])
        # Format headers: bold
        worksheet.format("A1:L1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
        })
        logger.info("Headers added to new sheet")

    return spreadsheet


def append_books(books: list[dict], sheet_name: str, token_path: str = "token.json") -> str:
    """
    Append a list of enriched books to the Google Sheet.
    Creates the sheet if it doesn't exist.

    Returns:
        URL of the spreadsheet.
    """
    if not books:
        logger.warning("No books to append")
        return ""

    client = _get_gspread_client(token_path)
    spreadsheet = get_or_create_sheet(client, sheet_name)
    worksheet = spreadsheet.sheet1

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

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info(f"Appended {len(rows)} rows to sheet '{sheet_name}'")

    return spreadsheet.url
