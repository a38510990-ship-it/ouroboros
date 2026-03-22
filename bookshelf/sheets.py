"""
sheets.py — Google Sheets integration via OAuth

Manages the bookshelf catalog spreadsheet:
- Creates the spreadsheet if it doesn't exist
- Initializes the header row
- Adds books with deduplication (by ISBN or Title+Author)
- Returns the spreadsheet URL

Authentication:
    Uses bookshelf/token.json (created by oauth_setup.py).
    Falls back to bookshelf/credentials.json if token.json is missing (re-authorizes).

Spreadsheet columns:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
TOKEN_PATH = HERE / "token.json"
CREDENTIALS_PATH = HERE / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Spreadsheet column headers (must match the order in _book_to_row)
HEADERS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]


class BookshelfSheet:
    """
    Interface to the bookshelf catalog Google Spreadsheet.

    Usage:
        sheet = BookshelfSheet(sheet_name="Bookshelf Catalog")
        added, skipped = sheet.add_books(books)
        url = sheet.get_url()
    """

    def __init__(self, sheet_name: str = "Bookshelf Catalog") -> None:
        """
        Initialize and open (or create) the spreadsheet.

        Args:
            sheet_name: Name of the Google Sheets spreadsheet.

        Raises:
            FileNotFoundError: If token.json is missing (run oauth_setup.py first).
            Exception: On authentication or API errors.
        """
        self.sheet_name = sheet_name
        self._client = _get_gspread_client()
        self._spreadsheet = self._open_or_create()
        self._worksheet = self._get_worksheet()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_url(self) -> str:
        """Return the URL of the spreadsheet."""
        return self._spreadsheet.url

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the catalog, skipping duplicates.

        Deduplication logic:
        1. If book has ISBN → check if ISBN already exists in catalog
        2. Otherwise → check if (Title, Author) combination already exists

        Args:
            books: List of book dicts with catalog fields.

        Returns:
            Tuple of (added, skipped) lists.
        """
        if not books:
            return [], []

        # Fetch existing data for dedup check
        existing = self._get_existing_books()

        added = []
        skipped = []

        rows_to_append = []

        for book in books:
            if self._is_duplicate(book, existing):
                logger.info(f"Skipping duplicate: '{book.get('Title', '?')}'")
                skipped.append(book)
            else:
                row = self._book_to_row(book)
                rows_to_append.append(row)
                added.append(book)

                # Update our in-memory existing set to catch intra-batch dupes
                isbn = book.get("ISBN", "").strip()
                title = book.get("Title", "").strip().lower()
                author = book.get("Author", "").strip().lower()
                if isbn:
                    existing["isbns"].add(isbn)
                if title:
                    existing["title_authors"].add((title, author))

        # Batch append all new rows at once
        if rows_to_append:
            self._worksheet.append_rows(
                rows_to_append,
                value_input_option="USER_ENTERED",
            )
            logger.info(f"Appended {len(rows_to_append)} row(s) to spreadsheet")

        return added, skipped

    # ── Private methods ────────────────────────────────────────────────────────

    def _open_or_create(self) -> gspread.Spreadsheet:
        """Open an existing spreadsheet by name, or create a new one."""
        try:
            spreadsheet = self._client.open(self.sheet_name)
            logger.info(f"Opened existing spreadsheet: '{self.sheet_name}'")
            return spreadsheet
        except gspread.SpreadsheetNotFound:
            logger.info(f"Creating new spreadsheet: '{self.sheet_name}'")
            spreadsheet = self._client.create(self.sheet_name)
            return spreadsheet

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get the first worksheet, initializing headers if it's new."""
        worksheet = self._spreadsheet.sheet1

        # Check if headers are already present
        try:
            first_row = worksheet.row_values(1)
        except Exception:
            first_row = []

        if first_row != HEADERS:
            logger.info("Initializing spreadsheet headers")
            worksheet.update(
                "A1",
                [HEADERS],
                value_input_option="USER_ENTERED",
            )
            # Format header row: bold
            try:
                worksheet.format(
                    "A1:H1",
                    {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                    },
                )
            except Exception as e:
                logger.debug(f"Could not format header row: {e}")

        return worksheet

    def _get_existing_books(self) -> dict:
        """
        Fetch all existing entries and build deduplication sets.

        Returns:
            Dict with:
                'isbns': set of existing ISBN strings
                'title_authors': set of (title_lower, author_lower) tuples
        """
        try:
            all_values = self._worksheet.get_all_values()
        except Exception as e:
            logger.warning(f"Could not fetch existing data: {e}")
            return {"isbns": set(), "title_authors": set()}

        isbns: set[str] = set()
        title_authors: set[tuple[str, str]] = set()

        # Skip header row (row 0)
        for row in all_values[1:]:
            # Pad row if shorter than expected
            while len(row) < 8:
                row.append("")

            title = row[0].strip().lower()   # Column A: Title
            author = row[1].strip().lower()  # Column B: Author
            isbn = row[3].strip()            # Column D: ISBN

            if isbn:
                isbns.add(isbn)
            if title:
                title_authors.add((title, author))

        logger.debug(f"Existing: {len(isbns)} ISBNs, {len(title_authors)} title+author pairs")
        return {"isbns": isbns, "title_authors": title_authors}

    def _is_duplicate(self, book: dict, existing: dict) -> bool:
        """Check if a book is already in the catalog."""
        isbn = book.get("ISBN", "").strip()
        if isbn and isbn in existing["isbns"]:
            return True

        title = book.get("Title", "").strip().lower()
        author = book.get("Author", "").strip().lower()
        if title and (title, author) in existing["title_authors"]:
            return True

        return False

    @staticmethod
    def _book_to_row(book: dict) -> list[str]:
        """Convert a book dict to a spreadsheet row (list of values)."""
        date_added = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return [
            book.get("Title", ""),
            book.get("Author", ""),
            book.get("Year", ""),
            book.get("ISBN", ""),
            book.get("Genre", ""),
            book.get("Pages", ""),
            book.get("Cover URL", ""),
            date_added,
        ]


# ── Authentication ─────────────────────────────────────────────────────────────

def _get_gspread_client() -> gspread.Client:
    """
    Build a gspread client using OAuth credentials from token.json.

    Raises:
        FileNotFoundError: If token.json is not found.
    """
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"token.json not found at {TOKEN_PATH}\n"
            "Run this to authorize: python bookshelf/oauth_setup.py"
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # Refresh token if expired
    if creds.expired and creds.refresh_token:
        logger.info("Refreshing expired OAuth token")
        try:
            creds.refresh(Request())
            # Save refreshed token
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            raise RuntimeError(
                f"Failed to refresh OAuth token: {e}\n"
                "Run oauth_setup.py to re-authorize."
            )

    return gspread.authorize(creds)
