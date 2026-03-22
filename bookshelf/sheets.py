"""
sheets.py — Google Sheets integration via gspread + OAuth 2.0

Manages the bookshelf catalog spreadsheet:
    - Creates or opens existing spreadsheet by name
    - Adds/updates books with deduplication
    - Tracks: Title, Author, Year, ISBN, Genre, Pages, Cover URL, Date Added

Authentication:
    Uses OAuth 2.0 with token.json (created by oauth_setup.py).
    No service account needed — works with regular Google account.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

# OAuth scopes required
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Spreadsheet columns (in order)
COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]

# Paths
_HERE = Path(__file__).parent
TOKEN_PATH = _HERE / "token.json"
CREDENTIALS_PATH = _HERE / "credentials.json"


class BookshelfSheet:
    """
    Manages a Google Sheets catalog for a bookshelf.

    Usage:
        sheet = BookshelfSheet(sheet_name="My Books")
        added, skipped = sheet.add_books(enriched_books)
        url = sheet.get_url()
    """

    def __init__(self, sheet_name: str = "Bookshelf Catalog"):
        self.sheet_name = sheet_name
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._worksheet: Optional[gspread.Worksheet] = None

    def _get_client(self) -> gspread.Client:
        """Get or create an authorized gspread client."""
        if self._client is not None:
            return self._client

        creds = self._load_credentials()
        self._client = gspread.authorize(creds)
        return self._client

    def _load_credentials(self) -> Credentials:
        """
        Load OAuth credentials from token.json.

        Raises:
            FileNotFoundError: If token.json doesn't exist.
            RuntimeError: If credentials are invalid/expired and can't be refreshed.
        """
        if not TOKEN_PATH.exists():
            raise FileNotFoundError(
                f"token.json not found at {TOKEN_PATH}. "
                "Run 'python bookshelf/oauth_setup.py' to authorize."
            )

        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        # Refresh if expired
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google credentials...")
            try:
                creds.refresh(Request())
                # Save refreshed token
                with open(TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
                logger.info("Credentials refreshed and saved.")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to refresh credentials: {e}. "
                    "Run 'python bookshelf/oauth_setup.py' to re-authorize."
                ) from e

        if not creds.valid:
            raise RuntimeError(
                "Invalid Google credentials. "
                "Run 'python bookshelf/oauth_setup.py' to re-authorize."
            )

        return creds

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get or create the spreadsheet and worksheet."""
        if self._worksheet is not None:
            return self._worksheet

        client = self._get_client()

        # Try to open existing spreadsheet
        try:
            self._spreadsheet = client.open(self.sheet_name)
            logger.info(f"Opened existing spreadsheet: {self.sheet_name}")
        except gspread.SpreadsheetNotFound:
            # Create new spreadsheet
            self._spreadsheet = client.create(self.sheet_name)
            logger.info(f"Created new spreadsheet: {self.sheet_name}")

        # Get first worksheet
        self._worksheet = self._spreadsheet.sheet1

        # Ensure header row exists
        self._ensure_header()

        return self._worksheet

    def _ensure_header(self) -> None:
        """Create or verify the header row."""
        try:
            first_row = self._worksheet.row_values(1)
        except Exception:
            first_row = []

        if not first_row or first_row[0] != "Title":
            self._worksheet.insert_row(COLUMNS, index=1)
            logger.info("Header row created.")

            # Format header: bold
            try:
                self._worksheet.format("A1:H1", {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 1.0},
                })
            except Exception as e:
                logger.warning(f"Could not format header: {e}")

    def get_url(self) -> str:
        """Return the URL to the Google Spreadsheet."""
        ws = self._get_worksheet()
        return self._spreadsheet.url

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the spreadsheet, skipping duplicates.

        Deduplication logic (in priority order):
            1. ISBN match (if ISBN exists)
            2. Exact "Title + Author" match (case-insensitive)

        Args:
            books: List of enriched book dicts from books_api.enrich_books()

        Returns:
            (added, skipped) — two lists of book dicts
        """
        ws = self._get_worksheet()

        # Fetch existing data for deduplication
        existing = self._get_existing_books(ws)
        existing_isbns = {b["isbn"] for b in existing if b["isbn"]}
        existing_keys = {b["key"] for b in existing}

        added = []
        skipped = []
        rows_to_add = []

        for book in books:
            isbn = str(book.get("ISBN", "")).strip()
            title = str(book.get("Title", "")).strip()
            author = str(book.get("Author", "")).strip()
            key = f"{title.lower()}|{author.lower()}"

            # Check for duplicates
            if isbn and isbn in existing_isbns:
                logger.info(f"Skipping duplicate (ISBN): {title}")
                skipped.append(book)
                continue
            if key in existing_keys:
                logger.info(f"Skipping duplicate (title+author): {title}")
                skipped.append(book)
                continue

            # Prepare row
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            row = [
                title,
                author,
                str(book.get("Year", "") or ""),
                isbn,
                str(book.get("Genre", "") or ""),
                str(book.get("Pages", "") or ""),
                str(book.get("Cover URL", "") or ""),
                now,
            ]
            rows_to_add.append(row)
            added.append(book)

            # Track to avoid intra-batch duplicates
            if isbn:
                existing_isbns.add(isbn)
            existing_keys.add(key)

        # Batch append for efficiency
        if rows_to_add:
            ws.append_rows(rows_to_add, value_input_option="RAW")
            logger.info(f"Added {len(rows_to_add)} books to sheet")

        return added, skipped

    def _get_existing_books(self, ws: gspread.Worksheet) -> list[dict]:
        """Fetch existing books for deduplication."""
        try:
            all_records = ws.get_all_records()
        except Exception as e:
            logger.warning(f"Could not fetch existing records: {e}")
            return []

        books = []
        for record in all_records:
            title = str(record.get("Title", "")).strip().lower()
            author = str(record.get("Author", "")).strip().lower()
            isbn = str(record.get("ISBN", "")).strip()
            books.append({
                "isbn": isbn,
                "key": f"{title}|{author}",
            })

        return books
