"""Phase 4 retention 테스트."""
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.retention import _days_since, detect_peak_regions, fetch_retention
from app.storage.models import RetentionCurve, Video


def _mk_video(yid: str, published: str | None) -> Video:
    return Video(
        id=1, youtube_id=yid, title="t", duration=600,
        published_at=published, local_path=Path("data/samples/x.mp4"),
    )


def test_days_since_iso_date() -> None:
    assert _days_since("2020-01-01") is not None
    assert _days_since("2020-01-01") > 1000


def test_days_since_none() -> None:
    assert _days_since(None) is None


def test_days_since_invalid() -> None:
    assert _days_since("not-a-date") is None


def test_fetch_retention_cold_start_recent_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.retention_dir", tmp_path / "retention")
    monkeypatch.setattr("app.config.settings.youtube_channel_id", "UCxxx")
    from datetime import UTC, datetime
    recent = datetime.now(UTC).date().isoformat()
    video = _mk_video("abc", recent)
    assert fetch_retention(video) is None


def test_fetch_retention_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.retention_dir", tmp_path / "retention")
    curve = RetentionCurve(
        youtube_id="abc",
        elapsed_ratios=[0.0, 0.5],
        audience_watch_ratio=[1.0, 0.5],
        relative_retention_performance=[1.0, 1.0],
        fetched_at="2026-05-11",
    )
    (tmp_path / "retention").mkdir(parents=True)
    (tmp_path / "retention" / "abc.json").write_text(
        curve.model_dump_json(), encoding="utf-8",
    )
    video = _mk_video("abc", "2020-01-01")
    with patch("app.pipeline.retention._query_analytics") as mock_query:
        result = fetch_retention(video)
    mock_query.assert_not_called()
    assert result is not None
    assert result.youtube_id == "abc"


def test_detect_peak_regions_empty_curve_returns_empty() -> None:
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[], audience_watch_ratio=[],
        relative_retention_performance=[],
        fetched_at="2026-05-11",
    )
    assert detect_peak_regions(curve, 600) == []


def test_detect_peak_regions_monotonic_decrease_no_spikes() -> None:
    """전 구간 단조 감소 (양수 slope 없음) → 영역 0."""
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[0.0, 0.25, 0.5, 0.75, 1.0],
        audience_watch_ratio=[1.0, 0.8, 0.6, 0.4, 0.2],
        relative_retention_performance=[1.0, 1.0, 1.0, 1.0, 1.0],
        fetched_at="2026-05-11",
    )
    assert detect_peak_regions(curve, 600) == []


def test_detect_peak_regions_single_spike_creates_region() -> None:
    """중간 한 지점에서 awr 갑자기 상승 → 1개 영역."""
    # 100s 영상, 10s 간격 sample. 50s 지점에서 spike.
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        audience_watch_ratio=[1.0, 0.9, 0.8, 0.7, 0.6, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3],
        relative_retention_performance=[1.0] * 11,
        fetched_at="2026-05-11",
    )
    regions = detect_peak_regions(
        curve, 100, min_region_sec=10, max_region_sec=80,
    )
    assert len(regions) == 1
    start, end, strength = regions[0]
    # spike가 50s 근처 → 영역이 그 주변
    assert 30 <= start <= 50
    assert 50 <= end <= 90
    assert strength > 0


def test_detect_peak_regions_threshold_override() -> None:
    """spike_threshold 인자가 None이 아니면 그 값 사용 (Phase 8 학습 주입)."""
    curve = RetentionCurve(
        youtube_id="x",
        elapsed_ratios=[0.0, 0.5, 1.0],
        audience_watch_ratio=[1.0, 0.95, 0.5],  # 0.5s에 작은 spike
        relative_retention_performance=[1.0] * 3,
        fetched_at="2026-05-11",
    )
    # 매우 큰 threshold → 영역 없음
    assert detect_peak_regions(curve, 100, spike_threshold=10.0) == []


def test_fetch_retention_no_channel_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.retention_dir", tmp_path / "retention")
    monkeypatch.setattr("app.config.settings.youtube_channel_id", "")
    video = _mk_video("abc", "2020-01-01")
    assert fetch_retention(video) is None
