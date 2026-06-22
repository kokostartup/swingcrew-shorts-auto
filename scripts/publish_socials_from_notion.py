"""GitHub Actions용 — slot 시각에 노션 source-of-truth로 FB/IG/Threads/TikTok 게시.

cron 4번/일 (07/11/17/20 KST). 영빈 PC 무관. SQLite 의존성 X.

흐름:
  1. 노션 list_pages_by_status('scheduled')
  2. scheduled_at이 현재 시각 ±15분 안인 모먼트만 처리
  3. R2 URL 구성: {R2_PUBLIC_URL}/{Internal ID}.mp4
  4. 메타: 노션 Title + Description
  5. FB + IG + Threads 게시 (social.py) + TikTok 게시 (Buffer shareNow)
  6. 노션 status='게시'로 전환 + Preview URL 업데이트

TikTok 처리 (2026-06-22 변경): Buffer customScheduled 예약 한도 10개 제한 회피 →
publish_ready 단계에서 Buffer 호출 안 함. 대신 슬롯 시각에 여기서 Buffer shareNow
(즉시 게시) 호출. 결과적으로 Buffer 예약 큐 안 쌓임.

workflow_dispatch로 수동 trigger 시 모든 'scheduled' 모먼트 처리 (시간 필터 무시).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.integrations import r2
from app.integrations.notion import (
    _get_client,
    list_pages_by_status,
)
from app.integrations.notion import (
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

# TARGET_INTERNAL_IDS: 콤마 구분 internal_id 목록. 지정 시 그 모먼트만 처리 (cron 지연 catch-up용).
_target_raw = os.environ.get("TARGET_INTERNAL_IDS", "").strip()
TARGET_INTERNAL_IDS: set[str] = (
    {x.strip() for x in _target_raw.split(",") if x.strip()} if _target_raw else set()
)

# DRY_RUN: True면 모먼트 fetch + 필터 흐름까지만, 실제 social API 호출 skip.
# Cloudflare Worker→GitHub workflow 통신 검증용 (실제 게시 안 함).
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}

# TARGET_PLATFORMS: 콤마 구분 facebook/instagram/threads 부분집합. 비어있으면 모두 시도.
# 특정 platform만 catch-up 시 (예: FB 이미 게시됐는데 IG/Threads만 누락) 중복 게시 방지.
_platforms_raw = os.environ.get("TARGET_PLATFORMS", "").strip().lower()
TARGET_PLATFORMS: set[str] = (
    {x.strip() for x in _platforms_raw.split(",") if x.strip()} if _platforms_raw else set()
)


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

    # TARGET_PLATFORMS 지정 시 그 platform만 시도 (catch-up — 이미 게시된 것 중복 회피).
    def _enabled(p: str) -> bool:
        return not TARGET_PLATFORMS or p in TARGET_PLATFORMS

    if _enabled("facebook"):
        try:
            fb_id = post_facebook_video(r2_url, text)
            results["facebook"] = facebook_video_url(fb_id)
        except SocialPostError as e:
            log.warning("publish_socials.fb_failed", iid=iid, error=str(e))
            results["facebook"] = f"error:{e}"

    if _enabled("instagram"):
        try:
            ig_id = post_instagram_reel(r2_url, text)
            results["instagram"] = instagram_reel_url(ig_id)
        except SocialPostError as e:
            log.warning("publish_socials.ig_failed", iid=iid, error=str(e))
            results["instagram"] = f"error:{e}"

    if _enabled("threads"):
        try:
            th_id = post_threads_video(r2_url, text)
            results["threads"] = threads_post_url(th_id)
        except SocialPostError as e:
            log.warning("publish_socials.threads_failed", iid=iid, error=str(e))
            results["threads"] = f"error:{e}"

    # TikTok — Buffer shareNow로 즉시 게시 (예약 한도 회피).
    # BUFFER_ACCESS_TOKEN 미설정 시 silent skip.
    if _enabled("tiktok") and settings.buffer_access_token:
        try:
            channels = buffer_api.get_channels_by_service()
            tiktok_channel_id = channels.get("tiktok")
            if tiktok_channel_id:
                post_id = buffer_api.create_video_post(
                    channel_id=tiktok_channel_id,
                    text=text,
                    video_url=r2_url,
                    service="tiktok",
                    share_now=True,
                )
                results["tiktok"] = f"buffer:{post_id}" if post_id else "buffer:queued"
            else:
                log.warning("publish_socials.tiktok_no_channel", iid=iid)
        except Exception as e:
            log.warning("publish_socials.tiktok_failed", iid=iid, error=str(e))
            results["tiktok"] = f"error:{e}"

    # 시도된 platform 중 1개라도 성공이면 status='게시'로 전환.
    success = any(not v.startswith("error:") for v in results.values())
    return success, results


def main() -> None:
    print("=== publish_socials_from_notion start ===", flush=True)
    print(f"SKIP_TIME_FILTER={SKIP_TIME_FILTER}", flush=True)
    if TARGET_INTERNAL_IDS:
        print(f"TARGET_INTERNAL_IDS={sorted(TARGET_INTERNAL_IDS)}", flush=True)
    if TARGET_PLATFORMS:
        print(f"TARGET_PLATFORMS={sorted(TARGET_PLATFORMS)}", flush=True)

    pages = list_pages_by_status("scheduled")
    # TARGET_INTERNAL_IDS 지정 시 'published' 페이지도 catch-up 대상 (이미 부분 게시된 모먼트의
    # 누락 platform 보완용). 평상시 cron엔 'scheduled'만.
    if TARGET_INTERNAL_IDS:
        pages.extend(list_pages_by_status("published"))
    print(f"candidate pages from Notion: {len(pages)}", flush=True)

    processed = 0
    success = 0
    for page in pages:
        sched = page.get("scheduled_at")
        if not sched:
            continue
        # TARGET 지정 시 그 모먼트만 처리 (cron 지연 catch-up 또는 specific re-publish).
        if TARGET_INTERNAL_IDS:
            iid = _internal_id_from_page(page["id"])
            if iid not in TARGET_INTERNAL_IDS:
                continue
        elif not SKIP_TIME_FILTER and not _within_slot(sched):
            continue

        processed += 1
        print(f"\n[{processed}] page={page['id']} scheduled_at={sched}", flush=True)
        if DRY_RUN:
            iid = _internal_id_from_page(page["id"])
            print(f"  DRY_RUN — would publish iid={iid}", flush=True)
            success += 1
            continue
        ok, urls = _publish_one_moment(page)
        if ok:
            success += 1
            ok_platforms = [p for p, u in urls.items() if u and not u.startswith("error:")]
            print(f"  OK platforms={ok_platforms}", flush=True)
            # error로 시작하지 않는 URL 중 우선순위 (instagram > facebook > threads).
            preview: str | None = None
            for p in ("instagram", "facebook", "threads"):
                u = urls.get(p)
                if u and not u.startswith("error:"):
                    preview = u
                    break
            if preview:
                try:
                    notion_update(page["id"], "published", preview_url=preview)
                except Exception as e:
                    log.warning(
                        "publish_socials.notion_update_failed",
                        page_id=page["id"],
                        error=str(e),
                    )
            # 모든 social 게시 + 노션 'published' 전환 끝 → R2 mp4 source 불필요 → 삭제.
            # 삭제 실패해도 게시 자체는 끝났으므로 swallow.
            iid = _internal_id_from_page(page["id"])
            if iid:
                try:
                    r2.delete_object(f"{iid}.mp4")
                except Exception as e:
                    log.warning(
                        "publish_socials.r2_delete_failed",
                        iid=iid,
                        error=str(e),
                    )
        else:
            print("  FAIL — all platforms errored", flush=True)
        for platform, url in urls.items():
            print(f"  {platform}: {url[:60]}", flush=True)

    print(f"\n=== done — processed={processed} success={success} ===", flush=True)


if __name__ == "__main__":
    main()
