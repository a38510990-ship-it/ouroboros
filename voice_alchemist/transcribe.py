"""
voice_alchemist/transcribe.py — Self-contained audio transcription via faster-whisper.

No ouroboros/ imports. Standalone for use in the Voice Note Alchemist mini-app.

Install:
    pip install faster-whisper

Hardware notes (measured on Colab CPU, 2-core Xeon):
    - tiny  (int8): ~1.8x realtime — 1 min audio ≈ 33s   ✅ default
    - base  (int8): <0.5x realtime — too slow on CPU-only ❌
    - small+: requires T4 GPU for acceptable latency

If a T4 GPU is available (CUDA), bump model_size to 'small' or 'medium'.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ModelSize = Literal[
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v2", "large-v3",
]

DEFAULT_MODEL: ModelSize = "tiny"
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_DEVICE = "auto"


def _ensure_faster_whisper() -> None:
    """Auto-install faster-whisper if not present (Colab-friendly)."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        logger.info("faster-whisper not found — installing...")
        subprocess.run(["pip", "install", "-q", "faster-whisper"], check=True)
        logger.info("faster-whisper installed.")


def _convert_to_wav(src: str | Path) -> str:
    """
    Convert any audio file to 16 kHz mono WAV using ffmpeg.

    Returns path to a temp WAV file (caller must delete it).
    Raises FileNotFoundError if src doesn't exist.
    Raises RuntimeError if ffmpeg conversion fails.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"Audio file not found: {src}")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ar", "16000",  # 16 kHz (Whisper native)
        "-ac", "1",      # mono
        "-f", "wav",
        tmp.name,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        os.unlink(tmp.name)
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}):\n{result.stderr}"
        )

    logger.debug("Converted %s → %s", src, tmp.name)
    return tmp.name


class Transcriber:
    """
    Thin wrapper around faster-whisper for voice note transcription.

    Usage:
        t = Transcriber()
        text = t.transcribe("voice_note.ogg")       # → str
        result = t.transcribe_with_metadata("note.m4a")  # → dict

    The model is loaded once and reused across calls.
    """

    def __init__(
        self,
        model_size: ModelSize = DEFAULT_MODEL,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        language: str | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        _ensure_faster_whisper()
        from faster_whisper import WhisperModel  # noqa: PLC0415

        logger.info(
            "Loading faster-whisper '%s' (device=%s, compute=%s)...",
            self.model_size, self.device, self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Whisper model loaded.")
        return self._model

    def transcribe(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file, return the full text as a string.

        Handles any ffmpeg-supported format (OGG, MP3, M4A, WAV, AAC, WebM).
        Returns empty string if no speech is detected.
        """
        file_path = Path(file_path)
        wav_path = _convert_to_wav(file_path)

        try:
            model = self._load_model()
            logger.info("Transcribing %s...", file_path.name)
            segments, info = model.transcribe(
                wav_path,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            logger.info(
                "Transcribed %.1fs (%s) → %d chars",
                info.duration, info.language, len(text),
            )
            return text
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def transcribe_with_metadata(self, file_path: str | Path) -> dict:
        """
        Like transcribe(), but returns a dict:
            {
                "text": str,
                "language": str,
                "duration": float,
                "segments": [{"start": float, "end": float, "text": str}, ...]
            }
        """
        file_path = Path(file_path)
        wav_path = _convert_to_wav(file_path)

        try:
            model = self._load_model()
            segments_gen, info = model.transcribe(
                wav_path,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            segments = [
                {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                for s in segments_gen
            ]
            text = " ".join(s["text"] for s in segments).strip()
            logger.info(
                "Transcribed %.1fs (%s) → %d chars, %d segments",
                info.duration, info.language, len(text), len(segments),
            )
            return {
                "text": text,
                "language": info.language,
                "duration": round(info.duration, 2),
                "segments": segments,
            }
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)
