"""Phase 6 멀티 플랫폼 게시 — 영빈 PC 단계 (R2 + YouTube publishAt 예약).

흐름:
  status='generated' + Scheduled At 있음
    → R2 업로드 (mp4 → public URL, Buffer + GitHub Actions 둘 다 fetch 가능)
    → Gemini publish meta (제목/설명/해시태그)
    → YouTube videos.insert (privacyStatus=private + publishAt=Scheduled At)
    → status='scheduled' + 노션 Published URLs 기록 (youtube URL만)

FB/IG/Threads는 GitHub Actions의 publish_socials_from_notion.py가 slot 시각에 처리.
TikTok도 동일 워크플로우에서 Buffer 큐로 처리. 영빈 PC에선 social 호출 X.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.integrations import buffer as buffer_api
from app.integrations import r2
from app.integrations.notion import fetch_page_meta as notion_fetch_page_meta
from app.integrations.notion import update_status as notion_update
from app.integrations.youtube import upload_short as youtube_upload
from app.integrations.youtube import video_url as youtube_video_url
from app.pipeline.analyze import load_cached_analysis
from app.pipeline.publish_meta import PublishMeta, generate_publish_meta
from app.storage.db import get_connection
from app.storage.models import MagicMoment
from app.utils.logger import get_logger

log = get_logger(__name__)


def _scheduled_at_to_utc_iso(scheduled_at: str) -> str:
    """노션 Scheduled At ISO (보통 KST +09:00) → UTC ISO ('Z' suffix)."""
    # Notion은 "2026-05-20T09:00:00.000+09:00" 형식.
    dt = datetime.fromisoformat(scheduled_at)
    if dt.tzinfo is None:
        # naive면 KST 가정.
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_buffer_text(meta: PublishMeta) -> str:
    """Buffer 캡션 텍스트 — 설명 + 해시태그."""
    base = meta.description.strip()
    if meta.hashtags:
        tag_str = " ".join(meta.hashtags)
        if tag_str not in base:
            base = f"{base}\n\n{tag_str}"
    return base[:2200]


def _resolve_meta(
    row: sqlite3.Row, page_id: str, moment: MagicMoment, channel: str = "ko",
) -> PublishMeta | None:
    """메타 결정 우선순위:
    1. SQLite publish_meta_json (process_approved에서 Gemini 생성)
    2. 노션 페이지 Title/Description (영빈 override) — 있으면 base에 덮어쓰기
    3. 둘 다 없으면 즉시 Gemini fallback (channel별 프롬프트)
    """
    base: PublishMeta | None = None
    raw = row["publish_meta_json"]
    if raw:
        try:
            base = PublishMeta.model_validate_json(raw)
        except Exception as e:
            log.warning("publish.meta_parse_failed", error=str(e))

    # 노션 fetch (영빈 override)
    try:
        notion_meta = notion_fetch_page_meta(page_id)
    except Exception as e:
        log.warning("publish.notion_fetch_failed", error=str(e))
        notion_meta = {"title": None, "description": None}

    if base is None:
        # SQLite 비어있음 → Gemini 즉시 생성
        try:
            base = generate_publish_meta(moment, channel=channel)
        except Exception as e:
            log.warning("publish.meta_gen_failed", error=str(e))
            return None

    # 영빈 override 반영
    title = notion_meta.get("title") or base.title
    description = notion_meta.get("description") or base.description
    return PublishMeta(
        title=title[:100],
        description=description[:2000],
        tags=base.tags,
        hashtags=base.hashtags,
    )


def _find_moment(
    youtube_id: str, start_time: float, end_time: float | None = None,
) -> MagicMoment | None:
    """Cache moment 매칭. 영빈 Time override 시 nearest cache + SQLite override (approve.py와 동일 정책)."""
    cached = load_cached_analysis(youtube_id)
    if cached is None or not cached.moments:
        return None
    exact = next(
        (m for m in cached.moments if abs(m.start_sec - start_time) < 0.5),
        None,
    )
    if exact is not None:
        return exact
    nearest = min(
        cached.moments,
        key=lambda m: abs(m.start_sec - start_time),
    )
    update: dict[str, float] = {"start_sec": start_time}
    if end_time is not None:
        update["end_sec"] = end_time
    return nearest.model_copy(update=update)


def _upload_r2(internal_id: str, mp4_path: Path) -> str:
    """R2 업로드. mp4 재생성 케이스 대비해 항상 새로 upload (key 동일하면 덮어쓰기).

    이전엔 `object_exists` 시 skip했으나 mp4 재생성 (% fix, 메타 수정 등) 후에도
    R2엔 옛 파일 그대로 남아 Buffer/외부 fetch에 옛 영상 노출되는 버그 있었음.
    """
    key = f"{internal_id}.mp4"
    return r2.upload_video(mp4_path, key=key)


def _publish_one(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> bool:
    """단일 shorts row 처리 → 성공 시 True. 실패는 status='error'."""
    short_id = row["id"]
    page_id = row["notion_page_id"]
    internal_id = row["internal_id"]
    generated_path = row["generated_path"]
    youtube_id = row["youtube_id"]
    scheduled_at = row["scheduled_at"]
    channel = row["channel"] if "channel" in row.keys() else "ko"

    if not generated_path or not Path(generated_path).exists():
        _mark_error(conn, short_id, page_id, "generated_path_missing")
        return False
    if not scheduled_at:
        log.info("publish.skip_no_schedule", short_id=short_id)
        return False
    if not internal_id:
        _mark_error(conn, short_id, page_id, "internal_id_missing")
        return False

    moment = _find_moment(youtube_id, row["start_time"], row["end_time"])
    if moment is None:
        _mark_error(conn, short_id, page_id, "moment_not_in_cache")
        return False

    # 1. R2 업로드. EN 채널은 FB/IG/Threads 게시 안 하지만, R2는 catch-up + Buffer
    #    수동 게시 시에도 쓸 수 있으므로 일관성 위해 동일하게 업로드.
    try:
        public_url = _upload_r2(internal_id, Path(generated_path))
    except Exception as e:
        log.warning("publish.r2_upload_failed", short_id=short_id, error=str(e))
        _mark_error(conn, short_id, page_id, f"r2_upload_failed: {e}")
        return False

    # 2. 메타 결정: SQLite publish_meta_json (process_approved 시점 Gemini 생성) +
    #    노션 Title/Description override (영빈 수정 우선).
    meta = _resolve_meta(row, page_id, moment, channel=channel)
    if meta is None:
        _mark_error(conn, short_id, page_id, "publish_meta_unresolved")
        return False

    published_urls: dict[str, str] = {}

    # 3. YouTube 예약 업로드 (channel별 OAuth — 영어 채널은 별도 token).
    try:
        publish_at_utc = _scheduled_at_to_utc_iso(scheduled_at)
        yt_video_id = youtube_upload(
            video_path=Path(generated_path),
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            publish_at_utc=publish_at_utc,
            channel=channel,
        )
        published_urls["youtube"] = youtube_video_url(yt_video_id)
    except Exception as e:
        log.warning("publish.youtube_failed", short_id=short_id, channel=channel, error=str(e))
        _mark_error(conn, short_id, page_id, f"youtube_upload_failed: {e}")
        return False

    # 4. FB/IG/Threads/TikTok은 GitHub Actions가 slot 시각에 처리 (publish_socials_from_notion).
    #    영빈 PC는 R2 + YouTube까지만. social 호출 X.

    # 5. SQLite + 노션 업데이트.
    urls_json = json.dumps(published_urls, ensure_ascii=False)
    conn.execute(
        "UPDATE shorts SET status = ?, published_urls = ? WHERE id = ?",
        ("scheduled", urls_json, short_id),
    )
    conn.commit()
    try:
        # 노션 Preview에 YouTube Shorts 링크만. Published URLs 컬럼은 비활성.
        notion_update(
            page_id, "scheduled",
            preview_url=published_urls.get("youtube"),
        )
    except Exception as e:
        log.warning(
            "publish.notion_update_failed",
            short_id=short_id, error=str(e),
        )

    log.info(
        "publish.done",
        short_id=short_id, internal_id=internal_id,
        platforms=list(published_urls.keys()),
    )
    return True


def _mark_error(
    conn: sqlite3.Connection, short_id: int, page_id: str | None, reason: str,
) -> None:
    log.warning("publish.mark_error", short_id=short_id, reason=reason)
    conn.execute(
        "UPDATE shorts SET status = 'error' WHERE id = ?", (short_id,),
    )
    conn.commit()
    if page_id:
        try:
            notion_update(page_id, "error")
        except Exception:
            pass


def publish_ready() -> int:
    """status='generated' + Scheduled At 있는 행 모두 게시 → 처리 수 반환."""
    conn = get_connection()
    processed = 0
    try:
        rows = conn.execute(
            "SELECT s.*, v.youtube_id "
            "FROM shorts s JOIN videos v ON s.source_video_id = v.id "
            "WHERE s.status = 'generated' "
            "  AND s.notion_page_id IS NOT NULL "
            "  AND s.scheduled_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            if _publish_one(conn, row):
                processed += 1
        log.info("publish.batch_done", processed=processed, total=len(rows))
        return processed
    finally:
        conn.close()


__all__ = ["publish_ready"]
