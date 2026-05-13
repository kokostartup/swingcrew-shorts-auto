"""Phase 3 analyze 테스트."""
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.analyze import (
    FEW_SHOT_EXAMPLES,
    _non_max_suppress,
    _parse_moments,
    analyze,
    load_cached_analysis,
)
from app.storage.models import AnalysisResult, MagicMoment, Transcript, TranscriptSegment, Video


def _make_segment(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, words=[])


def _make_video(yid: str, local_path: Path) -> Video:
    return Video(
        id=1, youtube_id=yid, title="t", duration=600, local_path=local_path,
    )


# ----- Fast unit tests -----


def test_few_shot_examples_loaded() -> None:
    assert len(FEW_SHOT_EXAMPLES) == 5
    for ex in FEW_SHOT_EXAMPLES:
        assert "copy1" in ex and "copy2" in ex


def test_parse_moments_basic() -> None:
    raw = {
        "moments": [
            {
                "start_sec": 30, "end_sec": 70,
                "hook_text": "헤드 스피드",
                "copy1": "헤드 스피드는",
                "copy2": "이 영상만 보세요!",
                "score": 8.5,
                "reasoning": "구체적 키워드 + 행동 유도",
            }
        ]
    }
    out = _parse_moments(raw)
    assert len(out) == 1
    assert out[0].copy1 == "헤드 스피드는"
    assert out[0].score == 8.5


def test_parse_moments_skips_invalid() -> None:
    raw = {
        "moments": [
            {"start_sec": "bad", "end_sec": 10, "hook_text": "x",
             "copy1": "a", "copy2": "b", "score": 1, "reasoning": ""},
            {"start_sec": 0, "end_sec": 10, "hook_text": "ok",
             "copy1": "a", "copy2": "b", "score": 5, "reasoning": ""},
        ]
    }
    out = _parse_moments(raw)
    assert len(out) == 1
    assert out[0].hook_text == "ok"


def test_non_max_suppress_keeps_highest_score() -> None:
    m1 = MagicMoment(start_sec=10, end_sec=70, hook_text="a",
                     copy1="a", copy2="b", score=5.0, reasoning="")
    m2 = MagicMoment(start_sec=25, end_sec=85, hook_text="b",
                     copy1="a", copy2="b", score=8.0, reasoning="")
    m3 = MagicMoment(start_sec=200, end_sec=260, hook_text="c",
                     copy1="a", copy2="b", score=6.0, reasoning="")
    out = _non_max_suppress([m1, m2, m3], min_gap_sec=30.0)
    # m1, m2는 25-10=15 < 30 → m2가 score 높아 유지. m3는 멀어서 유지.
    assert {m.hook_text for m in out} == {"b", "c"}
    assert [m.start_sec for m in out] == [25, 200]


def test_analyze_cache_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.config.settings.analyses_dir", tmp_path / "analyses",
    )
    yid = "abcdefghij1"
    fake = AnalysisResult(youtube_id=yid, model="gemini-test", moments=[])
    (tmp_path / "analyses").mkdir(parents=True)
    (tmp_path / "analyses" / f"{yid}.json").write_text(
        fake.model_dump_json(), encoding="utf-8",
    )

    video = _make_video(yid, tmp_path / "any.mp4")
    transcript = Transcript(
        language="ko",
        segments=[_make_segment(0, 5, "hi")],
    )
    with patch("app.pipeline.analyze.generate_json") as mock_gen:
        result = analyze(video, transcript)
    mock_gen.assert_not_called()
    assert result.youtube_id == yid


def test_analyze_empty_transcript_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.settings.analyses_dir", tmp_path / "analyses",
    )
    video = _make_video("abcdefghij1", tmp_path / "x.mp4")
    transcript = Transcript(language="ko", segments=[])
    with pytest.raises(ValueError):
        analyze(video, transcript)


def test_analyze_uses_gemini_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.settings.analyses_dir", tmp_path / "analyses",
    )
    monkeypatch.setattr(
        "app.config.settings.sqlite_path", tmp_path / "test.db",
    )

    yid = "abcdefghij1"
    video = _make_video(yid, tmp_path / "x.mp4")
    transcript = Transcript(
        language="ko",
        segments=[_make_segment(10, 40, "헤드 스피드는 이렇게")],
    )

    fake_raw = {
        "moments": [
            {
                "start_sec": 15, "end_sec": 75,
                "hook_text": "헤드 스피드",
                "copy1": "헤드 스피드는",
                "copy2": "이 영상만 보세요!",
                "score": 9.0,
                "reasoning": "강한 행동 유도",
            }
        ]
    }
    # DB 시드: shorts persist 위해 videos row 필요
    from app.storage.db import get_connection, upsert_video
    conn = get_connection(tmp_path / "test.db")
    try:
        upsert_video(conn, youtube_id=yid, title="t", duration=600)
    finally:
        conn.close()

    with patch(
        "app.pipeline.analyze.generate_json", return_value=fake_raw,
    ) as mock_gen:
        result = analyze(video, transcript)

    mock_gen.assert_called_once()
    assert len(result.moments) == 1
    assert result.moments[0].copy1 == "헤드 스피드는"
    # 캐시 파일 생성 확인
    assert (tmp_path / "analyses" / f"{yid}.json").exists()


def test_load_cached_analysis_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.settings.analyses_dir", tmp_path / "missing",
    )
    assert load_cached_analysis("nonexistent") is None
