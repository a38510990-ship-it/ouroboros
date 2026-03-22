"""
Bookshelf Catalog Telegram Bot
================================
Send a photo of your bookshelf — the bot recognizes all books and adds them
to your personal Google Sheets catalog.

Commands:
    /start   — welcome message and instructions
    /catalog — show link to your Google Sheet catalog
    /help    — show help

Usage:
    1. Run auth_google.py once to set up Google OAuth
    2. Copy env.example to .env and fill in your credentials
    3. Run: python bot.py
"""
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from vision import recognize_books
from books_api import enrich_books
from sheets import append_books, get_sheet_url

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Config from environment
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
GOOGLE_TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")

# Vision model — can be overridden via env var
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")

# ─────────────────────────────────────────────────────────────────────────────
# Message handlers
# ─────────────────────────────────────────────────────────────────────────────

WELCOME_MESSAGE = """📚 *Bookshelf Catalog Bot*

Send me a photo of your bookshelf and I'll:
1. Recognize all visible book titles
2. Look up metadata (author, year, ISBN)
3. Add them to your Google Sheets catalog

*Tips for best results:*
• Make sure book spines are visible and readable
• Good lighting helps a lot
• Include both vertical and horizontal books

Use /catalog to see your current catalog link.
"""

HELP_MESSAGE = """*How to use:*

📸 *Send a photo* — I'll recognize all books on the shelf

*/catalog* — Get the link to your Google Sheets catalog
*/start* — Show welcome message
*/help* — Show this help

*Your catalog* is stored in Google Sheets and grows with each new photo you send.
You can share it, sort it, filter it — it's just a regular spreadsheet!
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    sheet_url = get_sheet_url(GOOGLE_SHEET_NAME, GOOGLE_TOKEN_PATH)

    keyboard = []
    if sheet_url:
        keyboard.append([InlineKeyboardButton("📊 Open Catalog", url=sheet_url)])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")


async def catalog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /catalog command — show catalog link."""
    sheet_url = get_sheet_url(GOOGLE_SHEET_NAME, GOOGLE_TOKEN_PATH)

    if sheet_url:
        keyboard = [[InlineKeyboardButton("📊 Open Catalog", url=sheet_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"📚 Your catalog:\n{sheet_url}",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            "📭 No catalog yet — send me a photo of your bookshelf to start!"
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle photo messages.
    Downloads the photo, runs VLM recognition, enriches with Google Books,
    appends to Google Sheet, and replies with results.
    """
    msg = update.message
    chat_id = update.effective_chat.id

    # Status message
    status_msg = await msg.reply_text("📸 Got the photo! Analyzing your bookshelf...")

    try:
        # Get the highest resolution version of the photo
        photo = msg.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)

        # Download to a temp file
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        await photo_file.download_to_drive(tmp_path)
        logger.info(f"Downloaded photo: {tmp_path} ({Path(tmp_path).stat().st_size // 1024} KB)")

        # Step 1: VLM recognition
        await status_msg.edit_text("🔍 Recognizing books with AI vision...")

        books_raw = recognize_books(
            image_path=tmp_path,
            api_key=OPENROUTER_API_KEY,
            model=VLM_MODEL,
        )

        if not books_raw:
            await status_msg.edit_text(
                "😕 I couldn't recognize any books in this photo.\n\n"
                "Tips:\n"
                "• Make sure book spines are clearly visible\n"
                "• Try better lighting\n"
                "• The camera should face the spines directly"
            )
            return

        await status_msg.edit_text(
            f"📖 Found {len(books_raw)} book(s)! Looking up details..."
        )

        # Step 2: Enrich with Google Books API
        books_enriched = enrich_books(books_raw)

        # Step 3: Append to Google Sheets
        await status_msg.edit_text("📊 Saving to your Google Sheets catalog...")

        sheet_url = append_books(
            books=books_enriched,
            sheet_name=GOOGLE_SHEET_NAME,
            token_path=GOOGLE_TOKEN_PATH,
        )

        # Step 4: Build response message
        book_list = _format_book_list(books_enriched)
        keyboard = [[InlineKeyboardButton("📊 Open Catalog", url=sheet_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await status_msg.edit_text(
            f"✅ Added *{len(books_enriched)} book(s)* to your catalog!\n\n"
            f"{book_list}",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        await status_msg.edit_text(
            "❌ Error: Google credentials not found.\n"
            "Please run `python auth_google.py` on the server to set up authentication."
        )
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        await status_msg.edit_text(
            f"❌ Error processing image:\n{str(e)[:200]}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error in photo_handler: {e}")
        await status_msg.edit_text(
            "❌ Something went wrong. Please try again.\n"
            f"Error: {str(e)[:100]}"
        )
    finally:
        # Clean up temp file
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def _format_book_list(books: list[dict]) -> str:
    """Format the list of books for display in Telegram."""
    lines = []
    for i, book in enumerate(books[:10], 1):  # show max 10 in message
        title = book.get("title_confirmed") or book.get("title", "Unknown")
        author = book.get("authors_confirmed") or book.get("author", "")
        year = book.get("year", "")

        line = f"*{i}.* {title}"
        if author:
            line += f" — _{author}_"
        if year:
            line += f" ({year})"
        lines.append(line)

    if len(books) > 10:
        lines.append(f"_...and {len(books) - 10} more in the catalog_")

    return "\n".join(lines)


def _non_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-photo messages with a hint."""
    pass  # Ignore non-photo messages silently


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Start the bot."""
    logger.info(f"Starting Bookshelf Catalog Bot...")
    logger.info(f"Sheet name: {GOOGLE_SHEET_NAME}")
    logger.info(f"VLM model: {VLM_MODEL}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("catalog", catalog_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
