"""Phase 4 multi-signal scoring 테스트."""
from app.pipeline.score import (
    _normalize,
    _retention_uplift,
    compute_density,
    score_moments,
)
from app.storage.models import (
    MagicMoment,
    RetentionCurve,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)


def _mk_moment(start: float, end: float, score: float = 8.0) -> MagicMoment:
    return MagicMoment(
        start_sec=start, end_sec=end,
        hook_text="h", copy1="a", copy2="b",
        score=score, reasoning="",
    )


def _mk_transcript(word_starts: list[float]) -> Transcript:
    words = [
        TranscriptWord(start=t, end=t + 0.2, text="w", score=0.9)
        for t in word_starts
    ]
    return Transcript(
        language="ko",
        segments=[
            TranscriptSegment(
                start=min(word_starts), end=max(word_starts) + 0.2,
                text=" ".join(["w"] * len(words)), words=words,
            )
        ] if word_starts else [],
    )


def test_compute_density_basic() -> None:
    transcript = _mk_transcript([0.5, 1.0, 1.5, 2.0])
    # 구간 [0, 3] 안에 4 word → 4/3 = 1.333
    assert abs(compute_density(transcript, 0, 3) - 4 / 3) < 1e-6


def test_compute_density_zero_duration() -> None:
    transcript = _mk_transcript([0.5, 1.0])
    assert compute_density(transcript, 5, 5) == 0.0


def test_compute_density_excluded_words() -> None:
    transcript = _mk_transcript([1.0, 2.0, 50.0])
    # [0, 5] 구간엔 2 word만
    assert abs(compute_density(transcript, 0, 5) - 2 / 5) < 1e-6


def test_normalize_max_to_10() -> None:
    assert _normalize([5.0, 10.0, 2.5]) == [5.0, 10.0, 2.5]


def test_normalize_all_zero() -> None:
    assert _normalize([0.0, 0.0]) == [0.0, 0.0]


def test_retention_uplift_max_ratio_returns_10() -> None:
    # 영상 내 min/max 정규화 — max에 매치되는 구간은 10
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[0.0, 0.5, 1.0],
        audience_watch_ratio=[1.0, 0.5, 0.3],
        relative_retention_performance=[0.3, 0.7, 0.3],
        fetched_at="2026-05-11",
    )
    # video 600s, 구간 250-350 → ratio 0.42-0.58 → 0.5만 매치 → perf=[0.7] avg=0.7
    # min=0.3, max=0.7 → (0.7-0.3)/(0.7-0.3)*10 = 10.0
    uplift = _retention_uplift(curve, 250, 350, 600)
    assert abs(uplift - 10.0) < 1e-6


def test_retention_uplift_all_equal_returns_5() -> None:
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[0.0, 0.5, 1.0],
        audience_watch_ratio=[1.0, 0.5, 0.3],
        relative_retention_performance=[0.5, 0.5, 0.5],
        fetched_at="2026-05-11",
    )
    # 모든 perf 동일 → mid 5.0 반환
    assert _retention_uplift(curve, 0, 600, 600) == 5.0


def test_score_moments_with_retention() -> None:
    moments = [_mk_moment(0, 60, score=10.0)]
    transcript = _mk_transcript([1.0, 2.0, 3.0, 4.0])
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[0.0, 0.5, 1.0],
        audience_watch_ratio=[1.0, 0.5, 0.3],
        relative_retention_performance=[1.5, 1.5, 1.5],
        fetched_at="2026-05-11",
    )
    out = score_moments(moments, transcript, curve, video_duration=300)
    assert len(out) == 1
    m = out[0]
    assert m.density is not None
    assert m.retention_uplift is not None and m.retention_uplift > 0
    assert m.final_score is not None


def test_score_moments_cold_start() -> None:
    moments = [_mk_moment(0, 60, score=8.0)]
    transcript = _mk_transcript([1.0, 2.0])
    out = score_moments(moments, transcript, retention=None, video_duration=300)
    assert len(out) == 1
    assert out[0].retention_uplift is None
    assert out[0].final_score is not None
    # gemini*0.7 + density*0.3 (density=10 if 모먼트 1개)
    expected = 8.0 * 0.7 + 10.0 * 0.3
    assert abs(out[0].final_score - expected) < 1e-6


def test_score_moments_empty_returns_empty() -> None:
    assert score_moments([], _mk_transcript([]), None, 100) == []
