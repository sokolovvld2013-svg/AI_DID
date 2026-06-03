"""Транскрибация аудио через faster-whisper."""
import logging
import os
from pathlib import Path

from config import (
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_CONDITION_ON_PREVIOUS,
    WHISPER_CPU_THREADS,
    WHISPER_DEVICE,
    WHISPER_LOG_EVERY_N_SEGMENTS,
    WHISPER_MODEL_SIZE,
    WHISPER_VAD_FILTER,
)

logger = logging.getLogger(__name__)

_model = None


def _cpu_threads() -> int:
    if WHISPER_CPU_THREADS > 0:
        return WHISPER_CPU_THREADS
    return os.cpu_count() or 4


def _get_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "Не установлен faster-whisper. На сервере выполните: "
                "pip install faster-whisper — или pip install -r requirements.txt. "
                "Также нужен ffmpeg: sudo apt install -y ffmpeg"
            ) from e

        threads = _cpu_threads()
        logger.info(
            "Загрузка Whisper: size=%s, device=%s, compute=%s, cpu_threads=%d",
            WHISPER_MODEL_SIZE,
            WHISPER_DEVICE,
            WHISPER_COMPUTE_TYPE,
            threads,
        )
        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            cpu_threads=threads,
        )
    return _model


def preload_model() -> None:
    """Предзагрузка модели при старте (WHISPER_PRELOAD=true)."""
    _get_model()
    logger.info("Whisper готов к транскрибации")


def transcribe(audio_path: Path, language: str = "ru") -> str:
    """Транскрибация аудиофайла в текст."""
    model = _get_model()
    logger.info(
        "Транскрибация: %s (model=%s, beam=%d, vad=%s)",
        audio_path,
        WHISPER_MODEL_SIZE,
        WHISPER_BEAM_SIZE,
        WHISPER_VAD_FILTER,
    )

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=WHISPER_BEAM_SIZE,
        best_of=1,
        temperature=0,
        condition_on_previous_text=WHISPER_CONDITION_ON_PREVIOUS,
        vad_filter=WHISPER_VAD_FILTER,
        word_timestamps=False,
    )

    parts: list[str] = []
    for i, segment in enumerate(segments, start=1):
        parts.append(segment.text.strip())
        if i % WHISPER_LOG_EVERY_N_SEGMENTS == 0:
            logger.info(
                "Транскрибация: сегмент %d, ~%.0f с аудио",
                i,
                segment.end,
            )

    text = " ".join(parts)
    logger.info(
        "Транскрибация завершена, сегментов=%d, длина=%d, язык=%s",
        len(parts),
        len(text),
        info.language,
    )
    return text
