"""
sheets.py — Google Sheets integration for Bookshelf Catalog

Использует credentials из уже подключённого Google Drive в Colab.
Никакой дополнительной авторизации не нужно — Drive уже смонтирован.

Структура таблицы:
    Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import google.auth
import gspread
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logger = logging.getLogger(__name__)

SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
WORKSHEET_NAME = "Books"
COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Cover URL", "Date Added"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_client() -> gspread.Client:
    """
    Авторизоваться через уже подключённый Colab/Drive аккаунт.
    Никаких credentials.json, token.json или Google Cloud проекта не нужно.
    """
    creds, _ = google.auth.default(scopes=SCOPES)
    return gspread.authorize(creds)


class BookshelfSheet:
    """
    Интерфейс к Google Sheets каталогу.

    Таблица создаётся в Drive аккаунте, который подключён к Colab.
    """

    def __init__(self):
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._worksheet: gspread.Worksheet | None = None

    def add_books(self, books: list[dict]) -> tuple[list[dict], list[dict]]:
        """Добавить книги в каталог, пропуская дубликаты."""
        ws = self._get_worksheet()
        existing_keys = self._get_existing_keys(ws)

        added, skipped, rows_to_append = [], [], []

        for book in books:
            key = _dedup_key(book)
            if key in existing_keys:
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
        return self._get_spreadsheet().url

    def _get_client(self) -> gspread.Client:
        if self._client is None:
            self._client = _get_client()
        return self._client

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet

        client = self._get_client()
        try:
            self._spreadsheet = client.open(SHEET_NAME)
        except gspread.SpreadsheetNotFound:
            self._spreadsheet = client.create(SHEET_NAME)
            logger.info(f"Created new spreadsheet: {SHEET_NAME!r}")

        return self._spreadsheet

    def _get_worksheet(self) -> gspread.Worksheet:
        if self._worksheet is not None:
            return self._worksheet

        spreadsheet = self._get_spreadsheet()
        try:
            ws = spreadsheet.worksheet(WORKSHEET_NAME)
            if ws.row_values(1) != COLUMNS:
                ws.update("A1", [COLUMNS])
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(WORKSHEET_NAME, rows=1000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)
            try:
                spreadsheet.del_worksheet(spreadsheet.worksheet("Sheet1"))
            except gspread.WorksheetNotFound:
                pass

        self._worksheet = ws
        return ws

    def _get_existing_keys(self, ws: gspread.Worksheet) -> set[str]:
        try:
            return {_dedup_key(r) for r in ws.get_all_records() if _dedup_key(r)}
        except Exception as e:
            logger.warning(f"Could not read existing records: {e}")
            return set()


def _dedup_key(book: dict) -> str:
    isbn = str(book.get("ISBN", "")).strip()
    if isbn:
        return f"isbn:{isbn}"
    title = str(book.get("Title", "")).lower().strip()
    author = str(book.get("Author", "")).lower().strip()
    return f"ta:{title}|{author}" if title else ""


def _book_to_row(book: dict) -> list[str]:
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
