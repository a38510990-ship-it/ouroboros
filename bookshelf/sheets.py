"""
sheets.py — Google Sheets integration for Bookshelf Catalog

Connects to Google Sheets using OAuth 2.0 credentials from token.json.
Creates the spreadsheet if it doesn't exist.
Adds books with deduplication (by ISBN, or Title+Author).

Sheet structure:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added

Authentication:
    Run oauth_setup.py once to create token.json.
    The token auto-refreshes when it expires.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logger = logging.getLogger(__name__)

# Paths
TOKEN_PATH = HERE / "token.json"
CREDENTIALS_PATH = HERE / "credentials.json"

# Google API scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Sheet configuration
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
WORKSHEET_NAME = "Books"
COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]


class BookshelfSheet:
    """
    Interface to the Google Sheets catalog.

    Usage:
        sheet = BookshelfSheet()
        added, skipped = sheet.add_books(books)
        url = sheet.get_url()
    """

    def __init__(self):
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._worksheet: gspread.Worksheet | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the catalog, skipping duplicates.

        Args:
            books: List of book dicts (output from enrich_books).

        Returns:
            Tuple of (added, skipped) lists.

        Raises:
            FileNotFoundError: If token.json or credentials.json is missing.
        """
        ws = self._get_worksheet()
        existing_keys = self._get_existing_keys(ws)

        added = []
        skipped = []
        rows_to_append = []

        for book in books:
            key = _dedup_key(book)
            if key in existing_keys:
                logger.info(f"Duplicate: {book.get('Title')!r}")
                skipped.append(book)
            else:
                added.append(book)
                rows_to_append.append(_book_to_row(book))
                existing_keys.add(key)  # Prevent duplicates within this batch

        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
            logger.info(f"Added {len(added)} book(s) to sheet")

        return added, skipped

    def get_url(self) -> str:
        """
        Return the spreadsheet URL.

        Raises:
            FileNotFoundError: If token.json is missing.
        """
        spreadsheet = self._get_spreadsheet()
        return spreadsheet.url

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_client(self) -> gspread.Client:
        """Get or create an authenticated gspread client."""
        if self._client is not None:
            return self._client

        creds = _load_credentials()
        self._client = gspread.authorize(creds)
        return self._client

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        """Get or create the catalog spreadsheet."""
        if self._spreadsheet is not None:
            return self._spreadsheet

        client = self._get_client()

        try:
            spreadsheet = client.open(SHEET_NAME)
            logger.info(f"Opened existing spreadsheet: {SHEET_NAME!r}")
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(SHEET_NAME)
            logger.info(f"Created new spreadsheet: {SHEET_NAME!r}")

        self._spreadsheet = spreadsheet
        return spreadsheet

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get or create the Books worksheet with proper headers."""
        if self._worksheet is not None:
            return self._worksheet

        spreadsheet = self._get_spreadsheet()

        try:
            ws = spreadsheet.worksheet(WORKSHEET_NAME)
            # Verify headers are correct
            headers = ws.row_values(1)
            if headers != COLUMNS:
                logger.warning("Header mismatch, updating headers")
                ws.update("A1", [COLUMNS])
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(WORKSHEET_NAME, rows=1000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)
            logger.info(f"Created worksheet: {WORKSHEET_NAME!r}")
            # If there was a default Sheet1, remove it
            try:
                default_sheet = spreadsheet.worksheet("Sheet1")
                spreadsheet.del_worksheet(default_sheet)
            except gspread.WorksheetNotFound:
                pass

        self._worksheet = ws
        return ws

    def _get_existing_keys(self, ws: gspread.Worksheet) -> set[str]:
        """
        Get deduplication keys for all existing rows.
        Returns a set of strings like "isbn:978..." or "title+author:...".
        """
        try:
            all_values = ws.get_all_records()
        except Exception as e:
            logger.warning(f"Could not read existing records: {e}")
            return set()

        keys = set()
        for row in all_values:
            key = _dedup_key(row)
            if key:
                keys.add(key)

        logger.debug(f"Found {len(keys)} existing entries for deduplication")
        return keys


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_credentials() -> Credentials:
    """
    Load and refresh Google OAuth credentials from token.json.

    Raises:
        FileNotFoundError: If token.json is not found.
    """
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"token.json not found at {TOKEN_PATH}\n"
            "Run: python bookshelf/oauth_setup.py"
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds.expired and creds.refresh_token:
        logger.info("Refreshing expired credentials...")
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        logger.info("Credentials refreshed and saved")

    return creds


def _dedup_key(book: dict) -> str:
    """
    Generate a deduplication key for a book.

    Priority:
    1. ISBN (if present) → "isbn:9780..."
    2. Title + Author (normalized) → "ta:the hitchhiker's guide|douglas adams"
    """
    isbn = str(book.get("ISBN", "")).strip()
    if isbn:
        return f"isbn:{isbn}"

    title = str(book.get("Title", "")).lower().strip()
    author = str(book.get("Author", "")).lower().strip()
    if title:
        return f"ta:{title}|{author}"

    return ""


def _book_to_row(book: dict) -> list[str]:
    """Convert a book dict to a spreadsheet row (same order as COLUMNS)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [
        book.get("Title", ""),
        book.get("Author", ""),
        book.get("Year", ""),
        book.get("ISBN", ""),
        book.get("Genre", ""),
        book.get("Pages", ""),
        book.get("Cover URL", ""),
        now,
    ]
