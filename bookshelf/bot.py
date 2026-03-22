"""
bot.py — Bookshelf Catalog Telegram Bot

Main entry point. Handles Telegram updates and orchestrates:
    1. Receive photo from user
    2. Recognize books via VLM (vision.py)
    3. Enrich with Google Books API (books_api.py)
    4. Save to Google Sheets (sheets.py)
    5. Reply with results

Commands:
    /start — welcome message and instructions
    /sheet — link to the Google Sheet
    /help  — same as /start
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Load .env from bookshelf directory
load_dotenv(Path(__file__).parent / ".env")

# Configure logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# Import our modules
import vision
import books_api
from sheets import BookshelfSheet

# Environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")

# Sheet instance (initialized once, reused)
_sheet = BookshelfSheet(sheet_name=GOOGLE_SHEET_NAME)


# --- Handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help commands."""
    text = (
        "📚 *Bookshelf Catalog Bot*\n\n"
        "Send me a photo of your bookshelf and I'll catalog all the books!\n\n"
        "*How it works:*\n"
        "1️⃣ Take a photo of your bookshelf (spines visible)\n"
        "2️⃣ Send the photo to me\n"
        "3️⃣ I'll recognize the books using AI\n"
        "4️⃣ Enrich with data from Google Books\n"
        "5️⃣ Save everything to your Google Sheet\n\n"
        "*Commands:*\n"
        "/start — this message\n"
        "/sheet — link to your catalog spreadsheet\n\n"
        "You can send multiple photos to keep adding books! "
        "Duplicates are automatically skipped."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sheet command — return link to Google Sheet."""
    await update.message.reply_text("⏳ Getting your sheet URL...")
    try:
        url = _sheet.get_url()
        await update.message.reply_text(
            f"📊 Your catalog: {url}",
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "❌ Google authorization not set up yet.\n\n"
            "Run this command once on the machine running the bot:\n"
            "```\npython bookshelf/oauth_setup.py\n```",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error getting sheet URL: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming photos — the main bot workflow.

    Steps:
    1. Download photo (highest quality version)
    2. Recognize books with VLM
    3. Enrich with Google Books API
    4. Add to Google Sheets (dedup)
    5. Reply with results
    """
    msg = await update.message.reply_text("🔍 Analyzing your bookshelf...")

    try:
        # 1. Download photo
        photo = update.message.photo[-1]  # Highest resolution
        photo_file = await photo.get_file()
        image_data = await photo_file.download_as_bytearray()
        image_bytes = bytes(image_data)

        logger.info(f"Photo received: {len(image_bytes)} bytes from user {update.effective_user.id}")

        # 2. Recognize books via VLM
        await msg.edit_text("🤖 Recognizing books with AI...")
        try:
            raw_books = await vision.recognize_books(image_bytes)
        except EnvironmentError as e:
            await msg.edit_text(
                f"❌ OpenRouter not configured.\n\n"
                f"Add `OPENROUTER_API_KEY` to `bookshelf/.env`\n\n"
                f"Error: {e}"
            )
            return
        except RuntimeError as e:
            await msg.edit_text(f"❌ AI recognition failed: {e}")
            return

        if not raw_books:
            await msg.edit_text(
                "😕 Couldn't recognize any books in this photo.\n\n"
                "Tips:\n"
                "• Make sure book spines are clearly visible\n"
                "• Good lighting helps\n"
                "• Try a closer shot"
            )
            return

        logger.info(f"VLM recognized {len(raw_books)} books")
        await msg.edit_text(f"📖 Found {len(raw_books)} books, looking up details...")

        # 3. Enrich with Google Books API
        enriched_books = await books_api.enrich_books(raw_books)
        logger.info(f"Enriched {len(enriched_books)} books")

        # 4. Add to Google Sheets
        await msg.edit_text("💾 Saving to Google Sheets...")
        try:
            added, skipped = _sheet.add_books(enriched_books)
        except FileNotFoundError:
            await msg.edit_text(
                "❌ Google Sheets not set up.\n\n"
                "Run once on the server:\n"
                "```\npython bookshelf/oauth_setup.py\n```",
                parse_mode="Markdown",
            )
            return
        except Exception as e:
            logger.error(f"Error saving to sheets: {e}", exc_info=True)
            await msg.edit_text(f"❌ Error saving to Google Sheets: {e}")
            return

        # 5. Build and send result message
        result_text = _format_result(added, skipped)
        await msg.edit_text(result_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Unexpected error handling photo: {e}", exc_info=True)
        await msg.edit_text(f"❌ Unexpected error: {e}")


def _format_result(added: list[dict], skipped: list[dict]) -> str:
    """Format the result message after processing a photo."""
    lines = []

    if added:
        lines.append(f"✅ Added {len(added)} new books:")
        for book in added:
            title = book.get("Title", "Unknown")
            author = book.get("Author", "")
            if author:
                lines.append(f"  • *{_escape_md(title)}* — {_escape_md(author)}")
            else:
                lines.append(f"  • *{_escape_md(title)}*")

    if skipped:
        lines.append(f"\n⏭️ Skipped {len(skipped)} duplicates:")
        for book in skipped:
            title = book.get("Title", "Unknown")
            lines.append(f"  • {_escape_md(title)}")

    if not added and not skipped:
        lines.append("😕 No books were added.")

    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escape special Markdown characters."""
    chars = r"\_*[]()~`>#+-=|{}.!"
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set! Add it to bookshelf/.env")
        sys.exit(1)

    # Validate Google Sheets setup early
    token_path = Path(__file__).parent / "token.json"
    if not token_path.exists():
        logger.warning(
            "token.json not found. Google Sheets integration won't work.\n"
            "Run: python bookshelf/oauth_setup.py"
        )
    else:
        logger.info("token.json found — Google Sheets ready")

    logger.info("Starting Bookshelf Catalog Bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
