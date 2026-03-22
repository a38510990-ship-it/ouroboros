"""
sheets.py — Google Sheets integration via gspread + OAuth 2.0

Manages the bookshelf catalog spreadsheet:
    - Opens (or creates) the spreadsheet by name
    - Initializes headers if the sheet is empty
    - Adds new books (with deduplication)
    - Checks for duplicates by ISBN (preferred) or Title+Author

Spreadsheet columns:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added

OAuth credentials are loaded from bookshelf/token.json.
To generate token.json, run: python bookshelf/oauth_setup.py
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent

# Scopes needed to create and edit spreadsheets
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Spreadsheet column headers (order matters — matches add_books logic)
HEADERS = [
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
    Manages a single Google Sheets spreadsheet as a book catalog.

    Usage:
        sheet = BookshelfSheet(sheet_name="Bookshelf Catalog")
        added, skipped = sheet.add_books(enriched_books)
        url = sheet.get_url()
    """

    def __init__(
        self,
        sheet_name: str = "Bookshelf Catalog",
        token_path: Optional[Path] = None,
    ) -> None:
        self.sheet_name = sheet_name
        self.token_path = token_path or (HERE / "token.json")

        # Lazy-initialized
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._worksheet: Optional[gspread.Worksheet] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the spreadsheet, skipping duplicates.

        Args:
            books: List of enriched book dicts with keys:
                   Title, Author, Year, ISBN, Genre, Pages, Cover URL

        Returns:
            (added, skipped) — two lists of book dicts
        """
        worksheet = self._get_worksheet()
        existing = self._get_existing_keys(worksheet)

        added = []
        skipped = []
        rows_to_append = []
        date_added = _now_str()

        for book in books:
            key = _dedup_key(book)

            if key in existing:
                logger.info(f"Skipping duplicate: {book.get('Title', '?')}")
                skipped.append(book)
                continue

            row = [
                book.get("Title", ""),
                book.get("Author", ""),
                book.get("Year", ""),
                book.get("ISBN", ""),
                book.get("Genre", ""),
                book.get("Pages", ""),
                book.get("Cover URL", ""),
                date_added,
            ]
            rows_to_append.append(row)
            existing.add(key)  # Prevent duplicates within the same batch
            added.append(book)

        if rows_to_append:
            worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
            logger.info(f"Appended {len(rows_to_append)} rows to spreadsheet")

        return added, skipped

    def get_url(self) -> str:
        """Return the URL of the spreadsheet."""
        spreadsheet = self._get_spreadsheet()
        return spreadsheet.url

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_client(self) -> gspread.Client:
        """Get an authenticated gspread client."""
        if self._client is not None:
            return self._client

        creds = self._load_credentials()
        self._client = gspread.authorize(creds)
        return self._client

    def _load_credentials(self) -> Credentials:
        """
        Load OAuth credentials from token.json.

        Refreshes the access token if expired.
        Raises FileNotFoundError if token.json is missing.
        """
        if not self.token_path.exists():
            raise FileNotFoundError(
                f"token.json not found at {self.token_path}. "
                "Run: python bookshelf/oauth_setup.py"
            )

        creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        # Refresh if expired
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing Google OAuth token...")
            creds.refresh(Request())
            # Save refreshed token
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())
            logger.info("Token refreshed and saved")

        return creds

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        """Get the spreadsheet, creating it if it doesn't exist."""
        if self._spreadsheet is not None:
            return self._spreadsheet

        client = self._get_client()

        try:
            self._spreadsheet = client.open(self.sheet_name)
            logger.info(f"Opened existing spreadsheet: '{self.sheet_name}'")
        except gspread.SpreadsheetNotFound:
            logger.info(f"Creating new spreadsheet: '{self.sheet_name}'")
            self._spreadsheet = client.create(self.sheet_name)
            logger.info(f"Created spreadsheet: {self._spreadsheet.url}")

        return self._spreadsheet

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get the first worksheet, initializing headers if empty."""
        if self._worksheet is not None:
            return self._worksheet

        spreadsheet = self._get_spreadsheet()
        self._worksheet = spreadsheet.sheet1

        # Initialize headers if the sheet is empty
        first_row = self._worksheet.row_values(1)
        if not first_row:
            logger.info("Initializing spreadsheet headers")
            self._worksheet.update([HEADERS], "A1")
            logger.info(f"Headers written: {HEADERS}")

        return self._worksheet

    def _get_existing_keys(self, worksheet: gspread.Worksheet) -> set[str]:
        """
        Get a set of deduplication keys for all existing books.

        Reads all rows and builds keys from ISBN (or Title+Author).
        """
        all_values = worksheet.get_all_values()

        if len(all_values) <= 1:
            # Only header row (or empty)
            return set()

        # Find column indices
        header_row = all_values[0]
        col_idx = {name: i for i, name in enumerate(header_row)}

        title_col = col_idx.get("Title", 0)
        author_col = col_idx.get("Author", 1)
        isbn_col = col_idx.get("ISBN", 3)

        keys = set()
        for row in all_values[1:]:
            if not row or not any(row):
                continue

            isbn = row[isbn_col].strip() if isbn_col < len(row) else ""
            title = row[title_col].strip() if title_col < len(row) else ""
            author = row[author_col].strip() if author_col < len(row) else ""

            key = _make_key(isbn=isbn, title=title, author=author)
            if key:
                keys.add(key)

        logger.debug(f"Found {len(keys)} existing books in spreadsheet")
        return keys


# ── Utilities ──────────────────────────────────────────────────────────────────

def _dedup_key(book: dict) -> str:
    """Create a deduplication key for a book dict."""
    return _make_key(
        isbn=book.get("ISBN", ""),
        title=book.get("Title", ""),
        author=book.get("Author", ""),
    )


def _make_key(isbn: str, title: str, author: str) -> str:
    """
    Create a normalized deduplication key.

    Strategy:
    - If ISBN is available: use it (most reliable)
    - Otherwise: use normalized "title|author"
    """
    isbn = isbn.strip()
    if isbn:
        return f"isbn:{isbn}"

    # Fallback: normalized title + author
    title_norm = _normalize(title)
    author_norm = _normalize(author)

    if not title_norm:
        return ""

    return f"title:{title_norm}|author:{author_norm}"


def _normalize(text: str) -> str:
    """Lowercase, strip extra whitespace."""
    return " ".join(str(text).lower().split())


def _now_str() -> str:
    """Return current UTC date as YYYY-MM-DD string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
