"""GitHub Actions용 — slot 시각에 노션 source-of-truth로 FB/IG/Threads/TikTok 게시.

cron 4번/일 (07/11/17/20 KST). 영빈 PC 무관. SQLite 의존성 X.

흐름:
  1. 노션 list_pages_by_status('scheduled')
  2. scheduled_at이 현재 시각 ±15분 안인 모먼트만 처리
  3. R2 URL 구성: {R2_PUBLIC_URL}/{Internal ID}.mp4
     — HEAD pre-flight: 파일 404면 게시 skip + 노션 '오류' + Preview에 재업로드 안내
  4. 메타: 노션 Title + Description
  5. FB + IG + Threads 게시 (social.py) + TikTok 게시 (Buffer shareNow)
  6. 노션 status='게시'로 전환 + Preview URL 업데이트
     — 전 platform 실패 시 status 유지 + Preview에 ⚠️ 실패 사유 기록
  7. platform 실패가 1건이라도 있으면 exit 1 → GitHub 워크플로우 실패 알림

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

import httpx

from app.config import settings
from app.integrations import buffer as buffer_api
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
R2_DELETE_AFTER_DAYS = 3  # 게시된 mp4도 슬롯 시각 후 이 기간은 R2 보존 (오삭제 방어).

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


def _r2_file_missing(url: str) -> bool:
    """R2 public URL HEAD — 404면 True.

    네트워크 오류 등 404 외 응답은 False (게시 시도 진행 — pre-flight가
    일시 장애로 게시를 막으면 안 됨).
    """
    try:
        resp = httpx.head(url, timeout=10)
    except Exception:
        return False
    return resp.status_code == 404


def _write_failure_note(page: dict[str, Any], note: str, *, to_error: bool = False) -> None:
    """게시 실패 사유를 노션 Preview 칸에 기록 (영빈 UI = 노션).

    to_error=True면 status를 '오류'로 전환하되, 이미 'scheduled'가 아닌 페이지
    (catch-up 대상 'published' 등)는 status를 건드리지 않고 노트만 남김.
    """
    cur_status = page.get("status") or "scheduled"
    new_status = "error" if to_error and cur_status == "scheduled" else cur_status
    try:
        notion_update(page["id"], new_status, preview_url=note[:300])
    except Exception as e:
        log.warning(
            "publish_socials.notion_update_failed",
            page_id=page["id"],
            error=str(e),
        )


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
    if _r2_file_missing(r2_url):
        log.warning("publish_socials.r2_file_missing", iid=iid, url=r2_url)
        _write_failure_note(
            page,
            f"⚠️ R2에 {iid}.mp4 없음 — PC에서 r2_reupload.py 실행 후 Status를 예약으로",
            to_error=True,
        )
        return False, {"r2": "error:R2 파일 없음 — 재업로드 필요"}
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
    if not success and results:
        errs = "; ".join(f"{p}: {v.removeprefix('error:')[:60]}" for p, v in results.items())
        _write_failure_note(page, f"⚠️ 게시 실패 — {errs}")
    return success, results


def _cleanup_previous_slot_r2(scheduled_pages: list[dict[str, Any]]) -> None:
    """노션 status='게시' + 슬롯 시각 3일 경과한 iid의 mp4만 R2에서 삭제.

    ★ delete-list 방식: 이전 keep-list 방식("scheduled 제외 전부 삭제")은 노션
    조회가 일시적으로 빈 결과를 주면 버킷 전체를 삭제했고 (2026-07-15 P030
    11개, 2026-08-07 P033 11개 유실 사고), 미리 올려둔 미래 클립(아직 scheduled
    아님)도 삭제했다 (2026-08-11 P034 4개). delete-list는 조회 실패/빈 결과 시
    "아무것도 안 지움" 쪽으로 실패한다. scheduled인 iid는 이중 안전장치로 제외.
    ★ 나이 가드: scheduled_at + R2_DELETE_AFTER_DAYS 지나야 삭제 — 어떤 로직
    버그가 나도 최근 파일은 물리적으로 못 지움 (저장비는 클립당 ~25MB로 미미).
    TARGET_INTERNAL_IDS 지정 시(수동 catch-up) skip. DRY_RUN 시에도 skip —
    로컬 검증에서 실제 R2 삭제되면 안 됨.
    """
    if TARGET_INTERNAL_IDS or DRY_RUN:
        return
    try:
        published_pages = list_pages_by_status("published")
    except Exception as e:
        log.warning("publish_socials.r2_cleanup_query_failed", error=str(e))
        return
    now = datetime.now(UTC)
    published_iids: set[str] = set()
    for p in published_pages:
        iid = p.get("internal_id")
        sched_str = p.get("scheduled_at")
        if not iid or not sched_str:
            continue
        try:
            sched = datetime.fromisoformat(sched_str)
        except ValueError:
            continue
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=KST)
        if now - sched >= timedelta(days=R2_DELETE_AFTER_DAYS):
            published_iids.add(iid)
    scheduled_iids = {iid for p in scheduled_pages if (iid := p.get("internal_id"))}
    if not published_iids:
        print("R2 cleanup: no deletable published iids — skip", flush=True)
        return
    try:
        keys = r2.list_object_keys()
    except Exception as e:
        log.warning("publish_socials.r2_list_failed", error=str(e))
        return
    deleted = 0
    for key in keys:
        if not key.endswith(".mp4"):
            continue
        iid = key[:-4]
        if iid not in published_iids or iid in scheduled_iids:
            continue
        try:
            r2.delete_object(key)
            deleted += 1
        except Exception as e:
            log.warning(
                "publish_socials.r2_cleanup_failed",
                key=key,
                error=str(e),
            )
    print(f"R2 cleanup: deleted {deleted} published mp4", flush=True)


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

    # '게시' 확인된 이전 슬롯 mp4만 정리 — scheduled/미래 클립은 건드리지 않음.
    _cleanup_previous_slot_r2(pages)

    processed = 0
    success = 0
    platform_errors = 0
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
        platform_errors += sum(1 for u in urls.values() if u.startswith("error:"))
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
            # R2 mp4는 다음 슬롯 실행 시 정리 (main() 초반 _cleanup_previous_slot_r2)
            # → TikTok 등 일부 platform 실패 시 재시도 window 확보 (~4시간).
        else:
            print("  FAIL — all platforms errored", flush=True)
        for platform, url in urls.items():
            print(f"  {platform}: {url[:60]}", flush=True)

    print(f"\n=== done — processed={processed} success={success} ===", flush=True)
    if platform_errors:
        # exit 1 → GitHub 워크플로우 실패 처리 → repo 소유자에게 실패 메일 자동 발송.
        print(f"⚠️ platform 게시 실패 {platform_errors}건 — exit 1", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
