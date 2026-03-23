"""
sheets.py — Сохраняет каталог книг как CSV прямо в Google Drive.

Никакого Google Cloud проекта не нужно — файл пишется напрямую
через смонтированный Drive (/content/drive/MyDrive/).

Открыть в Google Sheets: просто кликни на файл в Drive.
"""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["Title", "Author", "Year", "ISBN", "Genre", "Pages", "Added_At", "Location"]

DEFAULT_PATH = "/content/drive/MyDrive/Bookshelf Catalog.csv"


class BookshelfSheet:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)

    def _ensure_file(self) -> None:
        """Создаёт CSV файл с заголовками если не существует."""
        if not self.path.parent.exists():
            raise RuntimeError(
                f"Google Drive не смонтирован или путь недоступен: {self.path.parent}\n"
                "Убедись что Drive смонтирован в Colab."
            )
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
            logger.info(f"Created new catalog: {self.path}")

    def _load_existing(self) -> list[dict]:
        """Загружает существующие книги из CSV."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def _is_duplicate(self, book: dict, existing: list[dict]) -> bool:
        """Проверяет дубликат по ISBN или Title+Author."""
        isbn = (book.get("ISBN") or "").strip()
        title = (book.get("Title") or "").strip().lower()
        author = (book.get("Author") or "").strip().lower()

        for row in existing:
            row_isbn = (row.get("ISBN") or "").strip()
            row_title = (row.get("Title") or "").strip().lower()
            row_author = (row.get("Author") or "").strip().lower()

            # Дубликат по ISBN (если оба не пустые)
            if isbn and row_isbn and isbn == row_isbn:
                return True
            # Дубликат по Title + Author
            if title and author and title == row_title and author == row_author:
                return True
        return False

    def add_books(self, books: list[dict], location: str = "") -> tuple[list[dict], list[dict]]:
        """
        Добавляет книги в CSV, пропуская дубликаты.

        Args:
            books: список книг для добавления
            location: расположение полки (опционально, пользователь может заполнить сам)

        Returns:
            (added, skipped) — списки добавленных и пропущенных книг
        """
        self._ensure_file()
        existing = self._load_existing()

        # Подтягиваем актуальные колонки из файла (на случай если файл уже имеет Location)
        actual_columns = self._get_actual_columns()

        added = []
        skipped = []
        now = datetime.now(timezone.utc).isoformat()

        new_rows = []
        for book in books:
            if self._is_duplicate(book, existing + new_rows):
                skipped.append(book)
            else:
                row = {col: book.get(col, "") for col in actual_columns}
                row["Added_At"] = now
                if "Location" in actual_columns:
                    row["Location"] = location
                new_rows.append(row)
                added.append(row)

        if new_rows:
            with self.path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=actual_columns)
                writer.writerows(new_rows)
            logger.info(f"Added {len(new_rows)} books to {self.path}")

        return added, skipped

    def _get_actual_columns(self) -> list[str]:
        """Возвращает колонки из существующего файла, или дефолтные если файл новый."""
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames:
                    return list(reader.fieldnames)
        return CSV_COLUMNS

    def get_url(self) -> str:
        """Возвращает информацию о расположении файла."""
        exists = self.path.exists()
        if exists:
            try:
                existing = self._load_existing()
                count = len(existing)
                return (
                    f"📁 <b>Файл:</b> <code>{self.path}</code>\n"
                    f"📚 Книг в каталоге: <b>{count}</b>\n\n"
                    "Открой Google Drive на компьютере — файл "
                    "<b>Bookshelf Catalog.csv</b> там уже есть.\n"
                    "Кликни на него → откроется в Google Sheets."
                )
            except Exception:
                pass
        return (
            f"📁 Каталог будет создан по пути:\n<code>{self.path}</code>\n\n"
            "Отправь первое фото полки чтобы начать!"
        )
