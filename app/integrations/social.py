"""직접 API 게시 — Facebook Page + Instagram Reels + Threads (Buffer 대체).

각 platform 별 함수:
- `post_facebook_video(video_url, description)` → FB video ID
- `post_instagram_reel(video_url, caption)` → IG media ID
- `post_threads_video(video_url, text)` → Threads media ID

R2 public URL을 받아서 platform별 API 호출. Instagram + Threads는 2-step
(container 생성 → status polling → publish).

cron Step 7의 token refresh가 만료 7일 전 자동 갱신하므로 호출 시 token 검증 X.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

# httpx INFO logger의 URL token 노출 방지.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Graph API endpoints
FB_GRAPH = "https://graph.facebook.com/v21.0"
IG_GRAPH = "https://graph.instagram.com/v21.0"
THREADS_GRAPH = "https://graph.threads.net/v1.0"

# Container 처리 polling 설정.
CONTAINER_POLL_INTERVAL_SEC = 5
CONTAINER_POLL_MAX_ATTEMPTS = 60  # 최대 5분 대기


class SocialPostError(RuntimeError):
    """게시 실패."""


def _post(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """공통 POST helper. 4xx/5xx 시 SocialPostError raise (error body 포함)."""
    r = httpx.post(url, data=data, timeout=60)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = (
                f"{r.status_code} code={err.get('code')} "
                f"subcode={err.get('error_subcode')}: {err.get('message')}"
            )
        except Exception:
            msg = f"{r.status_code}: {r.text[:300]}"
        raise SocialPostError(msg)
    return r.json()


def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    r = httpx.get(url, params=params, timeout=30)
    if r.status_code >= 400:
        raise SocialPostError(f"{r.status_code}: {r.text[:300]}")
    return r.json()


def post_facebook_video(video_url: str, description: str) -> str:
    """Facebook Page에 영상 게시 (즉시 공개).

    Endpoint: POST /{page-id}/videos
    Returns: FB video ID
    """
    if not settings.fb_page_id or not settings.meta_access_token:
        raise SocialPostError("FB_PAGE_ID 또는 META_ACCESS_TOKEN 미설정")
    data = _post(
        f"{FB_GRAPH}/{settings.fb_page_id}/videos",
        {
            "file_url": video_url,
            "description": description[:2200],
            "published": "true",
            "access_token": settings.meta_access_token,
        },
    )
    video_id = str(data["id"])
    log.info("social.fb_posted", video_id=video_id)
    return video_id


def _wait_for_ig_container(creation_id: str) -> None:
    """IG container status_code FINISHED까지 polling."""
    for _ in range(CONTAINER_POLL_MAX_ATTEMPTS):
        time.sleep(CONTAINER_POLL_INTERVAL_SEC)
        data = _get(
            f"{IG_GRAPH}/{creation_id}",
            {
                "fields": "status_code,status",
                "access_token": settings.instagram_access_token,
            },
        )
        code = data.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise SocialPostError(f"IG container ERROR: {data.get('status')}")
    raise SocialPostError("IG container polling timeout (5분)")


def post_instagram_reel(video_url: str, caption: str) -> str:
    """Instagram Reels 게시 (2-step).

    1. POST /{ig-user-id}/media (media_type=REELS, video_url)
    2. polling status FINISHED
    3. POST /{ig-user-id}/media_publish (creation_id)
    Returns: IG media ID
    """
    if not settings.ig_user_id or not settings.instagram_access_token:
        raise SocialPostError("IG_USER_ID 또는 INSTAGRAM_ACCESS_TOKEN 미설정")

    container = _post(
        f"{IG_GRAPH}/{settings.ig_user_id}/media",
        {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "share_to_feed": "true",
            "access_token": settings.instagram_access_token,
        },
    )
    creation_id = str(container["id"])
    log.info("social.ig_container_created", creation_id=creation_id)

    _wait_for_ig_container(creation_id)

    published = _post(
        f"{IG_GRAPH}/{settings.ig_user_id}/media_publish",
        {
            "creation_id": creation_id,
            "access_token": settings.instagram_access_token,
        },
    )
    media_id = str(published["id"])
    log.info("social.ig_published", media_id=media_id)
    return media_id


def _wait_for_threads_container(creation_id: str) -> None:
    """Threads container FINISHED까지 polling."""
    for _ in range(CONTAINER_POLL_MAX_ATTEMPTS):
        time.sleep(CONTAINER_POLL_INTERVAL_SEC)
        data = _get(
            f"{THREADS_GRAPH}/{creation_id}",
            {
                "fields": "status,error_message",
                "access_token": settings.threads_access_token,
            },
        )
        status = data.get("status")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise SocialPostError(f"Threads container ERROR: {data.get('error_message')}")
    raise SocialPostError("Threads container polling timeout (5분)")


def post_threads_video(video_url: str, text: str) -> str:
    """Threads 영상 게시 (2-step). `me` endpoint 사용 (token user 자동 식별).

    1. POST /me/threads (media_type=VIDEO, video_url, text)
    2. polling status FINISHED
    3. POST /me/threads_publish (creation_id)
    Returns: Threads media ID
    """
    if not settings.threads_access_token:
        raise SocialPostError("THREADS_ACCESS_TOKEN 미설정")

    container = _post(
        f"{THREADS_GRAPH}/me/threads",
        {
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": text[:500],  # Threads 본문 한도
            "access_token": settings.threads_access_token,
        },
    )
    creation_id = str(container["id"])
    log.info("social.threads_container_created", creation_id=creation_id)

    _wait_for_threads_container(creation_id)

    published = _post(
        f"{THREADS_GRAPH}/me/threads_publish",
        {
            "creation_id": creation_id,
            "access_token": settings.threads_access_token,
        },
    )
    media_id = str(published["id"])
    log.info("social.threads_published", media_id=media_id)
    return media_id


def facebook_video_url(video_id: str) -> str:
    """게시된 FB video ID → 영빈 페이지 영상 URL."""
    return f"https://www.facebook.com/{settings.fb_page_id}/videos/{video_id}"


def instagram_reel_url(media_id: str) -> str:
    """IG media ID → reel URL. 정확한 shortcode 필요 시 API 추가 호출."""
    return f"https://www.instagram.com/reel/{media_id}"


def threads_post_url(media_id: str) -> str:
    """Threads media ID → post URL."""
    return f"https://www.threads.net/@swingcrew/post/{media_id}"


__all__ = [
    "SocialPostError",
    "facebook_video_url",
    "instagram_reel_url",
    "post_facebook_video",
    "post_instagram_reel",
    "post_threads_video",
    "threads_post_url",
]
