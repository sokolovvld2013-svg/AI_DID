"""Транскрибация аудио через faster-whisper."""
import logging
from pathlib import Path

from config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL_SIZE

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info(
            "Загрузка Whisper: size=%s, device=%s",
            WHISPER_MODEL_SIZE,
            WHISPER_DEVICE,
        )
        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _model


def transcribe(audio_path: Path, language: str = "ru") -> str:
    """Транскрибация аудиофайла в текст."""
    model = _get_model()
    logger.info("Транскрибация: %s", audio_path)

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
    )

    parts = []
    for segment in segments:
        parts.append(segment.text.strip())

    text = " ".join(parts)
    logger.info("Транскрибация завершена, длина=%d, язык=%s", len(text), info.language)
    return text
