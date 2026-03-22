"""
sheets.py — Google Sheets integration for Bookshelf Catalog

Manages the spreadsheet: creates/opens it, adds books, handles deduplication.

Authentication:
    Uses OAuth 2.0 credentials stored in bookshelf/token.json.
    Run bookshelf/oauth_setup.py once to create this file.

Spreadsheet structure:
    Columns: Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added

Deduplication:
    A book is considered a duplicate if it matches an existing entry by:
    1. ISBN (if both have an ISBN)
    2. Title + Author (case-insensitive, if no ISBN)
"""

import datetime
import logging
import os
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logger = logging.getLogger(__name__)

TOKEN_PATH = HERE / "token.json"
CREDENTIALS_PATH = HERE / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")


class BookshelfSheet:
    """
    Interface to the Google Sheets catalog.

    Usage:
        sheet = BookshelfSheet()
        added, skipped = sheet.add_books(books)
        url = sheet.get_url()
    """

    def __init__(self, sheet_name: str = GOOGLE_SHEET_NAME) -> None:
        self.sheet_name = sheet_name
        self._client = None
        self._spreadsheet = None
        self._worksheet = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_url(self) -> str:
        """Return the URL to the spreadsheet."""
        ws = self._get_worksheet()
        return self._spreadsheet.url

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the spreadsheet, skipping duplicates.

        Args:
            books: List of dicts with catalog fields
                   (Title, Author, Year, ISBN, Genre, Pages, Cover URL)

        Returns:
            Tuple of (added, skipped) book lists.
        """
        ws = self._get_worksheet()
        existing = self._load_existing()

        added = []
        skipped = []
        rows_to_append = []

        for book in books:
            if self._is_duplicate(book, existing):
                logger.info(f"Skipping duplicate: {book.get('Title', '?')}")
                skipped.append(book)
            else:
                today = datetime.date.today().isoformat()
                row = [
                    book.get("Title", ""),
                    book.get("Author", ""),
                    book.get("Year", ""),
                    book.get("ISBN", ""),
                    book.get("Genre", ""),
                    book.get("Pages", ""),
                    book.get("Cover URL", ""),
                    today,
                ]
                rows_to_append.append(row)
                existing.append(book)  # Update in-memory to catch self-duplicates
                added.append(book)
                logger.info(f"Adding: {book.get('Title', '?')}")

        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option="RAW")
            logger.info(f"Appended {len(rows_to_append)} row(s) to spreadsheet")

        return added, skipped

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_client(self) -> gspread.Client:
        """Get an authenticated gspread client."""
        if self._client is not None:
            return self._client

        if not TOKEN_PATH.exists():
            raise FileNotFoundError(
                f"token.json not found at {TOKEN_PATH}. "
                "Run: python bookshelf/oauth_setup.py"
            )

        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        # Refresh token if expired
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google OAuth token")
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        self._client = gspread.authorize(creds)
        return self._client

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get or create the catalog worksheet."""
        if self._worksheet is not None:
            return self._worksheet

        client = self._get_client()

        # Try to open existing spreadsheet
        try:
            self._spreadsheet = client.open(self.sheet_name)
            logger.info(f"Opened existing spreadsheet: {self.sheet_name}")
        except gspread.SpreadsheetNotFound:
            logger.info(f"Creating new spreadsheet: {self.sheet_name}")
            self._spreadsheet = client.create(self.sheet_name)
            logger.info(f"Created spreadsheet: {self._spreadsheet.url}")

        # Get first worksheet
        self._worksheet = self._spreadsheet.sheet1

        # Initialize header if sheet is empty
        self._ensure_header()

        return self._worksheet

    def _ensure_header(self) -> None:
        """Add header row if the worksheet is empty."""
        ws = self._worksheet
        first_row = ws.row_values(1) if ws.row_count > 0 else []

        if not first_row:
            ws.append_row(COLUMNS, value_input_option="RAW")
            # Make header bold
            try:
                ws.format("A1:H1", {"textFormat": {"bold": True}})
            except Exception:
                pass  # Formatting is cosmetic, ignore errors
            logger.info("Initialized spreadsheet with header row")

    def _load_existing(self) -> list[dict]:
        """Load all existing books from the spreadsheet."""
        ws = self._get_worksheet()
        records = ws.get_all_records(expected_headers=COLUMNS[:-1])  # Exclude Date Added
        return records

    def _is_duplicate(self, book: dict, existing: list[dict]) -> bool:
        """
        Check if a book is already in the catalog.

        Deduplication strategy:
        1. If both have ISBN — compare ISBNs
        2. Otherwise — compare Title + Author (case-insensitive)
        """
        book_isbn = str(book.get("ISBN", "")).strip()
        book_title = str(book.get("Title", "")).strip().lower()
        book_author = str(book.get("Author", "")).strip().lower()

        for existing_book in existing:
            ex_isbn = str(existing_book.get("ISBN", "")).strip()
            ex_title = str(existing_book.get("Title", "")).strip().lower()
            ex_author = str(existing_book.get("Author", "")).strip().lower()

            # ISBN match (both non-empty)
            if book_isbn and ex_isbn and book_isbn == ex_isbn:
                return True

            # Title + Author match (if no ISBNs)
            if not book_isbn or not ex_isbn:
                if book_title and book_title == ex_title:
                    # If both have authors, they must match too
                    if book_author and ex_author:
                        if book_author == ex_author:
                            return True
                    else:
                        # At least titles match — consider duplicate
                        return True

        return False
