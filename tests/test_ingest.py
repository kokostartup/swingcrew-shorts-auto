"""Phase 2 ingest 테스트."""
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.ingest import (
    _extract_internal_id,
    _extract_youtube_id,
    _yt_upload_date_to_iso,
    ingest,
)
from app.storage.db import get_connection, upsert_video


def test_extract_youtube_id_url_forms() -> None:
    ids = [
        _extract_youtube_id("https://youtu.be/abcdefghij1"),
        _extract_youtube_id("https://www.youtube.com/watch?v=abcdefghij1"),
        _extract_youtube_id("https://www.youtube.com/shorts/abcdefghij1"),
        _extract_youtube_id("https://www.youtube.com/embed/abcdefghij1"),
        _extract_youtube_id("abcdefghij1"),
    ]
    assert all(i == "abcdefghij1" for i in ids)


def test_extract_youtube_id_invalid_raises() -> None:
    with pytest.raises(ValueError):
        _extract_youtube_id("not-an-id")
    with pytest.raises(ValueError):
        _extract_youtube_id("")


def test_extract_internal_id_from_description() -> None:
    desc = "영상 본문...\n\n구독 좋아요 부탁드립니다\n\n26-B001"
    assert _extract_internal_id(desc) == "26-B001"


def test_extract_internal_id_p_form() -> None:
    assert _extract_internal_id("프로 레슨 영상\n26-P007") == "26-P007"


def test_extract_internal_id_missing_returns_none() -> None:
    assert _extract_internal_id("ID 없는 description") is None
    assert _extract_internal_id("") is None
    assert _extract_internal_id("26-Z001") is None  # B/P 아님


def test_extract_internal_id_middle_of_text() -> None:
    """description 중간이라도 매치 (영빈 안내는 맨 하단이지만 보수적)."""
    assert _extract_internal_id("앞에 텍스트 26-B099 뒤에 텍스트") == "26-B099"


def test_yt_upload_date_to_iso() -> None:
    assert _yt_upload_date_to_iso("20260511") == "2026-05-11"
    assert _yt_upload_date_to_iso(None) is None
    assert _yt_upload_date_to_iso("invalid") is None
    assert _yt_upload_date_to_iso("") is None


def test_ingest_cache_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.samples_dir", tmp_path / "samples")
    monkeypatch.setattr("app.config.settings.sqlite_path", tmp_path / "test.db")

    yid = "abcdefghij1"
    samples = tmp_path / "samples"
    samples.mkdir(parents=True)
    (samples / f"{yid}.mp4").write_bytes(b"fake mp4")

    conn = get_connection(tmp_path / "test.db")
    upsert_video(
        conn, youtube_id=yid, title="Test Video",
        duration=100, published_at="2026-05-01",
    )
    conn.close()

    with patch("app.pipeline.ingest._download") as mock_dl, \
         patch("app.pipeline.ingest._fetch_metadata") as mock_meta:
        result = ingest(yid)

    mock_dl.assert_not_called()
    mock_meta.assert_not_called()
    assert result.youtube_id == yid
    assert result.title == "Test Video"
    assert result.duration == 100


def test_ingest_cache_miss_calls_yt_dlp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.samples_dir", tmp_path / "samples")
    monkeypatch.setattr("app.config.settings.sqlite_path", tmp_path / "test.db")

    yid = "abcdefghij1"
    fake_meta = {
        "title": "Sample",
        "duration": 600,
        "upload_date": "20260101",
    }

    def fake_download(youtube_id: str, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake mp4 content")

    with patch(
        "app.pipeline.ingest._fetch_metadata", return_value=fake_meta,
    ), patch(
        "app.pipeline.ingest._download", side_effect=fake_download,
    ) as mock_dl:
        result = ingest(yid)

    mock_dl.assert_called_once()
    assert result.youtube_id == yid
    assert result.title == "Sample"
    assert result.duration == 600
    assert result.published_at == "2026-01-01"


@pytest.mark.slow
def test_ingest_phase1_sample_cache_hit() -> None:
    """Phase 1에서 다운된 샘플이 캐시 hit으로 즉시 반환."""
    sample = Path("data/samples/1wwEY0KEkoA.mp4")
    if not sample.exists():
        pytest.skip("Phase 1 sample 영상 없음")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM videos WHERE youtube_id = ?", ("1wwEY0KEkoA",),
        ).fetchone()
        if existing is None:
            upsert_video(
                conn, youtube_id="1wwEY0KEkoA",
                title="(test seed)", duration=580,
            )
    finally:
        conn.close()

    with patch("app.pipeline.ingest._download") as mock_dl, \
         patch("app.pipeline.ingest._fetch_metadata") as mock_meta:
        result = ingest("1wwEY0KEkoA")

    mock_dl.assert_not_called()
    mock_meta.assert_not_called()
    assert result.youtube_id == "1wwEY0KEkoA"
