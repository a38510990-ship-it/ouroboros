"""
voice_alchemist/bot.py — Voice Note Alchemist Telegram Bot

Standalone mini-app (no ouroboros/ imports).

Send a voice message or audio file → get back a transcript (+ optional summary).
Results are saved to Google Drive.

Setup (in Colab):
    import subprocess, os
    subprocess.run(["pip", "install", "-q",
        "python-telegram-bot==21.5", "faster-whisper", "httpx", "python-dotenv"], check=True)
    os.chdir("/content/ouroboros_repo/voice_alchemist")
    os.environ["TELEGRAM_TOKEN"] = "YOUR_BOT_TOKEN_HERE"
    # os.environ["OPENROUTER_API_KEY"] = "..."   # enables LLM summaries
    # os.environ["ALLOWED_TELEGRAM_ID"] = "..."  # restrict to one user
    exec(open("bot.py").read())
    main()

Environment variables:
    TELEGRAM_TOKEN          — bot token (required)
    ALLOWED_TELEGRAM_ID     — if set, reject all other users (optional)
    OPENROUTER_API_KEY      — enables LLM summarisation for long transcripts (optional)
    OUROBOROS_MODEL_LIGHT   — LLM model for summaries (default: google/gemini-2.0-flash-001)
    WHISPER_MODEL           — faster-whisper model size (default: tiny)
    SUMMARY_MIN_CHARS       — char threshold to trigger summarisation (default: 500)
    DRIVE_ROOT              — Drive base path (default: /content/drive/MyDrive/Ouroboros)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, Message
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── Load .env from the same directory (works both as exec() and direct run) ──
HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

# ── Logging: stdout + rotating file ─────────────────────────────────────────
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log_file = HERE / "bot.log"

_file_handler = RotatingFileHandler(
    _log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny")
SUMMARY_MIN_CHARS = int(os.environ.get("SUMMARY_MIN_CHARS", 500))
SUMMARY_MODEL = os.environ.get("OUROBOROS_MODEL_LIGHT", "google/gemini-2.0-flash-001")
DRIVE_ROOT = Path(os.environ.get("DRIVE_ROOT", "/content/drive/MyDrive/Ouroboros"))
PROCESSED_DIR = DRIVE_ROOT / "voice_alchemist" / "processed"
MAX_FILE_SIZE_MB = 20
MAX_REPLY_CHARS = 4000

# Security: set to a specific int user ID to restrict access, or None for open
_raw_allowed = os.environ.get("ALLOWED_TELEGRAM_ID", "").strip()
ALLOWED_TELEGRAM_ID: int | None = int(_raw_allowed) if _raw_allowed else None

# ── Lazy singletons ───────────────────────────────────────────────────────────
_transcriber = None  # loaded on first voice message


def _get_transcriber():
    global _transcriber
    if _transcriber is None:
        # Import locally so exec()-ing the module doesn't trigger model load
        import importlib.util, sys

        # Allow both: run from voice_alchemist/ dir or as package
        if (HERE / "transcribe.py").exists():
            spec = importlib.util.spec_from_file_location(
                "_va_transcribe", HERE / "transcribe.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            Transcriber = mod.Transcriber
        else:
            # Fallback: try ouroboros package
            from ouroboros.transcribe import Transcriber  # type: ignore

        logger.info("Loading Whisper model '%s'...", WHISPER_MODEL)
        _transcriber = Transcriber(model_size=WHISPER_MODEL)
    return _transcriber


# ── Drive helpers ─────────────────────────────────────────────────────────────

def _save_transcript(
    text: str,
    summary: str | None,
    user_id: int,
    message_id: int,
    date: datetime,
) -> Path:
    """Save transcript + optional summary to Drive. Returns file path."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    date_str = date.strftime("%Y-%m-%d")
    filename = f"{date_str}_{user_id}_{message_id}.txt"
    out_path = PROCESSED_DIR / filename

    lines = [
        "Voice Note Transcript",
        f"Date:       {date.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"User ID:    {user_id}",
        f"Message ID: {message_id}",
        "",
        "── Transcript ──────────────────────────────────────────────────────",
        text,
    ]
    if summary:
        lines += [
            "",
            "── Summary ─────────────────────────────────────────────────────────",
            summary,
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Saved → %s (%d chars)", out_path, len(text))
    return out_path


# ── LLM summarisation ─────────────────────────────────────────────────────────

def _summarise_sync(text: str) -> str | None:
    """Call OpenRouter synchronously. Runs in a thread-pool executor."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None

    import httpx

    prompt = (
        "You are a voice note assistant. Below is a transcription of a voice message. "
        "Produce a clear, concise summary in the same language as the original. "
        "If there are action items, list them as bullets at the end.\n\n"
        f"Transcription:\n{text}"
    )

    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1024,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        return content if content else None
    except Exception as exc:
        logger.warning("LLM summarisation failed: %s", exc)
        return None


# ── Access guard ──────────────────────────────────────────────────────────────

def _is_allowed(user_id: int) -> bool:
    if ALLOWED_TELEGRAM_ID is None:
        return True
    return user_id == ALLOWED_TELEGRAM_ID


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and not _is_allowed(user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    name = user.first_name if user else "there"
    await update.message.reply_html(
        f"👋 Hey {name}!\n\n"
        f"I'm the <b>Voice Note Alchemist</b> 🎙️✨\n\n"
        f"Send me a voice message or audio file and I'll:\n"
        f"• 📄 Transcribe it to text\n"
        f"• 💡 Summarise it if long (&gt;{SUMMARY_MIN_CHARS} chars)\n"
        f"• 💾 Save the result to your Google Drive\n\n"
        f"<i>Supported: OGG, MP3, M4A, WAV, AAC, WebM · Max {MAX_FILE_SIZE_MB} MB</i>\n\n"
        f"Just send a voice note to get started!"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and not _is_allowed(user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    await update.message.reply_html(
        "<b>Voice Note Alchemist — Help</b>\n\n"
        "<b>Commands:</b>\n"
        "/start — Introduction\n"
        "/help  — This message\n\n"
        "<b>Supported formats:</b>\n"
        "Voice messages, MP3, M4A, OGG, WAV, AAC, WebM\n\n"
        "<b>Limits:</b>\n"
        f"• Max file size: {MAX_FILE_SIZE_MB} MB\n"
        f"• Summaries triggered at &gt;{SUMMARY_MIN_CHARS} chars\n\n"
        "<b>Output location:</b>\n"
        "<code>MyDrive/Ouroboros/voice_alchemist/processed/</code>\n\n"
        "<b>File naming:</b>\n"
        "<code>YYYY-MM-DD_&lt;user_id&gt;_&lt;msg_id&gt;.txt</code>"
    )


# ── Core audio pipeline ───────────────────────────────────────────────────────

MIME_TO_EXT = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
}


async def _handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message: Message = update.effective_message
    user = update.effective_user
    user_id = user.id if user else 0
    message_id = message.message_id

    # ── Access guard ──────────────────────────────────────────────────────
    if not _is_allowed(user_id):
        await message.reply_text("⛔ Access denied.")
        logger.warning("Rejected user %s (not in ALLOWED_TELEGRAM_ID)", user_id)
        return

    audio_obj = message.voice or message.audio
    if audio_obj is None:
        return

    # ── Size guard ────────────────────────────────────────────────────────
    file_size_bytes = audio_obj.file_size or 0
    file_size_mb = file_size_bytes / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        await message.reply_text(
            f"❌ File too large ({file_size_mb:.1f} MB). "
            f"Maximum: {MAX_FILE_SIZE_MB} MB."
        )
        return

    # ── Acknowledge ───────────────────────────────────────────────────────
    status_msg = await message.reply_text("🎙️ Listening... 👂")
    logger.info("Audio received: user=%s msg=%s size=%.2fMB", user_id, message_id, file_size_mb)

    try:
        # ── Download ──────────────────────────────────────────────────────
        tg_file = await audio_obj.get_file()
        mime = getattr(audio_obj, "mime_type", None) or "audio/ogg"
        suffix = MIME_TO_EXT.get(mime, ".ogg")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await tg_file.download_to_drive(tmp_path)
            logger.info("Downloaded → %s", tmp_path)

            # ── Transcribe (in thread pool — non-blocking) ─────────────
            loop = asyncio.get_event_loop()
            transcript: str = await loop.run_in_executor(
                None,
                lambda: _get_transcriber().transcribe(tmp_path),
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # ── Empty transcript ───────────────────────────────────────────
        if not transcript:
            await status_msg.edit_text(
                "🔇 No speech detected in this recording.\n"
                "Try again with clearer audio."
            )
            return

        # ── Optional summarisation ─────────────────────────────────────
        summary: str | None = None
        if len(transcript) >= SUMMARY_MIN_CHARS and os.environ.get("OPENROUTER_API_KEY"):
            await status_msg.edit_text("📝 Transcribed! Summarising...")
            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(None, lambda: _summarise_sync(transcript))

        # ── Save to Drive ──────────────────────────────────────────────
        msg_date = (
            message.date.replace(tzinfo=timezone.utc)
            if message.date
            else datetime.now(timezone.utc)
        )
        saved_path = _save_transcript(
            text=transcript,
            summary=summary,
            user_id=user_id,
            message_id=message_id,
            date=msg_date,
        )

        # ── Build reply ────────────────────────────────────────────────
        word_count = len(transcript.split())
        char_count = len(transcript)

        parts = [
            "✅ <b>Transcription complete</b>",
            f"<i>{word_count} words · {char_count} chars</i>",
            "",
            "<b>📄 Transcript:</b>",
            transcript,
        ]
        if summary:
            parts += ["", "<b>💡 Summary:</b>", summary]
        parts += ["", f"<i>💾 Saved: {saved_path.name}</i>"]

        reply_text = "\n".join(parts)

        # Truncate gracefully if too long
        if len(reply_text) > MAX_REPLY_CHARS:
            fallback = [
                "✅ <b>Transcription complete</b>",
                f"<i>{word_count} words · {char_count} chars — transcript too long to display here</i>",
            ]
            if summary:
                fallback += ["", "<b>💡 Summary:</b>", summary]
            fallback += ["", f"<i>💾 Full text saved: {saved_path.name}</i>"]
            reply_text = "\n".join(fallback)

            # Last resort: if even the fallback is too long
            if len(reply_text) > MAX_REPLY_CHARS:
                reply_text = (
                    f"✅ Transcription complete ({word_count} words).\n"
                    f"<i>💾 Saved: {saved_path.name}</i>"
                )

        await status_msg.edit_text(reply_text, parse_mode=ParseMode.HTML)
        logger.info(
            "Done: user=%s msg=%s chars=%d summary=%s saved=%s",
            user_id, message_id, char_count,
            "yes" if summary else "no", saved_path.name,
        )

    except Exception as exc:
        logger.error(
            "Pipeline error (user=%s msg=%s): %s",
            user_id, message_id, exc, exc_info=True,
        )
        await status_msg.edit_text(
            f"❌ Something went wrong.\n"
            f"<code>{type(exc).__name__}: {exc}</code>",
            parse_mode=ParseMode.HTML,
        )


# ── App wiring ────────────────────────────────────────────────────────────────

def build_app(token: str):
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.VOICE, _handle_audio))
    app.add_handler(MessageHandler(filters.AUDIO, _handle_audio))
    return app


def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TELEGRAM_TOKEN is not set.\n"
            "Set it in .env or via os.environ before calling main()."
        )

    logger.info("━━ Voice Note Alchemist ━━")
    logger.info("  Whisper model:     %s", WHISPER_MODEL)
    logger.info("  Summary threshold: %d chars", SUMMARY_MIN_CHARS)
    logger.info("  Summary model:     %s", SUMMARY_MODEL)
    logger.info("  Processed dir:     %s", PROCESSED_DIR)
    logger.info("  LLM summaries:     %s", "yes" if os.environ.get("OPENROUTER_API_KEY") else "no")
    logger.info("  Access control:    %s", f"user {ALLOWED_TELEGRAM_ID}" if ALLOWED_TELEGRAM_ID else "open")

    app = build_app(token)
    logger.info("Bot polling — Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
