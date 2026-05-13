"""Multi-signal 스코어링: gemini + retention + transcript density 가중 합산."""
from app.config import settings
from app.storage.models import (
    MagicMoment,
    RetentionCurve,
    Transcript,
)
from app.utils.logger import get_logger

log = get_logger(__name__)


def compute_density(
    transcript: Transcript, start: float, end: float,
) -> float:
    """모먼트 구간 [start, end]의 단어 수 / 구간 duration."""
    if end <= start:
        return 0.0
    word_count = 0
    for seg in transcript.segments:
        if seg.end < start or seg.start > end:
            continue
        for w in seg.words:
            if start <= w.start <= end:
                word_count += 1
    return word_count / (end - start)


def _normalize(raw: list[float], scale_max: float = 10.0) -> list[float]:
    """0~scale_max 선형 정규화. max=0이면 모두 0."""
    if not raw:
        return []
    mx = max(raw)
    if mx <= 0:
        return [0.0 for _ in raw]
    return [v / mx * scale_max for v in raw]


def _retention_uplift(
    curve: RetentionCurve, start: float, end: float, video_duration: int,
) -> float:
    """구간의 retention performance를 영상 내 min/max 기준 0~10으로 정규화.

    영상 절대 baseline (1.0) 대비가 아닌 영상 내 상대 ranking 사용.
    이유: 채널마다 절대 retention이 천차만별 (영빈 영상 평균 ~0.5)
    이라 절대 변환은 정보 손실. 영상 내 가장 retention 높은 구간 = 10점.
    """
    if video_duration <= 0 or not curve.relative_retention_performance:
        return 0.0
    start_ratio = start / video_duration
    end_ratio = end / video_duration
    vals = [
        perf
        for ratio, perf in zip(
            curve.elapsed_ratios,
            curve.relative_retention_performance,
            strict=False,
        )
        if start_ratio <= ratio <= end_ratio
    ]
    if not vals:
        return 0.0
    avg = sum(vals) / len(vals)
    overall_min = min(curve.relative_retention_performance)
    overall_max = max(curve.relative_retention_performance)
    if overall_max <= overall_min:
        return 5.0
    return (avg - overall_min) / (overall_max - overall_min) * 10


def score_moments(
    moments: list[MagicMoment],
    transcript: Transcript,
    retention: RetentionCurve | None,
    video_duration: int,
) -> list[MagicMoment]:
    """각 모먼트에 density, retention_uplift, final_score를 채워 반환."""
    if not moments:
        return moments

    raw_densities = [
        compute_density(transcript, m.start_sec, m.end_sec) for m in moments
    ]
    normalized = _normalize(raw_densities)

    has_retention = retention is not None and bool(
        retention.relative_retention_performance,
    )

    result: list[MagicMoment] = []
    for m, density in zip(moments, normalized, strict=True):
        if has_retention:
            assert retention is not None  # for type checker
            uplift = _retention_uplift(
                retention, m.start_sec, m.end_sec, video_duration,
            )
            final = (
                m.score * settings.scoring_w_gemini_with_retention
                + uplift * settings.scoring_w_retention
                + density * settings.scoring_w_density
            )
            result.append(
                m.model_copy(update={
                    "density": density,
                    "retention_uplift": uplift,
                    "final_score": final,
                })
            )
        else:
            final = (
                m.score * settings.scoring_w_gemini_cold
                + density * settings.scoring_w_density_cold
            )
            result.append(
                m.model_copy(update={
                    "density": density,
                    "retention_uplift": None,
                    "final_score": final,
                })
            )

    log.info(
        "score.computed",
        moments=len(result), retention=has_retention,
    )
    return result
