"""GitHub Actions용 — slot 시각에 노션 source-of-truth로 FB/IG/Threads/TikTok 게시.

cron 4번/일 (07/11/17/20 KST). 영빈 PC 무관. SQLite 의존성 X.

흐름:
  1. 노션 list_pages_by_status('scheduled')
  2. scheduled_at이 현재 시각 ±15분 안인 모먼트만 처리
  3. R2 URL 구성: {R2_PUBLIC_URL}/{Internal ID}.mp4
  4. 메타: 노션 Title + Description
  5. FB + IG + Threads 게시 (social.py)
  6. TikTok Buffer 큐 등록 (Buffer Free plan rate limit 안)
  7. 노션 status='게시'로 전환 + Preview URL 업데이트

workflow_dispatch로 수동 trigger 시 모든 'scheduled' 모먼트 처리 (시간 필터 무시).
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.integrations.notion import (
    _get_client,
    list_pages_by_status,
    update_status as notion_update,
)
from app.integrations.social import (
    SocialPostError,
    facebook_video_url,
    instagram_reel_url,
    post_facebook_video,
    post_instagram_reel,
    post_threads_video,
    threads_post_url,
)
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)

KST = timezone(timedelta(hours=9))
SLOT_TOLERANCE_MIN = 15  # 현재 시각 ±15분 모먼트 처리.

# workflow_dispatch에서 SKIP_TIME_FILTER=1 환경변수로 시간 필터 무시 (backlog 일괄 처리용).
SKIP_TIME_FILTER = os.environ.get("SKIP_TIME_FILTER", "").lower() in {"1", "true", "yes"}


def _internal_id_from_page(page_id: str) -> str | None:
    """노션 페이지의 Internal ID rich_text 추출."""
    client = _get_client()
    resp = client.pages.retrieve(page_id=page_id)
    props = resp.get("properties", {})
    iid_rt = props.get("Internal ID", {}).get("rich_text") or []
    return "".join(t.get("plain_text") or "" for t in iid_rt).strip() or None


def _within_slot(scheduled_at_iso: str) -> bool:
    """scheduled_at이 현재 시각 ±SLOT_TOLERANCE_MIN 안인지."""
    try:
        sched = datetime.fromisoformat(scheduled_at_iso)
    except ValueError:
        return False
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=KST)
    now = datetime.now(UTC)
    diff_min = abs((sched - now).total_seconds()) / 60
    return diff_min <= SLOT_TOLERANCE_MIN


def _build_post_text(title: str | None, description: str | None) -> str:
    """게시 본문 — description (또는 title fallback)."""
    if description:
        return description
    if title:
        return title
    return ""


def _publish_one_moment(page: dict[str, Any]) -> tuple[bool, dict[str, str]]:
    """단일 노션 모먼트 게시.

    Returns: (성공 여부, platform별 URL dict).
    """
    page_id = page["id"]
    title = page.get("title")
    description = page.get("description")
    iid = _internal_id_from_page(page_id)
    if not iid:
        log.warning("publish_socials.no_internal_id", page_id=page_id)
        return False, {}

    r2_url = f"{settings.r2_public_url.rstrip('/')}/{iid}.mp4"
    text = _build_post_text(title, description)
    if not text:
        log.warning("publish_socials.no_text", iid=iid)
        return False, {}

    results: dict[str, str] = {}

    # FB
    try:
        fb_id = post_facebook_video(r2_url, text)
        results["facebook"] = facebook_video_url(fb_id)
    except SocialPostError as e:
        log.warning("publish_socials.fb_failed", iid=iid, error=str(e))
        results["facebook"] = f"error:{e}"

    # IG
    try:
        ig_id = post_instagram_reel(r2_url, text)
        results["instagram"] = instagram_reel_url(ig_id)
    except SocialPostError as e:
        log.warning("publish_socials.ig_failed", iid=iid, error=str(e))
        results["instagram"] = f"error:{e}"

    # Threads
    try:
        th_id = post_threads_video(r2_url, text)
        results["threads"] = threads_post_url(th_id)
    except SocialPostError as e:
        log.warning("publish_socials.threads_failed", iid=iid, error=str(e))
        results["threads"] = f"error:{e}"

    # TikTok Buffer (선택). Free plan 24h ~13 publish 한도.
    if settings.buffer_access_token and settings.buffer_tiktok_channel_id:
        try:
            from app.integrations import buffer as buffer_api
            post_id = buffer_api.create_video_post(
                channel_id=settings.buffer_tiktok_channel_id,
                text=text, video_url=r2_url, service="tiktok",
            )
            results["tiktok"] = f"buffer:{post_id}"
        except Exception as e:
            log.warning("publish_socials.tiktok_buffer_failed", iid=iid, error=str(e))
            results["tiktok"] = f"error:{e}"

    # 게시 1개라도 성공이면 status='게시'로 전환.
    success = any(not v.startswith("error:") for v in results.values())
    return success, results


def main() -> None:
    print(f"=== publish_socials_from_notion start ===", flush=True)
    print(f"SKIP_TIME_FILTER={SKIP_TIME_FILTER}", flush=True)

    pages = list_pages_by_status("scheduled")
    print(f"scheduled pages from Notion: {len(pages)}", flush=True)

    processed = 0
    success = 0
    for page in pages:
        sched = page.get("scheduled_at")
        if not sched:
            continue
        if not SKIP_TIME_FILTER and not _within_slot(sched):
            continue

        processed += 1
        print(f"\n[{processed}] page={page['id']} scheduled_at={sched}", flush=True)
        ok, urls = _publish_one_moment(page)
        if ok:
            success += 1
            print(f"  OK platforms={list(urls.keys())}", flush=True)
            # 노션 status='게시'로 전환 + Preview URL을 facebook 또는 instagram으로
            preview = urls.get("instagram") or urls.get("facebook") or urls.get("threads")
            if preview and not preview.startswith("error:"):
                try:
                    notion_update(page["id"], "published", preview_url=preview)
                except Exception as e:
                    log.warning(
                        "publish_socials.notion_update_failed",
                        page_id=page["id"], error=str(e),
                    )
        else:
            print(f"  FAIL — all platforms errored", flush=True)
        for platform, url in urls.items():
            print(f"  {platform}: {url[:60]}", flush=True)

    print(f"\n=== done — processed={processed} success={success} ===", flush=True)


if __name__ == "__main__":
    main()
