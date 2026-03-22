"""
bot.py — Bookshelf Catalog Telegram Bot

Main entry point for the bot. Handles:
    /start  — welcome message and instructions
    /sheet  — show the Google Sheets URL
    photo   — process bookshelf photo, add to catalog

Pipeline per photo:
    1. Download the photo from Telegram
    2. Recognize books using VLM (vision.py)
    3. Enrich with Google Books metadata (books_api.py)
    4. Add to Google Sheets, skip duplicates (sheets.py)
    5. Reply with summary

Usage:
    python bookshelf/bot.py

Environment variables (from bookshelf/.env):
    TELEGRAM_BOT_TOKEN  — required
    OPENROUTER_API_KEY  — required
    VLM_MODEL           — optional, default: google/gemini-2.0-flash-001
    GOOGLE_SHEET_NAME   — optional, default: "Bookshelf Catalog"
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment from bookshelf/.env
HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# Import local modules (after load_dotenv)
import books_api
import sheets
import vision

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")

# ── Google Sheets (shared instance) ──────────────────────────────────────────

_bookshelf_sheet: sheets.BookshelfSheet | None = None


def get_sheet() -> sheets.BookshelfSheet:
    """Get the shared BookshelfSheet instance (lazy initialization)."""
    global _bookshelf_sheet
    if _bookshelf_sheet is None:
        _bookshelf_sheet = sheets.BookshelfSheet(sheet_name=GOOGLE_SHEET_NAME)
    return _bookshelf_sheet


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    text = (
        "📚 <b>Bookshelf Catalog Bot</b>\n\n"
        "Send me a photo of your bookshelf and I'll:\n"
        "• Recognize all the books using AI\n"
        "• Look up details (author, year, ISBN, genre)\n"
        "• Add them to your Google Sheets catalog\n\n"
        "<b>Commands:</b>\n"
        "/start — Show this message\n"
        "/sheet — Get a link to your catalog\n\n"
        "<i>Tip: The clearer the book spines, the better the recognition!</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sheet command — show spreadsheet URL."""
    try:
        sheet = get_sheet()
        url = sheet.get_url()
        await update.message.reply_text(
            f"📊 <b>Your catalog:</b> <a href=\"{url}\">{GOOGLE_SHEET_NAME}</a>",
            parse_mode=ParseMode.HTML,
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "⚠️ Google Sheets not configured yet.\n\n"
            "Please run: <code>python bookshelf/oauth_setup.py</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Error getting sheet URL: {e}")
        await update.message.reply_text(
            "❌ Could not connect to Google Sheets.\n"
            f"Error: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming photo message.

    Full pipeline:
        Download photo → VLM recognition → Google Books enrichment → Google Sheets
    """
    message = update.message
    user = message.from_user
    logger.info(f"Photo received from {user.username or user.id}")

    # Send "processing" indicator
    status_msg = await message.reply_text("🔍 Analyzing your bookshelf...")

    try:
        # ── Step 1: Download photo ─────────────────────────────────────────
        # Telegram provides multiple sizes; take the largest
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        image_bytes = bytes(image_bytes)
        logger.info(f"Downloaded photo: {len(image_bytes)} bytes")

        # ── Step 2: Recognize books with VLM ──────────────────────────────
        await status_msg.edit_text("📖 Recognizing books with AI...")

        try:
            raw_books = await vision.recognize_books(image_bytes)
        except RuntimeError as e:
            # Missing API key
            await status_msg.edit_text(
                f"⚠️ <b>Configuration error:</b>\n<code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not raw_books:
            await status_msg.edit_text(
                "🤷 I couldn't recognize any books in this photo.\n\n"
                "Tips for better results:\n"
                "• Make sure book spines are clearly visible\n"
                "• Good lighting helps a lot\n"
                "• Try a closer shot"
            )
            return

        logger.info(f"Recognized {len(raw_books)} books by VLM")

        # ── Step 3: Enrich with Google Books API ───────────────────────────
        await status_msg.edit_text(
            f"🔎 Found {len(raw_books)} book(s). Looking up details..."
        )

        enriched_books = await books_api.enrich_books(raw_books)
        logger.info(f"Enriched {len(enriched_books)} books")

        # ── Step 4: Add to Google Sheets ───────────────────────────────────
        await status_msg.edit_text("📊 Saving to Google Sheets...")

        try:
            sheet = get_sheet()
            added, skipped = sheet.add_books(enriched_books)
        except FileNotFoundError:
            await status_msg.edit_text(
                "⚠️ <b>Google Sheets not set up.</b>\n\n"
                "Run <code>python bookshelf/oauth_setup.py</code> first.",
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            logger.error(f"Error saving to Sheets: {e}")
            await status_msg.edit_text(
                f"❌ Error saving to Google Sheets:\n<code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Step 5: Build and send summary ────────────────────────────────
        summary = _build_summary(added, skipped, sheet)
        await status_msg.edit_text(summary, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.exception(f"Unexpected error processing photo: {e}")
        await status_msg.edit_text(
            f"❌ Something went wrong:\n<code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )


# ── Utilities ─────────────────────────────────────────────────────────────────

def _build_summary(
    added: list[dict],
    skipped: list[dict],
    sheet: sheets.BookshelfSheet,
) -> str:
    """Build a human-readable summary message."""
    lines = []

    if added:
        lines.append(f"✅ <b>Added {len(added)} book(s):</b>")
        for book in added:
            title = book.get("Title", "Unknown")
            author = book.get("Author", "")
            year = book.get("Year", "")

            line = f"• <b>{_escape_html(title)}</b>"
            if author:
                line += f" — {_escape_html(author)}"
            if year:
                line += f" ({year})"
            lines.append(line)

    if skipped:
        lines.append("")
        lines.append(f"⏭ <b>Already in catalog ({len(skipped)}):</b>")
        for book in skipped:
            title = book.get("Title", "Unknown")
            lines.append(f"• {_escape_html(title)}")

    if not added and not skipped:
        lines.append("🤷 No books were processed.")

    # Add sheet link
    try:
        url = sheet.get_url()
        lines.append(f"\n📊 <a href=\"{url}\">Open catalog</a>")
    except Exception:
        pass

    return "\n".join(lines)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def validate_config() -> bool:
    """Validate required environment variables before starting."""
    ok = True

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        print("❌ TELEGRAM_BOT_TOKEN is not set in bookshelf/.env")
        ok = False

    if not os.getenv("OPENROUTER_API_KEY"):
        logger.error("OPENROUTER_API_KEY is not set")
        print("❌ OPENROUTER_API_KEY is not set in bookshelf/.env")
        ok = False

    token_path = HERE / "token.json"
    if not token_path.exists():
        print()
        print("⚠️  token.json not found. Google Sheets won't work.")
        print("   Run: python bookshelf/oauth_setup.py")
        print("   (You can still test the bot, but catalog saving will fail)")
        print()

    return ok


def main() -> None:
    """Start the bot."""
    print("=" * 50)
    print("  Bookshelf Catalog Bot")
    print("=" * 50)

    if not validate_config():
        sys.exit(1)

    print(f"✅ Starting bot...")
    print(f"   Sheet name: {GOOGLE_SHEET_NAME}")
    print(f"   VLM model: {os.getenv('VLM_MODEL', 'google/gemini-2.0-flash-001')}")
    print()

    # Build application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot started. Waiting for messages...")
    print("🤖 Bot is running. Press Ctrl+C to stop.")
    print()

    # Start polling
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
