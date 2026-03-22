"""
sheets.py — Google Sheets integration via gspread

Manages the bookshelf catalog spreadsheet.
Uses OAuth 2.0 credentials from token.json (created by oauth_setup.py).

The spreadsheet has one worksheet ("Books") with columns:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added

Deduplication:
    Before adding a book, checks if it already exists by:
    1. ISBN match (if available)
    2. Title + Author match (case-insensitive)
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

# Paths
HERE = Path(__file__).parent
TOKEN_PATH = HERE / "token.json"
CREDENTIALS_PATH = HERE / "credentials.json"

# OAuth scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Worksheet structure
SHEET_HEADERS = [
    "Title",
    "Author",
    "Year",
    "ISBN",
    "Genre",
    "Pages",
    "Cover URL",
    "Date Added",
]


class BookshelfSheet:
    """
    Interface to the Google Sheets bookshelf catalog.

    Usage:
        sheet = BookshelfSheet(sheet_name="My Books")
        added, skipped = sheet.add_books(book_list)
        url = sheet.get_url()
    """

    def __init__(self, sheet_name: str = "Bookshelf Catalog"):
        """
        Initialize connection to Google Sheets.

        Args:
            sheet_name: Name of the Google Spreadsheet to use/create.

        Raises:
            FileNotFoundError: If token.json is not found.
            gspread.exceptions.APIError: If auth fails.
        """
        self.sheet_name = sheet_name
        self._client = None
        self._spreadsheet = None
        self._worksheet = None

    def _ensure_connected(self) -> None:
        """Lazily connect to Google Sheets when first needed."""
        if self._worksheet is not None:
            return

        # Load and refresh credentials
        creds = self._load_credentials()

        # Create gspread client
        self._client = gspread.authorize(creds)

        # Open or create spreadsheet
        self._spreadsheet = self._open_or_create_spreadsheet()

        # Open or create worksheet
        self._worksheet = self._open_or_create_worksheet()

        logger.info(f"Connected to Google Sheets: {self._spreadsheet.url}")

    def _load_credentials(self) -> Credentials:
        """
        Load OAuth credentials from token.json.

        Refreshes expired credentials automatically.

        Raises:
            FileNotFoundError: If token.json doesn't exist.
        """
        if not TOKEN_PATH.exists():
            raise FileNotFoundError(
                f"token.json not found at {TOKEN_PATH}.\n"
                "Please run: python bookshelf/oauth_setup.py"
            )

        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        # Refresh if expired
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google credentials...")
            creds.refresh(Request())

            # Save refreshed credentials
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
            logger.info("Credentials refreshed and saved.")

        return creds

    def _open_or_create_spreadsheet(self) -> gspread.Spreadsheet:
        """Open existing spreadsheet by name, or create a new one."""
        try:
            spreadsheet = self._client.open(self.sheet_name)
            logger.info(f"Opened existing spreadsheet: {self.sheet_name}")
            return spreadsheet
        except gspread.SpreadsheetNotFound:
            logger.info(f"Creating new spreadsheet: {self.sheet_name}")
            spreadsheet = self._client.create(self.sheet_name)
            return spreadsheet

    def _open_or_create_worksheet(self) -> gspread.Worksheet:
        """Open or create the 'Books' worksheet with correct headers."""
        try:
            ws = self._spreadsheet.worksheet("Books")
            logger.info("Found existing 'Books' worksheet")
        except gspread.WorksheetNotFound:
            logger.info("Creating 'Books' worksheet")
            ws = self._spreadsheet.add_worksheet(title="Books", rows=1000, cols=len(SHEET_HEADERS))
            # Delete default 'Sheet1' if it exists
            try:
                sheet1 = self._spreadsheet.worksheet("Sheet1")
                self._spreadsheet.del_worksheet(sheet1)
            except gspread.WorksheetNotFound:
                pass

        # Ensure headers are correct
        existing_values = ws.row_values(1) if ws.row_count > 0 else []
        if existing_values != SHEET_HEADERS:
            if not existing_values:
                # Empty sheet — add headers
                ws.append_row(SHEET_HEADERS, value_input_option="RAW")
                logger.info("Added headers to worksheet")
            else:
                # Existing data — headers might be wrong, but don't overwrite
                logger.warning(f"Unexpected headers: {existing_values}")

        return ws

    def get_url(self) -> str:
        """Get the URL of the spreadsheet."""
        self._ensure_connected()
        return self._spreadsheet.url

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the catalog, skipping duplicates.

        Args:
            books: List of book dicts with keys matching SHEET_HEADERS.
                   'Date Added' is set automatically.

        Returns:
            Tuple of (added_books, skipped_books).
        """
        if not books:
            return [], []

        self._ensure_connected()

        # Load existing books for deduplication
        existing_keys = self._load_existing_keys()
        logger.info(f"Existing books in catalog: {len(existing_keys)}")

        added = []
        skipped = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        rows_to_append = []

        for book in books:
            # Build deduplication key
            key = self._book_key(book)

            if key in existing_keys:
                logger.info(f"Duplicate: {book.get('Title', '?')}")
                skipped.append(book)
                continue

            # Build the row
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
            existing_keys.add(key)  # Prevent duplicates within same batch
            added.append(book)

        # Batch append all new rows at once
        if rows_to_append:
            self._worksheet.append_rows(rows_to_append, value_input_option="RAW")
            logger.info(f"Added {len(rows_to_append)} books to catalog")

        return added, skipped

    def _load_existing_keys(self) -> set[str]:
        """
        Load all existing books and build a set of deduplication keys.

        Keys are: normalized_isbn OR normalized_title+author.
        """
        all_records = self._worksheet.get_all_records()
        keys = set()

        for record in all_records:
            key = self._book_key(record)
            if key:
                keys.add(key)

        return keys

    def _book_key(self, book: dict) -> str:
        """
        Build a deduplication key for a book.

        Priority:
        1. ISBN (if available and non-empty) — most reliable
        2. normalized(title) + "|" + normalized(author)
        """
        isbn = str(book.get("ISBN", "")).strip()
        if isbn and isbn not in ("", "None"):
            return f"isbn:{isbn}"

        title = _normalize_text(book.get("Title", ""))
        author = _normalize_text(book.get("Author", ""))

        if title:
            return f"book:{title}|{author}"

        return ""


# ── Utilities ──────────────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """
    Normalize text for deduplication comparison.

    Lowercase, strip whitespace, remove common articles.
    """
    if not text:
        return ""

    # Lowercase and strip
    normalized = str(text).lower().strip()

    # Remove leading articles for better matching
    for article in ("the ", "a ", "an ", "the\t"):
        if normalized.startswith(article):
            normalized = normalized[len(article):]
            break

    # Collapse multiple spaces
    normalized = " ".join(normalized.split())

    return normalized
