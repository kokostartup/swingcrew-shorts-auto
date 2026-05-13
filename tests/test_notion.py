"""Phase 5 Notion 어댑터 테스트."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.integrations.notion import (
    STATUS_EN_TO_KO,
    STATUS_KO_TO_EN,
    NotionAPIError,
    _moment_properties,
    _smpte,
    _time_range,
    _youtube_timestamp_url,
    create_page,
    list_pages_by_status,
    update_status,
)
from app.storage.models import MagicMoment, Video


def _mk_video() -> Video:
    return Video(
        id=1, youtube_id="abcdefghij1", title="t", duration=600,
        local_path=Path("data/samples/abcdefghij1.mp4"),
    )


def _mk_moment(
    start: float = 25.6, end: float = 51.0,
    scene: str | None = "letterbox_4_5",
) -> MagicMoment:
    return MagicMoment(
        start_sec=start, end_sec=end,
        hook_text="hook", copy1="copy1", copy2="copy2",
        score=8.0, reasoning="why", scene_type=scene,
        final_score=8.5,
    )


# ----- 매핑 -----


def test_status_mapping_round_trip() -> None:
    for en, ko in STATUS_EN_TO_KO.items():
        assert STATUS_KO_TO_EN[ko] == en
    assert len(STATUS_EN_TO_KO) == 7
    assert STATUS_EN_TO_KO["proposed"] == "제안"
    assert STATUS_KO_TO_EN["승인"] == "approved"


# ----- SMPTE timecode -----


def test_smpte_zero() -> None:
    assert _smpte(0) == "00:00:00"


def test_smpte_basic_25_6() -> None:
    # 25.6s @30fps → 768 frames → 0m 25s 18f
    assert _smpte(25.6) == "00:25:18"


def test_smpte_minute_wrap() -> None:
    assert _smpte(60.0) == "01:00:00"


def test_smpte_single_frame() -> None:
    # 1/30 = 0.0333s → 1 frame
    assert _smpte(1 / 30) == "00:00:01"


def test_smpte_hour_overflow_truncates() -> None:
    # 1시간 = 3600s → "60:00:00" (영빈 영상은 미드폼이라 보통 < 1h)
    assert _smpte(3600.0) == "60:00:00"


def test_time_range_format() -> None:
    assert _time_range(25.6, 51.0) == "00:25:18 - 00:51:00"


# ----- YouTube timestamp URL -----


def test_youtube_timestamp_url_integer_seconds() -> None:
    assert (
        _youtube_timestamp_url("abcdefghij1", 25.6)
        == "https://youtu.be/abcdefghij1?t=25s"
    )


def test_youtube_timestamp_url_zero() -> None:
    assert _youtube_timestamp_url("xyz12345678", 0) == "https://youtu.be/xyz12345678?t=0s"


# ----- _moment_properties -----


def test_moment_properties_includes_korean_status() -> None:
    props = _moment_properties(_mk_video(), _mk_moment())
    assert props["Status"] == {"select": {"name": "제안"}}
    assert props["Source Video"]["url"].startswith("https://youtu.be/")
    assert props["Time Range"]["rich_text"][0]["text"]["content"] == "00:25:18 - 00:51:00"
    assert props["Score"]["number"] == 8.5  # final_score 우선
    assert props["Scene Type"] == {"select": {"name": "letterbox_4_5"}}


def test_moment_properties_omits_scene_type_when_none() -> None:
    props = _moment_properties(_mk_video(), _mk_moment(scene=None))
    assert "Scene Type" not in props


def test_moment_properties_includes_internal_id_from_video() -> None:
    video = Video(
        id=1, youtube_id="abcdefghij1", title="t", duration=600,
        internal_id="26-B005",
        local_path=Path("data/samples/abcdefghij1.mp4"),
    )
    props = _moment_properties(video, _mk_moment())
    assert props["Internal ID"]["rich_text"][0]["text"]["content"] == "26-B005"


def test_moment_properties_omits_internal_id_when_video_has_none() -> None:
    """video.internal_id가 None이면 Internal ID property 안 들어감."""
    props = _moment_properties(_mk_video(), _mk_moment())
    assert "Internal ID" not in props


def test_moment_properties_uses_gemini_score_when_no_final() -> None:
    m = MagicMoment(
        start_sec=0, end_sec=30, hook_text="h", copy1="c1", copy2="c2",
        score=7.7, reasoning="r",
    )
    props = _moment_properties(_mk_video(), m)
    assert props["Score"]["number"] == 7.7


def test_moment_properties_truncates_reasoning() -> None:
    m = MagicMoment(
        start_sec=0, end_sec=30, hook_text="h", copy1="c1", copy2="c2",
        score=5.0, reasoning="x" * 3000,
    )
    props = _moment_properties(_mk_video(), m)
    text = props["Reasoning"]["rich_text"][0]["text"]["content"]
    assert len(text) <= 1900


# ----- Notion API mock 기반 어댑터 함수 -----


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """notion-client lazy init을 우회 + token/db_id + data_source_id 캐시 셋업."""
    monkeypatch.setattr("app.config.settings.notion_token", "fake-token")
    monkeypatch.setattr(
        "app.config.settings.notion_shorts_db_id", "fake-db-id",
    )
    monkeypatch.setattr("app.integrations.notion._client", None)
    monkeypatch.setattr(
        "app.integrations.notion._data_source_id", "fake-ds-id",
    )
    client = MagicMock()
    monkeypatch.setattr("app.integrations.notion._get_client", lambda: client)
    return client


def test_create_page_returns_page_id(fake_client: MagicMock) -> None:
    fake_client.pages.create.return_value = {"id": "page-abc"}
    pid = create_page(_mk_video(), _mk_moment())
    assert pid == "page-abc"
    fake_client.pages.create.assert_called_once()
    call_kwargs = fake_client.pages.create.call_args.kwargs
    assert call_kwargs["parent"]["database_id"] == "fake-db-id"


def test_create_page_raises_on_api_failure(fake_client: MagicMock) -> None:
    fake_client.pages.create.side_effect = RuntimeError("boom")
    with pytest.raises(NotionAPIError):
        create_page(_mk_video(), _mk_moment())


def test_list_pages_by_status_parses_results(fake_client: MagicMock) -> None:
    fake_client.data_sources.query.return_value = {
        "results": [
            {
                "id": "p1",
                "properties": {
                    "Scheduled At": {
                        "date": {"start": "2026-05-20T09:00:00.000+09:00"},
                    },
                    "Scene Type": {"select": {"name": "split_right"}},
                },
            },
            {
                "id": "p2",
                "properties": {
                    "Scheduled At": {"date": None},
                    "Scene Type": {"select": None},
                },
            },
        ],
        "has_more": False,
    }
    out = list_pages_by_status("approved")
    assert len(out) == 2
    assert out[0]["id"] == "p1"
    assert out[0]["scheduled_at"] == "2026-05-20T09:00:00.000+09:00"
    assert out[0]["scene_type"] == "split_right"
    assert out[1]["scheduled_at"] is None
    assert out[1]["scene_type"] is None


def test_list_pages_by_status_paginates(fake_client: MagicMock) -> None:
    fake_client.data_sources.query.side_effect = [
        {
            "results": [{"id": "p1", "properties": {}}],
            "has_more": True,
            "next_cursor": "cursor-2",
        },
        {
            "results": [{"id": "p2", "properties": {}}],
            "has_more": False,
        },
    ]
    out = list_pages_by_status("approved")
    assert [p["id"] for p in out] == ["p1", "p2"]
    assert fake_client.data_sources.query.call_count == 2


def test_list_pages_by_status_invalid_status() -> None:
    with pytest.raises(ValueError):
        list_pages_by_status("nonsense")


def test_update_status_passes_korean(fake_client: MagicMock) -> None:
    update_status("page-1", "generated")
    fake_client.pages.update.assert_called_once()
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert props["Status"] == {"select": {"name": "생성"}}


def test_update_status_with_published_urls(fake_client: MagicMock) -> None:
    update_status("page-1", "published", published_urls=["https://yt", "https://ig"])
    props = fake_client.pages.update.call_args.kwargs["properties"]
    text = props["Published URLs"]["rich_text"][0]["text"]["content"]
    assert "https://yt" in text and "https://ig" in text


def test_update_status_invalid_status() -> None:
    with pytest.raises(ValueError):
        update_status("page-1", "nonsense")
