"""WhisperX word-level transcript 추출 (CUDA + 한국어).

모듈 레벨 cache + lazy import로 CLI 시작 시간 절감.
"""
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import settings
from app.storage.db import get_connection, mark_processed
from app.storage.models import Transcript, TranscriptSegment, TranscriptWord, Video
from app.utils.logger import get_logger

log = get_logger(__name__)

_asr_model: Any = None
_align_cache: dict[str, tuple[Any, Any]] = {}


@contextmanager
def _torch_load_compat() -> Iterator[None]:
    """WhisperX/pyannote 모델 로드 구간에만 weights_only=False 적용.

    PyTorch 2.6+의 `torch.load(weights_only=True)` 기본값이 pyannote/lightning의
    옛 pickle 체크포인트와 비호환. HF Hub 공식 모델만 신뢰해서 우회.
    전역 패치 대신 contextmanager로 격리.
    """
    import torch

    original = torch.load

    def patched(*args: Any, **kwargs: Any) -> Any:
        kwargs["weights_only"] = False
        return original(*args, **kwargs)

    torch.load = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        torch.load = original  # type: ignore[assignment]


def _language_for_channel(channel: str) -> str:
    """channel → whisperx language ('ko'/'en'). default config 값(ko)."""
    if channel == "en":
        return "en"
    return settings.whisperx_language


# ASR 모델은 language별로 별도 로드. dict[language] = model.
_asr_models: dict[str, Any] = {}


def _get_asr_model(language: str) -> Any:
    """WhisperX ASR 모델 lazy 로드 (language별 별도 캐시)."""
    cached = _asr_models.get(language)
    if cached is not None:
        return cached

    import whisperx

    log.info(
        "transcribe.loading_asr_model",
        model=settings.whisperx_model,
        device=settings.whisperx_device,
        compute_type=settings.whisperx_compute_type,
        language=language,
    )
    with _torch_load_compat():
        model = whisperx.load_model(
            settings.whisperx_model,
            device=settings.whisperx_device,
            compute_type=settings.whisperx_compute_type,
            language=language,
        )
    _asr_models[language] = model
    log.info("transcribe.asr_model_loaded", language=language)
    return model


def _get_align_model(language: str, device: str) -> tuple[Any, Any]:
    """언어별 alignment 모델 lazy 로드 + 캐시."""
    key = f"{language}:{device}"
    cached = _align_cache.get(key)
    if cached is not None:
        return cached

    import whisperx

    log.info("transcribe.loading_align_model", language=language)
    with _torch_load_compat():
        model, metadata = whisperx.load_align_model(
            language_code=language, device=device,
        )
    _align_cache[key] = (model, metadata)
    log.info("transcribe.align_model_loaded", language=language)
    return _align_cache[key]


def _run_whisperx(audio_path: Path, language: str) -> dict:
    """WhisperX 전체 pipeline: load_audio → transcribe → align."""
    import whisperx

    audio = whisperx.load_audio(str(audio_path))
    asr = _get_asr_model(language)

    log.info("transcribe.asr_run_start", path=str(audio_path), language=language)
    raw = asr.transcribe(audio, batch_size=settings.whisperx_batch_size)
    log.info(
        "transcribe.asr_run_done",
        segments=len(raw.get("segments", [])),
        language=raw.get("language"),
    )

    align_model, align_meta = _get_align_model(
        language, settings.whisperx_device,
    )

    log.info("transcribe.align_run_start")
    aligned = whisperx.align(
        raw["segments"], align_model, align_meta, audio,
        settings.whisperx_device, return_char_alignments=False,
    )
    log.info("transcribe.align_run_done")

    return {
        "language": raw.get("language", settings.whisperx_language),
        "segments": aligned["segments"],
    }


def _dict_to_transcript(raw: dict) -> Transcript:
    """WhisperX 출력 dict → Transcript 모델 (방어적 파싱)."""
    segments: list[TranscriptSegment] = []
    for seg in raw.get("segments", []) or []:
        words: list[TranscriptWord] = []
        for w in seg.get("words", []) or []:
            words.append(
                TranscriptWord(
                    start=float(w.get("start") or seg.get("start") or 0.0),
                    end=float(w.get("end") or seg.get("end") or 0.0),
                    text=str(w.get("word", "")).strip(),
                    score=w.get("score"),
                )
            )
        segments.append(
            TranscriptSegment(
                start=float(seg.get("start") or 0.0),
                end=float(seg.get("end") or 0.0),
                text=str(seg.get("text", "")).strip(),
                words=words,
            )
        )
    return Transcript(
        language=str(raw.get("language") or settings.whisperx_language),
        segments=segments,
    )


def transcribe(video: Video) -> Transcript:
    """영상 transcribe + JSON 저장. 캐시 hit 시 즉시 반환."""
    out_path = settings.transcripts_dir / f"{video.youtube_id}.json"

    if out_path.exists():
        log.info(
            "transcribe.cache_hit",
            youtube_id=video.youtube_id, path=str(out_path),
        )
        return Transcript.model_validate_json(
            out_path.read_text(encoding="utf-8"),
        )

    if not video.local_path.exists():
        raise FileNotFoundError(
            f"Video file not found: {video.local_path}. "
            "ingest()를 먼저 실행하세요."
        )

    t0 = time.time()
    language = _language_for_channel(video.channel)
    raw = _run_whisperx(video.local_path, language)
    transcript = _dict_to_transcript(raw)
    elapsed = time.time() - t0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        transcript.model_dump_json(indent=2),
        encoding="utf-8",
    )

    total_words = sum(len(s.words) for s in transcript.segments)
    log.info(
        "transcribe.done",
        youtube_id=video.youtube_id,
        segments=len(transcript.segments),
        words=total_words,
        elapsed_sec=round(elapsed, 2),
        path=str(out_path),
    )

    conn = get_connection()
    try:
        mark_processed(conn, video.youtube_id)
    finally:
        conn.close()

    return transcript
