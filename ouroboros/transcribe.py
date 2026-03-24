"""
ouroboros/transcribe.py — Audio transcription via faster-whisper.

Part of the Voice Note Alchemist pipeline.

Install:
    pip install faster-whisper

Hardware notes (measured on Colab CPU, 2-core Xeon):
    - tiny  (int8): ~1.8x realtime — 1 min audio ≈ 33s   ✅ default
    - base  (int8): <0.5x realtime — too slow on CPU-only ❌
    - small+: requires T4 GPU for acceptable latency

If a T4 GPU is available (CUDA), the model param can be bumped to
'small' or 'medium' without code changes.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ── Model selection ──────────────────────────────────────────────────────────

ModelSize = Literal["tiny", "tiny.en", "base", "base.en", "small", "small.en",
                    "medium", "medium.en", "large-v2", "large-v3"]

DEFAULT_MODEL: ModelSize = "tiny"          # safe default: works on CPU
DEFAULT_COMPUTE_TYPE = "int8"              # lowest memory, fastest on CPU
DEFAULT_DEVICE = "auto"                    # "auto" picks CUDA if available

# ── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_faster_whisper() -> None:
    """Auto-install faster-whisper if not present (Colab-friendly)."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        logger.info("faster-whisper not found — installing...")
        subprocess.run(
            ["pip", "install", "-q", "faster-whisper"],
            check=True,
        )
        logger.info("faster-whisper installed.")


def _convert_to_wav(src: str | Path) -> str:
    """
    Convert any audio file to 16 kHz mono WAV using ffmpeg.

    Returns path to a temp WAV file (caller is responsible for cleanup).
    Raises FileNotFoundError if src doesn't exist.
    Raises RuntimeError if ffmpeg conversion fails.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"Audio file not found: {src}")

    suffix = src.suffix.lower()
    if suffix == ".wav":
        # Already WAV — still re-encode to enforce 16kHz mono
        pass

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ar", "16000",   # 16 kHz sample rate (Whisper native)
        "-ac", "1",       # mono
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


# ── Main class ───────────────────────────────────────────────────────────────

class Transcriber:
    """
    Thin wrapper around faster-whisper for voice note transcription.

    Usage:
        t = Transcriber()                          # lazy-loads model on first call
        text = t.transcribe("voice_note.ogg")

    The model is loaded once and reused across calls.
    """

    def __init__(
        self,
        model_size: ModelSize = DEFAULT_MODEL,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        language: str | None = None,
    ) -> None:
        """
        Args:
            model_size:    faster-whisper model name. Default: "tiny" (CPU-safe).
            device:        "auto" | "cpu" | "cuda". "auto" picks CUDA if available.
            compute_type:  "int8" | "float16" | "float32". int8 is fastest on CPU.
            language:      ISO 639-1 code (e.g. "en", "ru") or None for auto-detect.
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None  # lazy init

    def _load_model(self):
        """Load (or return cached) faster-whisper model."""
        if self._model is not None:
            return self._model

        _ensure_faster_whisper()
        from faster_whisper import WhisperModel  # noqa: PLC0415

        logger.info(
            "Loading faster-whisper model '%s' (device=%s, compute=%s)...",
            self.model_size, self.device, self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Model loaded.")
        return self._model

    def transcribe(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file and return the full text.

        Handles any format supported by ffmpeg (OGG, MP3, M4A, WAV, etc.).
        The audio is converted to 16 kHz mono WAV before transcription.

        Args:
            file_path: Path to the audio file.

        Returns:
            Full transcribed text as a single string.

        Raises:
            FileNotFoundError: If the audio file doesn't exist.
            RuntimeError: If ffmpeg conversion fails.
        """
        file_path = Path(file_path)

        # Convert to WAV (raises FileNotFoundError / RuntimeError on failure)
        wav_path = _convert_to_wav(file_path)

        try:
            model = self._load_model()

            logger.info("Transcribing %s...", file_path.name)
            segments, info = model.transcribe(
                wav_path,
                language=self.language,
                beam_size=5,
                vad_filter=True,           # skip silent regions
                vad_parameters={
                    "min_silence_duration_ms": 500,
                },
            )

            # Materialise the generator into text
            text = " ".join(seg.text.strip() for seg in segments).strip()

            logger.info(
                "Transcribed %.1fs of audio (%s) → %d chars",
                info.duration, info.language, len(text),
            )
            return text

        finally:
            # Always clean up temp WAV
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def transcribe_with_metadata(self, file_path: str | Path) -> dict:
        """
        Like transcribe(), but returns a dict with text + metadata.

        Returns:
            {
                "text": str,
                "language": str,       # detected language code
                "duration": float,     # audio duration in seconds
                "segments": [          # individual segments with timestamps
                    {"start": float, "end": float, "text": str},
                    ...
                ]
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

            segments = []
            for seg in segments_gen:
                segments.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                })

            text = " ".join(s["text"] for s in segments).strip()

            logger.info(
                "Transcribed %.1fs of %s audio → %d chars, %d segments",
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


# ── CLI / smoke test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        # No file provided — just confirm the stack loads
        print("Transcriber ready.")
        print(f"  Model:        {DEFAULT_MODEL}")
        print(f"  Compute type: {DEFAULT_COMPUTE_TYPE}")
        print(f"  Device:       {DEFAULT_DEVICE}")
        print()
        print("Usage: python -m ouroboros.transcribe <audio_file>")
        sys.exit(0)

    audio_file = sys.argv[1]
    model_size = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL

    print(f"Transcribing: {audio_file}  (model={model_size})")

    t = Transcriber(model_size=model_size)
    try:
        result = t.transcribe_with_metadata(audio_file)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"\n── Transcript ({result['language']}, {result['duration']}s) ──")
    print(result["text"])

    if result["segments"]:
        print(f"\n── Segments ({len(result['segments'])}) ──")
        for seg in result["segments"][:5]:  # show first 5
            print(f"  [{seg['start']:6.2f}s → {seg['end']:6.2f}s]  {seg['text']}")
        if len(result["segments"]) > 5:
            print(f"  ... ({len(result['segments']) - 5} more)")
