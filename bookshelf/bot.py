#!/usr/bin/env python3
"""
Bookshelf Catalog Telegram Bot
================================
Send a photo of your bookshelf → get a Google Sheets catalog.

Usage:
    1. Run auth_google.py once to authenticate with Google
    2. Set env variables (copy env.example → .env and fill in)
    3. python bot.py

Commands:
    /start   - Welcome message & instructions
    /status  - Show catalog link (if already created)
    /help    - Help text
"""
import logging
import os
import tempfile
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

from vision import recognize_books
from books_api import enrich_books
from sheets import append_books

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", "token.json")

# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "📚 *Bookshelf Catalog Bot*\n\n"
        "Send me a photo of your bookshelf and I will:\n"
        "1. Recognize all the books using AI vision\n"
        "2. Enrich with metadata from Google Books\n"
        "3. Save everything to your Google Sheet\n\n"
        "Just send a photo to get started!\n"
        "_(Make sure book spines are visible and readable)_",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        "📖 *How to use:*\n\n"
        "• Send a photo of your bookshelf\n"
        "• I'll scan the books and create/update your catalog\n"
        "• Each new photo *adds* books to the same sheet (no duplicates check yet)\n\n"
        "📋 *Commands:*\n"
        "/start — Welcome message\n"
        "/status — Show your catalog link\n"
        "/help — This message\n\n"
        "💡 *Tips for best results:*\n"
        "• Good lighting\n"
        "• Book spines facing forward\n"
        "• Photo taken straight-on (not at angle)",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command — show current sheet URL."""
    sheet_url = context.bot_data.get("sheet_url")
    if sheet_url:
        await update.message.reply_text(
            f"📊 Your catalog:\n{sheet_url}",
        )
    else:
        await update.message.reply_text(
            "No catalog yet. Send me a photo of your bookshelf to create one!"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler: receive photo → VLM → Google Books → Sheets."""
    msg = update.message
    await msg.reply_text("📸 Photo received! Analyzing your bookshelf...")

    # Download the highest-resolution photo
    photo = msg.photo[-1]  # last = largest
    photo_file = await photo.get_file()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await photo_file.download_to_drive(tmp_path)
        logger.info(f"Photo downloaded to: {tmp_path}")

        # Step 1: VLM recognition
        await msg.reply_text("🔍 Recognizing books with AI vision...")
        try:
            books_raw = recognize_books(tmp_path, OPENROUTER_API_KEY)
        except Exception as e:
            logger.error(f"VLM error: {e}")
            await msg.reply_text(f"❌ Vision error: {e}")
            return

        if not books_raw:
            await msg.reply_text(
                "😕 I couldn't recognize any books in this photo.\n"
                "Try a clearer photo with visible book spines."
            )
            return

        await msg.reply_text(f"📚 Found *{len(books_raw)} books*! Fetching metadata...", parse_mode="Markdown")
        logger.info(f"Raw books: {books_raw}")

        # Step 2: Enrich with Google Books
        books_enriched = enrich_books(books_raw)

        # Step 3: Save to Google Sheets
        await msg.reply_text("💾 Saving to Google Sheets...")
        try:
            sheet_url = append_books(books_enriched, GOOGLE_SHEET_NAME, TOKEN_PATH)
        except FileNotFoundError as e:
            await msg.reply_text(
                f"❌ Google auth error: {e}\n\n"
                "Run `python auth_google.py` on the server to authenticate."
            )
            return
        except Exception as e:
            logger.error(f"Sheets error: {e}")
            await msg.reply_text(f"❌ Google Sheets error: {e}")
            return

        # Cache sheet URL
        context.bot_data["sheet_url"] = sheet_url

        # Build summary of recognized books
        book_list = "\n".join(
            f"• *{b.get('title', '?')}*" + (f" — {b.get('author', '')}" if b.get('author') else "")
            for b in books_raw[:20]  # max 20 in message
        )
        if len(books_raw) > 20:
            book_list += f"\n_...and {len(books_raw) - 20} more_"

        await msg.reply_text(
            f"✅ *Done!* Added {len(books_enriched)} books to your catalog.\n\n"
            f"{book_list}\n\n"
            f"📊 [Open Google Sheet]({sheet_url})",
            parse_mode="Markdown",
        )

    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document uploads (high-res photos sent as files)."""
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Please send an image (photo or image file).")
        return

    msg = update.message
    await msg.reply_text("📸 Image received! Analyzing your bookshelf...")

    ext = ".jpg"
    if doc.mime_type == "image/png":
        ext = ".png"
    elif doc.mime_type == "image/webp":
        ext = ".webp"

    doc_file = await doc.get_file()

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await doc_file.download_to_drive(tmp_path)
        logger.info(f"Document downloaded to: {tmp_path}")

        await msg.reply_text("🔍 Recognizing books with AI vision...")
        try:
            books_raw = recognize_books(tmp_path, OPENROUTER_API_KEY)
        except Exception as e:
            logger.error(f"VLM error: {e}")
            await msg.reply_text(f"❌ Vision error: {e}")
            return

        if not books_raw:
            await msg.reply_text(
                "😕 I couldn't recognize any books in this photo.\n"
                "Try a clearer photo with visible book spines."
            )
            return

        await msg.reply_text(f"📚 Found *{len(books_raw)} books*! Fetching metadata...", parse_mode="Markdown")

        books_enriched = enrich_books(books_raw)

        await msg.reply_text("💾 Saving to Google Sheets...")
        try:
            sheet_url = append_books(books_enriched, GOOGLE_SHEET_NAME, TOKEN_PATH)
        except FileNotFoundError as e:
            await msg.reply_text(f"❌ Google auth error: {e}")
            return
        except Exception as e:
            logger.error(f"Sheets error: {e}")
            await msg.reply_text(f"❌ Google Sheets error: {e}")
            return

        context.bot_data["sheet_url"] = sheet_url

        book_list = "\n".join(
            f"• *{b.get('title', '?')}*" + (f" — {b.get('author', '')}" if b.get('author') else "")
            for b in books_raw[:20]
        )
        if len(books_raw) > 20:
            book_list += f"\n_...and {len(books_raw) - 20} more_"

        await msg.reply_text(
            f"✅ *Done!* Added {len(books_enriched)} books to your catalog.\n\n"
            f"{book_list}\n\n"
            f"📊 [Open Google Sheet]({sheet_url})",
            parse_mode="Markdown",
        )

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Start the bot."""
    logger.info("Starting Bookshelf Catalog Bot...")

    # Verify Google auth exists
    if not Path(TOKEN_PATH).exists():
        logger.error(
            "token.json not found! Run `python auth_google.py` first."
        )
        raise SystemExit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
