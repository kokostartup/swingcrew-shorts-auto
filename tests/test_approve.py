"""Phase 5 approve 파이프라인 테스트 (mock 기반)."""
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.approve import (
    poll_status_from_notion,
    process_approved,
    sync_to_notion,
)
from app.storage.db import get_connection, upsert_video
from app.storage.models import AnalysisResult, MagicMoment, Video


@pytest.fixture
def conn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[sqlite3.Connection]:
    """tmp DB + analyses_dir 격리."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.config.settings.sqlite_path", db_path)
    monkeypatch.setattr("app.config.settings.analyses_dir", tmp_path / "an")
    monkeypatch.setattr(
        "app.config.settings.shorts_output_dir", tmp_path / "out",
    )
    c = get_connection(db_path)
    try:
        yield c
    finally:
        c.close()


def _mk_moment(start: float = 10.0, end: float = 30.0) -> MagicMoment:
    return MagicMoment(
        start_sec=start, end_sec=end,
        hook_text="hook", copy1="copy1", copy2="copy2",
        score=8.0, reasoning="why",
        scene_type="letterbox_4_5", final_score=8.0,
    )


def _insert_short(
    conn: sqlite3.Connection, video_id: int, start: float, end: float,
) -> int:
    cur = conn.execute(
        "INSERT INTO shorts "
        "(source_video_id, start_time, end_time, score, scene_type, status) "
        "VALUES (?, ?, ?, 8.0, 'letterbox_4_5', 'proposed') RETURNING id",
        (video_id, start, end),
    )
    sid = int(cur.fetchone()["id"])
    conn.commit()
    return sid


# ----- sync_to_notion -----


def test_sync_creates_pages_and_persists_page_id(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=tmp_path / "abc.mp4",
    )
    result = AnalysisResult(
        youtube_id="abc", model="x", moments=[_mk_moment()],
    )
    with patch(
        "app.pipeline.approve.notion_create_page", return_value="page-1",
    ) as mock_create:
        created = sync_to_notion(video, result)
    assert created == 1
    mock_create.assert_called_once()
    row = conn.execute(
        "SELECT notion_page_id FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["notion_page_id"] == "page-1"


def test_sync_skips_already_pushed(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    _insert_short(conn, vid, 10.0, 30.0)
    conn.execute("UPDATE shorts SET notion_page_id = 'already'")
    conn.commit()
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=tmp_path / "abc.mp4",
    )
    result = AnalysisResult(
        youtube_id="abc", model="x", moments=[_mk_moment()],
    )
    with patch("app.pipeline.approve.notion_create_page") as mock_create:
        created = sync_to_notion(video, result)
    assert created == 0
    mock_create.assert_not_called()


def test_sync_warns_when_moment_missing(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    _insert_short(conn, vid, 99.0, 110.0)
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=tmp_path / "abc.mp4",
    )
    result = AnalysisResult(
        youtube_id="abc", model="x", moments=[_mk_moment(10.0, 30.0)],
    )
    with patch("app.pipeline.approve.notion_create_page") as mock_create:
        created = sync_to_notion(video, result)
    assert created == 0
    mock_create.assert_not_called()


# ----- poll_status_from_notion -----


def test_poll_syncs_approved_status_and_scheduled_at(
    conn: sqlite3.Connection,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute("UPDATE shorts SET notion_page_id = 'p1'")
    conn.commit()

    def fake_list(status: str) -> list[dict[str, object]]:
        if status == "approved":
            return [
                {
                    "id": "p1", "status": "approved",
                    "scheduled_at": "2026-05-20T09:00:00.000+09:00",
                    "scene_type": None,
                },
            ]
        return []

    with patch("app.pipeline.approve.notion_list", side_effect=fake_list):
        counts = poll_status_from_notion()
    assert counts["approved"] == 1
    row = conn.execute(
        "SELECT status, scheduled_at FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["status"] == "approved"
    assert row["scheduled_at"] == "2026-05-20T09:00:00.000+09:00"


def test_poll_syncs_rejected(conn: sqlite3.Connection) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute("UPDATE shorts SET notion_page_id = 'p1'")
    conn.commit()

    def fake_list(status: str) -> list[dict[str, object]]:
        if status == "rejected":
            return [
                {
                    "id": "p1", "status": "rejected",
                    "scheduled_at": None, "scene_type": None,
                },
            ]
        return []

    with patch("app.pipeline.approve.notion_list", side_effect=fake_list):
        counts = poll_status_from_notion()
    assert counts["rejected"] == 1
    row = conn.execute(
        "SELECT status FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["status"] == "rejected"


def test_poll_idempotent_on_no_change(conn: sqlite3.Connection) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved', "
        "scheduled_at = '2026-05-20T09:00:00.000+09:00', "
        "scene_type = 'letterbox_4_5'",
    )
    conn.commit()

    def fake_list(status: str) -> list[dict[str, object]]:
        if status == "approved":
            return [
                {
                    "id": "p1", "status": "approved",
                    "scheduled_at": "2026-05-20T09:00:00.000+09:00",
                    "scene_type": "letterbox_4_5",
                },
            ]
        return []

    with patch("app.pipeline.approve.notion_list", side_effect=fake_list):
        counts = poll_status_from_notion()
    assert counts["approved"] == 0
    assert counts["scheduled_synced"] == 0
    assert counts["scene_overridden"] == 0


def test_poll_detects_scheduled_at_change(conn: sqlite3.Connection) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved'",
    )
    conn.commit()

    def fake_list(status: str) -> list[dict[str, object]]:
        if status == "approved":
            return [
                {
                    "id": "p1", "status": "approved",
                    "scheduled_at": "2026-06-01T12:00:00.000+09:00",
                    "scene_type": None,
                },
            ]
        return []

    with patch("app.pipeline.approve.notion_list", side_effect=fake_list):
        counts = poll_status_from_notion()
    assert counts["scheduled_synced"] == 1


def test_poll_scene_type_override_from_notion(conn: sqlite3.Connection) -> None:
    """노션에서 영빈이 Scene Type 바꾸면 SQLite도 그 값으로 갱신."""
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved', "
        "scene_type = 'letterbox_4_5'",
    )
    conn.commit()

    def fake_list(status: str) -> list[dict[str, object]]:
        if status == "approved":
            return [
                {
                    "id": "p1", "status": "approved",
                    "scheduled_at": None, "scene_type": "split_right",
                },
            ]
        return []

    with patch("app.pipeline.approve.notion_list", side_effect=fake_list):
        counts = poll_status_from_notion()
    assert counts["scene_overridden"] == 1
    row = conn.execute(
        "SELECT scene_type FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["scene_type"] == "split_right"


# ----- process_approved -----


def _write_cache(tmp_path: Path, yid: str, moment: MagicMoment) -> None:
    """analyze cache 시드."""
    an_dir = tmp_path / "an"
    an_dir.mkdir(parents=True, exist_ok=True)
    result = AnalysisResult(youtube_id=yid, model="x", moments=[moment])
    (an_dir / f"{yid}.json").write_text(
        result.model_dump_json(), encoding="utf-8",
    )


def _fake_meta() -> "object":
    from app.pipeline.publish_meta import PublishMeta
    return PublishMeta(
        title="t", description="d", tags=["a"], hashtags=["#a"],
    )


def test_process_approved_runs_ffmpeg_and_updates_status(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved'",
    )
    conn.commit()
    moment = _mk_moment(10.0, 30.0)
    _write_cache(tmp_path, "abc", moment)

    fake_video_path = tmp_path / "abc.mp4"
    fake_video_path.touch()
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=fake_video_path,
    )

    with patch(
        "app.pipeline.approve.get_video_by_youtube_id", return_value=video,
    ), patch(
        "app.pipeline.approve.make_short",
        return_value=tmp_path / "out.mp4",
    ) as mock_make, patch(
        "app.pipeline.approve.generate_publish_meta", return_value=_fake_meta(),
    ), patch(
        "app.pipeline.approve.notion_update",
    ) as mock_update:
        n = process_approved()

    assert n == 1
    mock_make.assert_called_once()
    # notion_update 호출: ("p1", "generated", ...) 다양한 extra
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "p1" and args[1] == "generated"
    row = conn.execute(
        "SELECT status, generated_path FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["status"] == "generated"
    assert row["generated_path"]


def test_process_approved_marks_error_when_source_missing(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved'",
    )
    conn.commit()
    _write_cache(tmp_path, "abc", _mk_moment(10.0, 30.0))
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=tmp_path / "missing.mp4",
    )
    with patch(
        "app.pipeline.approve.get_video_by_youtube_id", return_value=video,
    ), patch(
        "app.pipeline.approve.make_short",
    ) as mock_make, patch(
        "app.pipeline.approve.notion_update",
    ) as mock_update:
        n = process_approved()
    assert n == 0
    mock_make.assert_not_called()
    mock_update.assert_called_once_with("p1", "error")
    row = conn.execute(
        "SELECT status FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["status"] == "error"


def test_process_approved_marks_error_on_ffmpeg_failure(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved'",
    )
    conn.commit()
    _write_cache(tmp_path, "abc", _mk_moment(10.0, 30.0))
    fake_video_path = tmp_path / "abc.mp4"
    fake_video_path.touch()
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=fake_video_path,
    )
    with patch(
        "app.pipeline.approve.get_video_by_youtube_id", return_value=video,
    ), patch(
        "app.pipeline.approve.make_short",
        side_effect=RuntimeError("ffmpeg boom"),
    ), patch(
        "app.pipeline.approve.notion_update",
    ) as mock_update:
        n = process_approved()
    assert n == 0
    mock_update.assert_called_once_with("p1", "error")
    row = conn.execute(
        "SELECT status FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["status"] == "error"


def test_process_approved_assigns_internal_id_s01(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """video.internal_id 있으면 첫 승인 시 S01 부여 + Notion 업데이트."""
    vid = upsert_video(
        conn, youtube_id="abc", title="t", duration=300,
        internal_id="26-B001",
    )
    sid = _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved'",
    )
    conn.commit()
    _write_cache(tmp_path, "abc", _mk_moment(10.0, 30.0))
    fake_video_path = tmp_path / "abc.mp4"
    fake_video_path.touch()
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        internal_id="26-B001", local_path=fake_video_path,
    )
    with patch(
        "app.pipeline.approve.get_video_by_youtube_id", return_value=video,
    ), patch(
        "app.pipeline.approve.make_short",
        return_value=tmp_path / "out.mp4",
    ), patch(
        "app.pipeline.approve.generate_publish_meta", return_value=_fake_meta(),
    ), patch(
        "app.pipeline.approve.notion_update",
    ) as mock_update:
        n = process_approved()
    assert n == 1
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "p1" and args[1] == "generated"
    assert kwargs.get("internal_id") == "26-B001-S01"
    row = conn.execute(
        "SELECT internal_id, generated_path FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["internal_id"] == "26-B001-S01"
    assert "26-B001-S01.mp4" in row["generated_path"]


def test_process_approved_assigns_next_s_number(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """기존 S01, S02 있으면 다음은 S03."""
    vid = upsert_video(
        conn, youtube_id="abc", title="t", duration=300,
        internal_id="26-B001",
    )
    conn.execute(
        "INSERT INTO shorts (source_video_id, start_time, end_time, status, "
        "internal_id) VALUES (?, 0, 10, 'generated', '26-B001-S01')", (vid,),
    )
    conn.execute(
        "INSERT INTO shorts (source_video_id, start_time, end_time, status, "
        "internal_id) VALUES (?, 60, 70, 'generated', '26-B001-S02')", (vid,),
    )
    sid = _insert_short(conn, vid, 100.0, 130.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved' "
        "WHERE id = ?", (sid,),
    )
    conn.commit()
    _write_cache(tmp_path, "abc", _mk_moment(100.0, 130.0))
    fake_video_path = tmp_path / "abc.mp4"
    fake_video_path.touch()
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        internal_id="26-B001", local_path=fake_video_path,
    )
    with patch(
        "app.pipeline.approve.get_video_by_youtube_id", return_value=video,
    ), patch(
        "app.pipeline.approve.make_short",
        return_value=tmp_path / "out.mp4",
    ), patch(
        "app.pipeline.approve.generate_publish_meta", return_value=_fake_meta(),
    ), patch(
        "app.pipeline.approve.notion_update",
    ) as mock_update:
        process_approved()
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "p1" and args[1] == "generated"
    assert kwargs.get("internal_id") == "26-B001-S03"
    row = conn.execute(
        "SELECT internal_id FROM shorts WHERE id = ?", (sid,),
    ).fetchone()
    assert row["internal_id"] == "26-B001-S03"


def test_process_approved_no_internal_id_falls_back_to_youtube_id_pattern(
    conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """video.internal_id 없으면 internal_id 부여 안 함 + 기존 파일명 패턴 유지."""
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    _insert_short(conn, vid, 10.0, 30.0)
    conn.execute(
        "UPDATE shorts SET notion_page_id = 'p1', status = 'approved'",
    )
    conn.commit()
    _write_cache(tmp_path, "abc", _mk_moment(10.0, 30.0))
    fake_video_path = tmp_path / "abc.mp4"
    fake_video_path.touch()
    video = Video(
        id=vid, youtube_id="abc", title="t", duration=300,
        local_path=fake_video_path,
    )
    with patch(
        "app.pipeline.approve.get_video_by_youtube_id", return_value=video,
    ), patch(
        "app.pipeline.approve.make_short",
        return_value=tmp_path / "out.mp4",
    ), patch(
        "app.pipeline.approve.generate_publish_meta", return_value=_fake_meta(),
    ), patch(
        "app.pipeline.approve.notion_update",
    ) as mock_update:
        process_approved()
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "p1" and args[1] == "generated"
    assert kwargs.get("internal_id") is None


def test_process_approved_skips_when_no_approved(
    conn: sqlite3.Connection,
) -> None:
    """proposed/rejected만 있고 approved 없으면 0개 처리."""
    vid = upsert_video(conn, youtube_id="abc", title="t", duration=300)
    _insert_short(conn, vid, 10.0, 30.0)  # default 'proposed'
    with patch("app.pipeline.approve.make_short") as mock_make:
        n = process_approved()
    assert n == 0
    mock_make.assert_not_called()
