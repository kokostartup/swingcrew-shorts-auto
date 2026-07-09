"""Notion 승인 워크플로우 (Phase 5) — sync / poll / process 함수.

상태 전이:
    proposed  ← Gemini 분석 후 sync_to_notion이 push
    approved  ← 영빈이 노션에서 토글 → poll_status_from_notion이 sync
    generated ← polling 후 process_approved가 ffmpeg 실행
    scheduled / published ← Phase 6에서 채움
    rejected  ← 영빈 토글 → poll이 sync, 이후 처리는 없음
    error     ← 처리 단계 실패
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import settings
from app.integrations.notion import create_page as notion_create_page
from app.integrations.notion import list_pages_by_status as notion_list
from app.integrations.notion import update_status as notion_update
from app.pipeline.analyze import load_cached_analysis
from app.pipeline.edit import make_short
from app.pipeline.publish_meta import generate_publish_meta
from app.pipeline.scene import classify_scene_with_metrics
from app.storage.db import get_connection, get_video_by_youtube_id
from app.storage.models import AnalysisResult, Video
from app.utils.logger import get_logger

log = get_logger(__name__)


def sync_to_notion(video: Video, result: AnalysisResult) -> int:
    """분석된 모먼트 중 노션 push 안 된 것만 push (멱등).

    SQLite shorts.notion_page_id가 NULL인 행만 대상.

    한국 채널 (video.channel='ko'): notion + SQLite status='proposed'
        → 영빈이 노션에서 ✅ 토글 필요.
    영어 채널 (video.channel='en'): notion + SQLite status='approved' (자동)
        → 영빈 토글 단계 skip, 다음 process_approved에서 바로 ffmpeg.
    """
    if video.id is None:
        raise ValueError("video.id 누락 — analyze() 먼저 호출하세요.")

    # EN 채널은 영빈 ✅ 단계 skip → 'approved'로 즉시 진입.
    initial_status = "approved" if video.channel == "en" else "proposed"

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM shorts WHERE source_video_id = ? ORDER BY start_time",
            (video.id,),
        ).fetchall()
        # 분석 결과의 모먼트를 start_time 키로 인덱싱 (0.1초 단위 반올림)
        moments_by_key = {round(m.start_sec, 1): m for m in result.moments}
        created = 0
        for row in rows:
            if row["notion_page_id"]:
                continue
            key = round(row["start_time"], 1)
            moment = moments_by_key.get(key)
            if moment is None:
                log.warning(
                    "approve.moment_not_in_cache",
                    short_id=row["id"],
                    start=row["start_time"],
                )
                continue
            # 노션 push 시점에 short internal_id 부여 (없으면) — 영빈이 노션에서
            # 모먼트별 구분 가능하도록 (예: 26-P004-S01).
            short_iid = row["internal_id"]
            if not short_iid and video.internal_id:
                short_iid = _next_short_internal_id(
                    conn,
                    video.internal_id,
                    video.id,
                )
                conn.execute(
                    "UPDATE shorts SET internal_id = ? WHERE id = ?",
                    (short_iid, row["id"]),
                )
            page_id = notion_create_page(
                video,
                moment,
                short_iid,
                channel=video.channel,
                initial_status=initial_status,
            )
            from datetime import UTC, datetime

            conn.execute(
                "UPDATE shorts SET notion_page_id = ?, status = ?, pushed_at = ? WHERE id = ?",
                (page_id, initial_status, datetime.now(UTC).isoformat(), row["id"]),
            )
            created += 1
        conn.commit()
        log.info(
            "approve.sync_to_notion",
            youtube_id=video.youtube_id,
            channel=video.channel,
            initial_status=initial_status,
            created=created,
            total=len(rows),
        )
        return created
    finally:
        conn.close()


def poll_status_from_notion(channel: str = "ko") -> dict[str, int]:
    """노션 → SQLite 단방향 sync (영빈 토글 결과 + Scheduled At + Scene Type override).

    영어 채널은 영빈 토글 단계 없지만, Scheduled At/Scene/Time override는 가능하므로
    호출 자체는 의미 있음 (run_daily가 ko/en 각각 호출).

    Returns:
        {"approved": N, "rejected": N, "scheduled_synced": N, "scene_overridden": N}
    """
    counts = {
        "approved": 0,
        "rejected": 0,
        "scheduled_synced": 0,
        "scene_overridden": 0,
        "time_overridden": 0,
        "copy_overridden": 0,
    }
    conn = get_connection()
    try:
        # 'generated'는 status 전환 아닌 Scheduled At sync 전용 (publish 트리거).
        for status_en in ("approved", "rejected", "generated"):
            pages = notion_list(status_en, channel=channel)
            for p in pages:
                row = conn.execute(
                    "SELECT id, status, scheduled_at, scene_type, "
                    "       start_time, end_time, copy1, copy2 "
                    "FROM shorts WHERE notion_page_id = ?",
                    (p["id"],),
                ).fetchone()
                if row is None:
                    continue
                new_sched = p["scheduled_at"]
                new_scene = p["scene_type"]
                new_start = p.get("start_sec")
                new_end = p.get("end_sec")
                new_copy1 = p.get("copy1")
                new_copy2 = p.get("copy2")
                if row["status"] != status_en and status_en in {"approved", "rejected"}:
                    conn.execute(
                        "UPDATE shorts SET status = ?, scheduled_at = ? WHERE id = ?",
                        (status_en, new_sched, row["id"]),
                    )
                    counts[status_en] += 1
                elif new_sched and new_sched != row["scheduled_at"]:
                    conn.execute(
                        "UPDATE shorts SET scheduled_at = ? WHERE id = ?",
                        (new_sched, row["id"]),
                    )
                    counts["scheduled_synced"] += 1
                # Scene Type override: 노션 값이 있고 SQLite와 다르면 노션 우선.
                if new_scene and new_scene != row["scene_type"]:
                    conn.execute(
                        "UPDATE shorts SET scene_type = ? WHERE id = ?",
                        (new_scene, row["id"]),
                    )
                    counts["scene_overridden"] += 1
                # Start/End Sec override: 영빈이 직접 수정한 시간.
                # 0.1초 이상 차이나면 sync (반올림 오차 제외).
                time_changed = False
                if new_start is not None and abs(new_start - row["start_time"]) > 0.1:
                    conn.execute(
                        "UPDATE shorts SET start_time = ? WHERE id = ?",
                        (new_start, row["id"]),
                    )
                    time_changed = True
                if new_end is not None and abs(new_end - row["end_time"]) > 0.1:
                    conn.execute(
                        "UPDATE shorts SET end_time = ? WHERE id = ?",
                        (new_end, row["id"]),
                    )
                    time_changed = True
                if time_changed:
                    counts["time_overridden"] += 1
                # Hook (copy1/copy2) override: 노션에서 영빈이 시그니처 카피 수정.
                copy_changed = False
                if new_copy1 and new_copy1 != row["copy1"]:
                    conn.execute(
                        "UPDATE shorts SET copy1 = ? WHERE id = ?",
                        (new_copy1, row["id"]),
                    )
                    copy_changed = True
                if new_copy2 and new_copy2 != row["copy2"]:
                    conn.execute(
                        "UPDATE shorts SET copy2 = ? WHERE id = ?",
                        (new_copy2, row["id"]),
                    )
                    copy_changed = True
                if copy_changed:
                    counts["copy_overridden"] += 1
        conn.commit()
        log.info("approve.poll_status_from_notion", **counts)
        return counts
    finally:
        conn.close()


def _next_short_internal_id(
    conn: sqlite3.Connection,
    video_internal_id: str,
    source_video_id: int,
) -> str:
    """그 영상에서 이미 부여된 S 번호의 다음 값을 반환.

    예: 기존에 S01, S02 있으면 'S03'. 없으면 'S01'.
    """
    rows = conn.execute(
        "SELECT internal_id FROM shorts WHERE source_video_id = ? AND internal_id IS NOT NULL",
        (source_video_id,),
    ).fetchall()
    used = set()
    prefix = f"{video_internal_id}-S"
    for r in rows:
        sid = r["internal_id"] or ""
        if sid.startswith(prefix) and sid[len(prefix) :].isdigit():
            used.add(int(sid[len(prefix) :]))
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n:02d}"


def process_approved(*, skip_publish_meta: bool = False) -> int:
    """status='approved' 행에 대해 ffmpeg로 시그니처 mp4 생성.

    승인 시점에 internal_id 부여 (예: 26-B001-S01).
    완료 시 status='generated' + Notion '생성' + Internal ID 업데이트.
    실패 시 status='error' + Notion '오류'.

    skip_publish_meta:
      False (기본, cron 호출용) — Gemini로 publish_meta 자동 생성 + 노션 Title/Description 채움.
      True (Claude Code 세션 호출용) — publish_meta skip. Claude Code 메인이 직후
        publish-meta-writer 에이전트로 처리하도록 publish_meta_json=NULL 상태로 둠.
    """
    settings.shorts_output_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    processed = 0
    try:
        rows = conn.execute(
            "SELECT s.*, v.youtube_id, v.internal_id AS video_internal_id "
            "FROM shorts s JOIN videos v ON s.source_video_id = v.id "
            "WHERE s.status = 'approved' AND s.notion_page_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            short_id = row["id"]
            youtube_id = row["youtube_id"]
            page_id = row["notion_page_id"]
            video_internal_id = row["video_internal_id"]

            # P시리즈(ko)는 풀스크린 변형 전용 (영빈 결정 2026-07-09) —
            # framing spec은 Claude Code 세션의 framing-director 에이전트가 만들므로
            # cron path에서는 렌더하지 않고 approved로 남겨둔다 (legacy 포맷 방지).
            # 렌더는 scripts/render_fullscreen.py가 담당.
            if (
                row["channel"] == "ko"
                and video_internal_id
                and "-P" in video_internal_id
            ):
                log.info(
                    "approve.skip_p_series_fullscreen",
                    short_id=short_id,
                    internal_id=row["internal_id"] or video_internal_id,
                )
                continue

            cached = load_cached_analysis(youtube_id)
            if cached is None:
                _mark_error(conn, short_id, page_id, "analysis_cache_missing")
                continue

            moment = next(
                (m for m in cached.moments if abs(m.start_sec - row["start_time"]) < 0.5),
                None,
            )
            if moment is None and cached.moments:
                # 영빈이 노션에서 Time override한 케이스 — nearest cache moment 선택 후
                # 시간만 SQLite ground truth로 override (copy/메타는 cache 그대로).
                nearest = min(
                    cached.moments,
                    key=lambda m: abs(m.start_sec - row["start_time"]),
                )
                moment = nearest.model_copy(
                    update={
                        "start_sec": row["start_time"],
                        "end_sec": row["end_time"],
                    }
                )
                log.info(
                    "approve.cache_moment_time_overridden",
                    short_id=short_id,
                    cache_start=nearest.start_sec,
                    sqlite_start=row["start_time"],
                )
            if moment is None:
                _mark_error(conn, short_id, page_id, "moment_not_in_cache")
                continue
            # 영빈 노션 Hook override (copy1/copy2). SQLite 값 있으면 cache 덮어쓰기.
            copy_overrides: dict[str, str] = {}
            if row["copy1"]:
                copy_overrides["copy1"] = row["copy1"]
            if row["copy2"]:
                copy_overrides["copy2"] = row["copy2"]
            if copy_overrides:
                moment = moment.model_copy(update=copy_overrides)
                log.info(
                    "approve.copy_overridden",
                    short_id=short_id,
                    **copy_overrides,
                )

            video = get_video_by_youtube_id(conn, youtube_id)
            if video is None or not video.local_path.exists():
                _mark_error(conn, short_id, page_id, "source_video_missing")
                continue

            # internal_id 부여 (기존에 있으면 재사용 — idempotent).
            short_internal_id = row["internal_id"]
            if not short_internal_id and video_internal_id:
                short_internal_id = _next_short_internal_id(
                    conn,
                    video_internal_id,
                    row["source_video_id"],
                )

            # 영빈 결정 2026-06-05: 모든 영상을 wide letterbox 강제 (face_count 무관).
            # classify_scene_with_metrics가 face detection skip하고 항상
            # (face_centered_dynamic, 0.5, [(0, dur, 0.5, 2)]) 반환.
            # 옛 face_segments cache 있어도 무시하고 항상 새 룰 적용.
            try:
                new_scene, new_cx, new_segments = classify_scene_with_metrics(
                    video.local_path,
                    moment.start_sec,
                    moment.end_sec,
                )
                face_cx = new_cx
                backfilled_segments = new_segments
                current_scene = new_scene
                segments_to_save = (
                    json.dumps([list(s) for s in new_segments]) if new_segments else None
                )
                conn.execute(
                    "UPDATE shorts SET face_center_x = ?, scene_type = ?, "
                    "face_segments = ? WHERE id = ?",
                    (face_cx, current_scene, segments_to_save, short_id),
                )
                conn.commit()
            except Exception as e:
                log.warning(
                    "approve.scene_backfill_failed",
                    short_id=short_id,
                    error=str(e),
                )
                face_cx = row["face_center_x"]
                current_scene = row["scene_type"]
                backfilled_segments = None

            # SQLite scene_type 우선 (영빈 override 반영). cache fallback.
            strategy = current_scene or moment.scene_type or "letterbox_4_5"
            if short_internal_id:
                output = settings.shorts_output_dir / f"{short_internal_id}.mp4"
            else:
                output = (
                    settings.shorts_output_dir
                    / f"{youtube_id}_{int(moment.start_sec * 10):05d}.mp4"
                )
            face_segments = backfilled_segments  # backfill 결과 우선
            if face_segments is None:
                segments_json = row["face_segments"]
                if segments_json:
                    try:
                        raw = json.loads(segments_json)
                        # 3-tuple (legacy) / 4-tuple 둘 다 호환.
                        face_segments = [
                            (
                                float(s[0]),
                                float(s[1]),
                                float(s[2]),
                                int(s[3]) if len(s) >= 4 else 1,
                            )
                            for s in raw
                        ]
                    except Exception as e:
                        log.warning(
                            "approve.face_segments_parse_failed",
                            short_id=short_id,
                            error=str(e),
                        )
            try:
                make_short(
                    video.local_path,
                    moment.start_sec,
                    moment.end_sec,
                    strategy,  # type: ignore[arg-type]
                    moment.copy1,
                    moment.copy2,
                    output,
                    face_center_x=face_cx,
                    face_segments=face_segments,
                    internal_id=short_internal_id,
                )
            except Exception as e:
                log.warning(
                    "approve.ffmpeg_failed",
                    short_id=short_id,
                    error=str(e),
                )
                _mark_error(conn, short_id, page_id, f"ffmpeg_failed: {e}")
                continue

            # Gemini publish_meta 생성 (title/description/tags/hashtags).
            # 영빈이 노션에서 Title/Description 검토 + 수정 가능.
            # Claude Code 세션에서 호출 시 skip_publish_meta=True → 메인이 직후
            # publish-meta-writer 에이전트로 처리.
            meta_json: str | None = None
            meta_title: str | None = None
            meta_description: str | None = None
            if not skip_publish_meta:
                try:
                    meta = generate_publish_meta(moment, channel=video.channel)
                    meta_json = meta.model_dump_json()
                    meta_title = meta.title
                    meta_description = meta.description
                except Exception as e:
                    log.warning(
                        "approve.publish_meta_failed",
                        short_id=short_id,
                        error=str(e),
                    )
            else:
                log.info(
                    "approve.publish_meta_skipped",
                    short_id=short_id,
                    reason="claude_code_agent_path",
                )

            # skip_publish_meta=True인 경우 publish_meta_json은 그대로 보존
            # (Claude Code 세션에서 publish-meta-writer 에이전트 결과가 이미 있을 수 있음)
            if skip_publish_meta:
                conn.execute(
                    "UPDATE shorts SET status = 'generated', "
                    "generated_path = ?, internal_id = ? "
                    "WHERE id = ?",
                    (str(output), short_internal_id, short_id),
                )
            else:
                conn.execute(
                    "UPDATE shorts SET status = 'generated', "
                    "generated_path = ?, internal_id = ?, "
                    "publish_meta_json = ? "
                    "WHERE id = ?",
                    (str(output), short_internal_id, meta_json, short_id),
                )
            conn.commit()

            update_extra: dict[str, Any] = {"internal_id": short_internal_id}
            if meta_title:
                update_extra["title"] = meta_title
            if meta_description:
                update_extra["description"] = meta_description
            try:
                notion_update(page_id, "generated", **update_extra)
            except Exception as e:
                log.warning(
                    "approve.notion_status_update_failed",
                    short_id=short_id,
                    error=str(e),
                )
            processed += 1
        log.info("approve.process_approved", processed=processed)
        return processed
    finally:
        conn.close()


def auto_reject_stale(max_age_days: int = 7) -> int:
    """N일 동안 ✅ 안 받은 'proposed' 모먼트 → 자동 거절.

    SQLite shorts.pushed_at 기준. status='rejected' + 노션 Status='거절'.
    EN 채널은 'proposed' 단계 안 거치므로 (즉시 'approved') 영향 없음.
    """
    from datetime import UTC, datetime, timedelta

    cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
    conn = get_connection()
    rejected = 0
    try:
        rows = conn.execute(
            "SELECT id, notion_page_id, channel FROM shorts "
            "WHERE status = 'proposed' "
            "  AND pushed_at IS NOT NULL "
            "  AND pushed_at < ?",
            (cutoff,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE shorts SET status = 'rejected' WHERE id = ?",
                (r["id"],),
            )
            if r["notion_page_id"]:
                try:
                    notion_update(r["notion_page_id"], "rejected")
                except Exception as e:
                    log.warning(
                        "approve.auto_reject_notion_failed",
                        short_id=r["id"],
                        error=str(e),
                    )
            rejected += 1
        conn.commit()
        if rejected:
            log.info(
                "approve.auto_rejected",
                count=rejected,
                max_age_days=max_age_days,
            )
        return rejected
    finally:
        conn.close()


def _mark_error(
    conn: sqlite3.Connection,
    short_id: int,
    page_id: str | None,
    reason: str,
) -> None:
    """SQLite + Notion 둘 다 error 상태로 마킹 (Notion 실패는 swallow)."""
    log.warning("approve.mark_error", short_id=short_id, reason=reason)
    conn.execute(
        "UPDATE shorts SET status = 'error' WHERE id = ?",
        (short_id,),
    )
    conn.commit()
    if page_id:
        try:
            notion_update(page_id, "error")
        except Exception as e:
            log.warning(
                "approve.notion_error_update_failed",
                short_id=short_id,
                error=str(e),
            )


__all__ = [
    "auto_reject_stale",
    "poll_status_from_notion",
    "process_approved",
    "sync_to_notion",
]
