"""
sheets.py — Google Sheets integration for Bookshelf Catalog

Использует авторизацию через Google Colab — никакого Google Cloud проекта не нужно.
Запускается в Colab, где пользователь уже залогинен в свой Google-аккаунт.

Структура таблицы:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import gspread
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logger = logging.getLogger(__name__)

# Sheet configuration
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
WORKSHEET_NAME = "Books"
COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]


def _authenticate():
    """
    Авторизация через Google Colab.

    В Colab вызывает auth.authenticate_user() — открывает браузер один раз.
    После этого google.auth.default() возвращает готовые credentials.

    Никакого Google Cloud проекта, credentials.json или billing не нужно.
    """
    try:
        from google.colab import auth
        auth.authenticate_user()
        logger.info("Colab authentication successful")
    except ImportError:
        # Не в Colab — пробуем application default credentials
        logger.info("Not in Colab, using google.auth.default()")

    import google.auth
    creds, _ = google.auth.default(scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    return creds


class BookshelfSheet:
    """
    Интерфейс к Google Sheets каталогу.

    Использование:
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
        Добавить книги в каталог, пропуская дубликаты.

        Args:
            books: Список книг (output из enrich_books).

        Returns:
            Tuple из (added, skipped) списков.
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
                existing_keys.add(key)

        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
            logger.info(f"Added {len(added)} book(s) to sheet")

        return added, skipped

    def get_url(self) -> str:
        """Вернуть ссылку на таблицу."""
        return self._get_spreadsheet().url

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_client(self) -> gspread.Client:
        """Получить или создать авторизованный gspread клиент."""
        if self._client is not None:
            return self._client

        creds = _authenticate()
        self._client = gspread.authorize(creds)
        return self._client

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        """Получить или создать таблицу."""
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
        """Получить или создать лист Books с заголовками."""
        if self._worksheet is not None:
            return self._worksheet

        spreadsheet = self._get_spreadsheet()

        try:
            ws = spreadsheet.worksheet(WORKSHEET_NAME)
            headers = ws.row_values(1)
            if headers != COLUMNS:
                logger.warning("Header mismatch, updating headers")
                ws.update("A1", [COLUMNS])
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(WORKSHEET_NAME, rows=1000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)
            logger.info(f"Created worksheet: {WORKSHEET_NAME!r}")
            try:
                default_sheet = spreadsheet.worksheet("Sheet1")
                spreadsheet.del_worksheet(default_sheet)
            except gspread.WorksheetNotFound:
                pass

        self._worksheet = ws
        return ws

    def _get_existing_keys(self, ws: gspread.Worksheet) -> set[str]:
        """Получить ключи дедупликации для существующих строк."""
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


def _dedup_key(book: dict) -> str:
    """
    Ключ дедупликации.
    1. ISBN → "isbn:9780..."
    2. Title + Author → "ta:название|автор"
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
    """Конвертировать книгу в строку таблицы."""
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
