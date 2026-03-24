"""
ouroboros/voice_bot.py — Voice Note Alchemist Telegram Bot.

Receives voice/audio messages, transcribes them with faster-whisper,
optionally summarises long text via LLM, and saves results to Drive.

Usage:
    python -m ouroboros.voice_bot

Environment variables:
    TELEGRAM_TOKEN          — bot token (required)
    OPENROUTER_API_KEY      — for LLM summarisation (optional, falls back gracefully)
    DRIVE_ROOT              — Drive base path (default: /content/drive/MyDrive/Ouroboros)
    WHISPER_MODEL           — faster-whisper model size (default: tiny)
    SUMMARY_MIN_CHARS       — char threshold to trigger summarisation (default: 500)
    OUROBOROS_MODEL_LIGHT   — LLM model for summaries (default: google/gemini-2.0-flash-001)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update, Message
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ouroboros.transcribe import Transcriber

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

MAX_FILE_SIZE_MB = 20
SUMMARY_MIN_CHARS = int(os.environ.get("SUMMARY_MIN_CHARS", 500))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny")
DRIVE_ROOT = os.environ.get(
    "DRIVE_ROOT", "/content/drive/MyDrive/Ouroboros"
)
PROCESSED_DIR = Path(DRIVE_ROOT) / "voice_alchemist" / "processed"
SUMMARY_MODEL = os.environ.get(
    "OUROBOROS_MODEL_LIGHT", "google/gemini-2.0-flash-001"
)

# ── Lazy singletons — loaded once, reused across all messages ─────────────────

_transcriber: Transcriber | None = None
_llm_client = None  # LLMClient, optional — only loaded if OPENROUTER_API_KEY present


def _get_transcriber() -> Transcriber:
    global _transcriber
    if _transcriber is None:
        logger.info("Loading Whisper model '%s'...", WHISPER_MODEL)
        _transcriber = Transcriber(model_size=WHISPER_MODEL)
    return _transcriber


def _get_llm():
    """Return LLMClient if OPENROUTER_API_KEY is available, else None."""
    global _llm_client
    if _llm_client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if api_key:
            from ouroboros.llm import LLMClient  # noqa: PLC0415
            _llm_client = LLMClient(api_key=api_key)
            logger.info("LLM client ready (model=%s)", SUMMARY_MODEL)
        else:
            logger.warning(
                "OPENROUTER_API_KEY not set — LLM summarisation disabled."
            )
            # Set a sentinel so we don't retry every call
            _llm_client = False  # type: ignore[assignment]
    return _llm_client if _llm_client else None


# ── Drive helpers ─────────────────────────────────────────────────────────────

def _ensure_processed_dir() -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_DIR


def _save_transcript(
    text: str,
    summary: str | None,
    user_id: int,
    message_id: int,
    date: datetime,
) -> Path:
    """Persist transcript (and optional summary) to Drive. Returns the file path."""
    out_dir = _ensure_processed_dir()
    date_str = date.strftime("%Y-%m-%d")
    filename = f"{date_str}_{user_id}_{message_id}.txt"
    out_path = out_dir / filename

    lines = [
        "Voice Note Transcript",
        f"Date:       {date.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"User ID:    {user_id}",
        f"Message ID: {message_id}",
        "",
        "── Transcript ──",
        text,
    ]

    if summary:
        lines += [
            "",
            "── Summary ──",
            summary,
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Saved transcript → %s (%d chars)", out_path, len(text))
    return out_path


# ── LLM summarisation (async, non-blocking) ───────────────────────────────────

async def _summarise(text: str) -> str | None:
    """Ask the LLM for a summary. Returns None if LLM unavailable or on error."""
    llm = _get_llm()
    if llm is None:
        return None

    prompt = (
        "You are a voice note assistant. Below is a transcription of a voice message. "
        "Produce a clear, concise summary in the same language as the original. "
        "If there are action items, list them as bullets at the end.\n\n"
        f"Transcription:\n{text}"
    )

    try:
        loop = asyncio.get_event_loop()
        msg, _usage = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=SUMMARY_MODEL,
                reasoning_effort="low",
                max_tokens=1024,
            ),
        )
        content = (msg.get("content") or "").strip()
        return content if content else None
    except Exception as exc:
        logger.warning("LLM summarisation failed: %s", exc)
        return None


# ── Core message handler ──────────────────────────────────────────────────────

async def _handle_audio_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle both Voice and Audio messages identically."""
    message: Message = update.effective_message
    user = update.effective_user
    user_id = user.id if user else 0
    message_id = message.message_id

    # Prefer Voice, fall back to Audio
    audio_obj = message.voice or message.audio
    if audio_obj is None:
        return  # shouldn't happen given our filters

    # ── Size guard ──────────────────────────────────────────────────────────
    file_size_bytes = audio_obj.file_size or 0
    file_size_mb = file_size_bytes / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        await message.reply_text(
            f"❌ File too large ({file_size_mb:.1f} MB). "
            f"Maximum supported: {MAX_FILE_SIZE_MB} MB."
        )
        return

    # ── Acknowledge receipt ─────────────────────────────────────────────────
    status_msg = await message.reply_text("🎙️ Received! Transcribing…")

    try:
        # ── Download ──────────────────────────────────────────────────────
        tg_file = await audio_obj.get_file()

        # Pick a sensible extension from the mime type
        mime = getattr(audio_obj, "mime_type", None) or "audio/ogg"
        ext_map = {
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/webm": ".webm",
            "audio/x-m4a": ".m4a",
        }
        suffix = ext_map.get(mime, ".ogg")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await tg_file.download_to_drive(tmp_path)
            logger.info(
                "Downloaded audio → %s (%.2f MB, user=%s, msg=%s)",
                tmp_path, file_size_mb, user_id, message_id,
            )

            # ── Transcribe (blocking, run in thread pool) ─────────────────
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(
                None,
                lambda: _get_transcriber().transcribe(tmp_path),
            )

        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # ── Empty transcript ───────────────────────────────────────────────
        if not transcript:
            await status_msg.edit_text(
                "🔇 No speech detected in this recording.\n"
                "Try again with a clearer audio clip."
            )
            return

        # ── Optional summarisation ─────────────────────────────────────────
        summary: str | None = None
        if len(transcript) >= SUMMARY_MIN_CHARS:
            await status_msg.edit_text("📝 Transcribed! Summarising…")
            summary = await _summarise(transcript)

        # ── Save to Drive ──────────────────────────────────────────────────
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

        # ── Build reply ────────────────────────────────────────────────────
        word_count = len(transcript.split())
        char_count = len(transcript)

        reply_parts = [
            "✅ <b>Transcription complete</b>",
            f"<i>{word_count} words · {char_count} chars</i>",
            "",
            "<b>📄 Transcript:</b>",
            transcript,
        ]

        if summary:
            reply_parts += ["", "<b>💡 Summary:</b>", summary]

        reply_parts += ["", f"<i>💾 Saved: {saved_path.name}</i>"]

        reply_text = "\n".join(reply_parts)

        # Telegram hard-caps messages at 4096 chars — truncate gracefully
        MAX_REPLY = 4000
        if len(reply_text) > MAX_REPLY:
            # First try truncating just the transcript
            budget = MAX_REPLY - len("\n".join(reply_parts[:3])) - len(
                "\n".join(reply_parts[5:])
            ) - 20
            truncated_transcript = (
                transcript[:budget] + "…" if budget > 0 else "[too long to display]"
            )
            reply_parts[4] = truncated_transcript
            reply_text = "\n".join(reply_parts)

            # Still too long? Drop transcript entirely, keep summary
            if len(reply_text) > MAX_REPLY:
                fallback = [
                    "✅ <b>Transcription complete</b>",
                    f"<i>{word_count} words · {char_count} chars — too long to display here</i>",
                ]
                if summary:
                    fallback += ["", "<b>💡 Summary:</b>", summary]
                fallback += ["", f"<i>💾 Full text saved: {saved_path.name}</i>"]
                reply_text = "\n".join(fallback)

        await status_msg.edit_text(reply_text, parse_mode=ParseMode.HTML)

        logger.info(
            "Done: user=%s msg=%s chars=%d summary=%s saved=%s",
            user_id, message_id, char_count,
            "yes" if summary else "no",
            saved_path.name,
        )

    except Exception as exc:
        logger.error(
            "Error processing voice note (user=%s msg=%s): %s",
            user_id, message_id, exc, exc_info=True,
        )
        await status_msg.edit_text(
            f"❌ Something went wrong during transcription.\n"
            f"<code>{type(exc).__name__}: {exc}</code>",
            parse_mode=ParseMode.HTML,
        )


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.message.reply_text(
        f"👋 Hey {name}!\n\n"
        f"I'm the <b>Voice Note Alchemist</b> 🎙️✨\n\n"
        f"Send me a voice message or audio file and I'll:\n"
        f"• 📄 Transcribe it to text\n"
        f"• 💡 Summarise it if it's long (&gt;{SUMMARY_MIN_CHARS} chars)\n"
        f"• 💾 Save the result to your Google Drive\n\n"
        f"<i>Supported formats: OGG, MP3, M4A, WAV, AAC, WebM</i>\n\n"
        f"Just send me a voice note to get started!",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>Voice Note Alchemist — Help</b>\n\n"
        "<b>Commands:</b>\n"
        "/start — Introduction\n"
        "/help  — This message\n\n"
        "<b>Supported formats:</b>\n"
        "Voice messages, MP3, M4A, OGG, WAV, AAC, WebM\n\n"
        "<b>Limits:</b>\n"
        f"• Max file size: {MAX_FILE_SIZE_MB} MB\n"
        f"• Summaries for transcripts &gt;{SUMMARY_MIN_CHARS} chars\n\n"
        "<b>Output location:</b>\n"
        "<code>MyDrive/Ouroboros/voice_alchemist/processed/</code>\n\n"
        "<b>File naming:</b>\n"
        "<code>YYYY-MM-DD_&lt;user_id&gt;_&lt;msg_id&gt;.txt</code>",
        parse_mode=ParseMode.HTML,
    )


# ── App wiring ────────────────────────────────────────────────────────────────

def build_application(token: str):
    """Build and return the configured Telegram Application (for testing or reuse)."""
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.VOICE, _handle_audio_message))
    app.add_handler(MessageHandler(filters.AUDIO, _handle_audio_message))

    return app


def main() -> None:
    """Run the bot in long-polling mode (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TELEGRAM_TOKEN environment variable is not set.\n"
            "Example:  export TELEGRAM_TOKEN='123456:ABC-DEF...'"
        )

    logger.info("━━ Voice Note Alchemist ━━")
    logger.info("  Whisper model:     %s", WHISPER_MODEL)
    logger.info("  Summary threshold: %d chars", SUMMARY_MIN_CHARS)
    logger.info("  Summary model:     %s", SUMMARY_MODEL)
    logger.info("  Processed dir:     %s", PROCESSED_DIR)
    logger.info("  LLM available:     %s", bool(os.environ.get("OPENROUTER_API_KEY")))

    app = build_application(token)
    logger.info("Bot polling — Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
