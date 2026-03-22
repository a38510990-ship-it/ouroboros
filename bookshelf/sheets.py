"""
sheets.py — Google Sheets integration via gspread + OAuth

Creates or opens a Google Sheet and manages the book catalog.

Sheet structure:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added

Features:
    - Auto-creates sheet if it doesn't exist
    - Deduplication by ISBN (if available) or Title+Author
    - Returns (added, skipped) for user feedback
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.auth.exceptions import TransportError

logger = logging.getLogger(__name__)

# OAuth scopes needed for gspread
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Column headers (order matters — matches column indices)
HEADERS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]

# Default paths (relative to this file)
_DEFAULT_TOKEN = str(Path(__file__).parent / "token.json")
_DEFAULT_CREDENTIALS = str(Path(__file__).parent / "credentials.json")


class BookshelfSheet:
    """
    Manages a Google Sheet for the bookshelf catalog.

    Usage:
        sheet = BookshelfSheet(sheet_name="Bookshelf Catalog")
        added, skipped = sheet.add_books(enriched_books)
    """

    def __init__(
        self,
        sheet_name: str = "Bookshelf Catalog",
        token_path: str = _DEFAULT_TOKEN,
        credentials_path: str = _DEFAULT_CREDENTIALS,
    ):
        self.sheet_name = sheet_name
        self.token_path = token_path
        self.credentials_path = credentials_path
        self._client: Optional[gspread.Client] = None
        self._worksheet: Optional[gspread.Worksheet] = None

    def _get_credentials(self) -> Credentials:
        """Load and refresh OAuth credentials from token.json."""
        token_path = Path(self.token_path)
        credentials_path = Path(self.credentials_path)

        if not token_path.exists():
            raise FileNotFoundError(
                f"token.json not found at {token_path}\n"
                f"Run: python bookshelf/oauth_setup.py"
            )

        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                logger.info("Refreshing expired OAuth token...")
                try:
                    creds.refresh(Request())
                    # Save refreshed token
                    with open(token_path, "w") as f:
                        f.write(creds.to_json())
                    logger.info("Token refreshed and saved.")
                except TransportError as e:
                    raise RuntimeError(f"Failed to refresh token: {e}") from e
            else:
                raise RuntimeError(
                    "OAuth token is invalid and cannot be refreshed.\n"
                    "Run: python bookshelf/oauth_setup.py"
                )

        return creds

    def _get_client(self) -> gspread.Client:
        """Get or create authorized gspread client."""
        if self._client is None:
            creds = self._get_credentials()
            self._client = gspread.authorize(creds)
        return self._client

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get or create the worksheet, initializing headers if new."""
        if self._worksheet is not None:
            return self._worksheet

        client = self._get_client()

        # Try to open existing spreadsheet
        try:
            spreadsheet = client.open(self.sheet_name)
            logger.info(f"Opened existing spreadsheet: {self.sheet_name}")
        except gspread.SpreadsheetNotFound:
            # Create new spreadsheet
            logger.info(f"Creating new spreadsheet: {self.sheet_name}")
            spreadsheet = client.create(self.sheet_name)
            # Share with current user (make it accessible)
            # The spreadsheet is created in the authorized user's Drive

        worksheet = spreadsheet.sheet1

        # Check if headers exist; add them if not
        existing_data = worksheet.get_all_values()
        if not existing_data or existing_data[0] != HEADERS:
            logger.info("Adding headers to worksheet...")
            if not existing_data:
                worksheet.append_row(HEADERS)
            else:
                # Insert headers at row 1 if something else is there
                worksheet.insert_row(HEADERS, index=1)

        self._worksheet = worksheet
        return worksheet

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the Google Sheet, skipping duplicates.

        Args:
            books: List of enriched book dicts (from books_api.enrich_books)

        Returns:
            Tuple of (added_books, skipped_books)
        """
        if not books:
            return [], []

        worksheet = self._get_worksheet()

        # Load existing data for deduplication
        existing_rows = worksheet.get_all_records()
        existing_keys = _build_existing_keys(existing_rows)

        added = []
        skipped = []
        rows_to_append = []

        for book in books:
            dedup_key = _make_dedup_key(book)
            if dedup_key in existing_keys:
                logger.debug(f"Skipping duplicate: {dedup_key}")
                skipped.append(book)
            else:
                logger.debug(f"Adding new book: {dedup_key}")
                row = _book_to_row(book)
                rows_to_append.append(row)
                existing_keys.add(dedup_key)  # Prevent duplicates within this batch
                added.append(book)

        # Batch append all new rows
        if rows_to_append:
            worksheet.append_rows(rows_to_append)
            logger.info(f"Added {len(rows_to_append)} books to sheet")

        return added, skipped

    def get_url(self) -> str:
        """Get the URL of the Google Sheet."""
        worksheet = self._get_worksheet()
        spreadsheet_id = worksheet.spreadsheet.id
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def _build_existing_keys(rows: list[dict]) -> set[str]:
    """Build a set of deduplication keys from existing sheet rows."""
    keys = set()
    for row in rows:
        key = _make_dedup_key(row)
        if key:
            keys.add(key)
    return keys


def _make_dedup_key(book: dict) -> str:
    """
    Create a deduplication key for a book.

    Priority:
    1. ISBN (most reliable)
    2. normalized Title + Author
    """
    isbn = str(book.get("ISBN", "")).strip()
    if isbn:
        return f"isbn:{isbn}"

    title = str(book.get("Title", "") or book.get("title", "")).strip().lower()
    author = str(book.get("Author", "") or book.get("author", "")).strip().lower()

    if title:
        return f"ta:{title}|{author}"

    return ""


def _book_to_row(book: dict) -> list:
    """Convert an enriched book dict to a list of cell values for gspread."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
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
