"""
sheets.py — Google Sheets integration for Bookshelf Catalog

Uses OAuth 2.0 (not Service Account) — user authorizes via browser once,
token is saved to token.json and auto-refreshed.

First-time setup:
    python bookshelf/oauth_setup.py

After that, this module works automatically.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import gspread

logger = logging.getLogger(__name__)

# Required API scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Catalog column headers (order matters — matches sheet columns)
COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]


def get_credentials(
    token_path: str,
    credentials_path: str,
) -> Credentials:
    """
    Load or create OAuth credentials.

    1. If token.json exists and is valid → return it
    2. If token.json exists but expired → refresh it
    3. If token.json doesn't exist → run browser OAuth flow
    """
    creds = None
    token_file = Path(token_path)
    creds_file = Path(credentials_path)

    if not creds_file.exists():
        raise FileNotFoundError(
            f"credentials.json not found at: {creds_file}\n"
            "Run: python bookshelf/oauth_setup.py"
        )

    # Load existing token
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            logger.warning(f"Could not load token.json: {e}")
            creds = None

    # Refresh or create new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth token...")
            creds.refresh(Request())
        else:
            logger.info("Running OAuth authorization flow (browser will open)...")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)

        # Save refreshed/new token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
        logger.info(f"Token saved to: {token_file}")

    return creds


class BookshelfSheet:
    """
    Google Sheets client for the bookshelf catalog.

    Sheet structure:
        Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added

    Features:
    - Creates the sheet if it doesn't exist
    - Deduplication by ISBN (if available) or Title+Author
    - Thread-safe for async bots (gspread is sync, called from async via asyncio)
    """

    def __init__(
        self,
        sheet_name: str = "Bookshelf Catalog",
        token_path: str | None = None,
        credentials_path: str | None = None,
    ):
        self.sheet_name = sheet_name
        self._token_path = token_path or str(Path(__file__).parent / "token.json")
        self._credentials_path = credentials_path or str(
            Path(__file__).parent / "credentials.json"
        )
        self._client: gspread.Client | None = None
        self._worksheet: gspread.Worksheet | None = None

    def _get_client(self) -> gspread.Client:
        """Get or create gspread client with OAuth credentials."""
        if self._client is None:
            creds = get_credentials(
                token_path=self._token_path,
                credentials_path=self._credentials_path,
            )
            self._client = gspread.authorize(creds)
        return self._client

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get or create the catalog worksheet."""
        if self._worksheet is None:
            client = self._get_client()
            try:
                spreadsheet = client.open(self.sheet_name)
                logger.info(f"Opened existing spreadsheet: {self.sheet_name}")
            except gspread.SpreadsheetNotFound:
                logger.info(f"Creating new spreadsheet: {self.sheet_name}")
                spreadsheet = client.create(self.sheet_name)

            worksheet = spreadsheet.sheet1

            # Initialize headers if sheet is empty
            existing = worksheet.get_all_values()
            if not existing:
                worksheet.append_row(COLUMNS)
                logger.info("Initialized sheet with column headers")
            elif existing[0] != COLUMNS:
                # Headers exist but don't match — log a warning
                logger.warning(
                    f"Sheet headers don't match expected: {existing[0]}"
                )

            self._worksheet = worksheet

        return self._worksheet

    def get_url(self) -> str:
        """Return the URL of the Google Sheet."""
        worksheet = self._get_worksheet()
        return worksheet.spreadsheet.url

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Add books to the catalog, skipping duplicates.

        Args:
            books: list of enriched book dicts (from books_api.enrich_books)

        Returns:
            (added, skipped) — lists of added and skipped books
        """
        if not books:
            return [], []

        worksheet = self._get_worksheet()

        # Load existing books for deduplication
        existing_books = self._get_existing_books(worksheet)

        added = []
        skipped = []
        rows_to_add = []

        for book in books:
            if self._is_duplicate(book, existing_books):
                logger.info(f"Skipping duplicate: {book.get('Title')}")
                skipped.append(book)
            else:
                rows_to_add.append(book)
                added.append(book)
                # Add to in-memory set to catch duplicates within this batch
                existing_books.append(book)

        if rows_to_add:
            date_added = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            rows = [
                [
                    book.get("Title", ""),
                    book.get("Author", ""),
                    book.get("Year", ""),
                    book.get("ISBN", ""),
                    book.get("Genre", ""),
                    book.get("Pages", ""),
                    book.get("Cover URL", ""),
                    date_added,
                ]
                for book in rows_to_add
            ]
            worksheet.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info(f"Added {len(rows_to_add)} books to sheet")

        return added, skipped

    def _get_existing_books(self, worksheet: gspread.Worksheet) -> list[dict]:
        """Load existing books from the sheet for deduplication."""
        try:
            all_values = worksheet.get_all_values()
            if len(all_values) <= 1:  # Only headers or empty
                return []

            headers = all_values[0]
            books = []
            for row in all_values[1:]:
                # Pad row if shorter than headers
                while len(row) < len(headers):
                    row.append("")
                book = dict(zip(headers, row))
                books.append(book)
            return books
        except Exception as e:
            logger.warning(f"Could not load existing books: {e}")
            return []

    def _is_duplicate(self, book: dict, existing: list[dict]) -> bool:
        """
        Check if a book is already in the catalog.

        Deduplication logic:
        1. If both have ISBN → match by ISBN
        2. Otherwise → match by normalized Title + Author
        """
        isbn = book.get("ISBN", "").strip()
        title = _normalize(book.get("Title", ""))
        author = _normalize(book.get("Author", ""))

        for existing_book in existing:
            # ISBN match (most reliable)
            existing_isbn = existing_book.get("ISBN", "").strip()
            if isbn and existing_isbn and isbn == existing_isbn:
                return True

            # Title + Author match (fallback)
            existing_title = _normalize(existing_book.get("Title", ""))
            existing_author = _normalize(existing_book.get("Author", ""))
            if title and existing_title and title == existing_title:
                if not author or not existing_author or author == existing_author:
                    return True

        return False


def _normalize(text: str) -> str:
    """Normalize text for fuzzy comparison (lowercase, strip spaces)."""
    return text.lower().strip()
