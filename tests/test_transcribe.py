"""Phase 2 transcribe 테스트."""
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.transcribe import _dict_to_transcript, transcribe
from app.storage.models import Transcript, Video

FIXTURE = Path("tests/fixtures/transcripts/sample_short.json")


def _dummy_whisperx_dict() -> dict:
    return {
        "language": "ko",
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "안녕하세요 테스트",
                "words": [
                    {"start": 0.0, "end": 0.8, "word": "안녕하세요", "score": 0.95},
                    {"start": 1.0, "end": 1.5, "word": "테스트", "score": 0.92},
                ],
            },
        ],
    }


def test_dict_to_transcript_parses_whisperx_output() -> None:
    t = _dict_to_transcript(_dummy_whisperx_dict())
    assert t.language == "ko"
    assert len(t.segments) == 1
    assert t.segments[0].text == "안녕하세요 테스트"
    assert len(t.segments[0].words) == 2
    assert t.segments[0].words[0].text == "안녕하세요"
    assert t.segments[0].words[0].score == 0.95


def test_dict_to_transcript_handles_missing_words() -> None:
    raw = {
        "language": "ko",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "단어 키 없음"},
            {"start": 1.0, "end": 2.0, "text": "빈 list", "words": []},
        ],
    }
    t = _dict_to_transcript(raw)
    assert all(s.words == [] for s in t.segments)


def test_dict_to_transcript_falls_back_to_default_language() -> None:
    raw: dict = {"segments": []}
    t = _dict_to_transcript(raw)
    assert t.language == "ko"
    assert t.segments == []


def test_transcribe_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.settings.transcripts_dir", tmp_path / "transcripts",
    )

    yid = "abcdefghij1"
    (tmp_path / "transcripts").mkdir(parents=True)
    fake = Transcript(language="ko", segments=[])
    (tmp_path / "transcripts" / f"{yid}.json").write_text(
        fake.model_dump_json(), encoding="utf-8",
    )

    video = Video(
        youtube_id=yid, title="t", duration=0,
        local_path=tmp_path / "nonexistent.mp4",
    )

    with patch("app.pipeline.transcribe._run_whisperx") as mock_run:
        result = transcribe(video)

    mock_run.assert_not_called()
    assert result.language == "ko"


def test_transcribe_missing_video_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.settings.transcripts_dir", tmp_path / "transcripts",
    )
    video = Video(
        youtube_id="abcdefghij1", title="t", duration=0,
        local_path=tmp_path / "missing.mp4",
    )
    with pytest.raises(FileNotFoundError):
        transcribe(video)


def test_fixture_loads_into_transcript_model() -> None:
    if not FIXTURE.exists():
        pytest.skip(f"fixture not found: {FIXTURE}")
    t = Transcript.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert t.language == "ko"
    assert len(t.segments) == 1
    assert len(t.segments[0].words) == 4


@pytest.mark.slow
@pytest.mark.external
def test_transcribe_real_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실 GPU + WhisperX로 1wwEY0KEkoA 전사 (CI에서 skip)."""
    sample = Path("data/samples/1wwEY0KEkoA.mp4")
    if not sample.exists():
        pytest.skip("Phase 1 sample 영상 없음")

    monkeypatch.setattr(
        "app.config.settings.transcripts_dir", tmp_path / "transcripts",
    )
    monkeypatch.setattr(
        "app.config.settings.sqlite_path", tmp_path / "test.db",
    )

    from app.storage.db import get_connection, upsert_video

    conn = get_connection(tmp_path / "test.db")
    try:
        upsert_video(
            conn, youtube_id="1wwEY0KEkoA",
            title="(slow test)", duration=580,
        )
    finally:
        conn.close()

    video = Video(
        youtube_id="1wwEY0KEkoA",
        title="(slow test)",
        duration=580,
        local_path=sample,
    )
    t = transcribe(video)
    assert t.language == "ko"
    assert len(t.segments) > 0
