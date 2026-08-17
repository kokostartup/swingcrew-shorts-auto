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
from collections.abc import Callable
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

# 컨테이너 단계 재시도 (IG/스레드 공통).
CONTAINER_MAX_TRIES = 3
CONTAINER_RETRY_BACKOFF_SEC = 30

# 스레드 컨테이너 error_message 중 재시도로 풀리는 값.
# `UNKNOWN`은 스레드가 실패를 분류하지 못한 서버 측 일시 오류 — 같은 mp4가 다음
# 시도에 정상 처리된다 (2026-08-17 26-B016-S05: FB/IG는 동일 R2 URL로 성공,
# 스레드만 11초 만에 UNKNOWN. 같은 인코딩의 S02는 이틀 전 스레드 게시 성공).
# 나머지(INVALID_*, FILE_TOO_LARGE 등)는 스펙 위반이라 재시도해도 결과가 같다.
# 새로 관측되는 일시 오류가 있으면 여기에만 추가할 것 — 기본은 재시도 안 함.
# IG는 이런 enum 없이 자유 문자열만 줘서 같은 방식으로 못 거른다 (_wait_for_ig_container 참고).
THREADS_RETRYABLE_CONTAINER_ERRORS = frozenset({"UNKNOWN"})


class SocialPostError(RuntimeError):
    """게시 실패."""


class _ContainerRetryableError(SocialPostError):
    """컨테이너 단계 일시 실패 — 컨테이너를 새로 만들면 풀린다."""


def _post(url: str, data: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
    """공통 POST helper. 4xx/5xx + 네트워크 예외 모두 SocialPostError로 변환.

    httpx의 raw exception (ReadTimeout/ConnectError 등)을 잡지 않으면 publish_socials의
    `except SocialPostError` 못 잡아 script 전체 die — 다른 platform 처리 + 노션 update
    모두 누락. 반드시 변환.

    FB `/videos` 같은 Meta server-side fetch는 R2 download + 인코딩 시간 누적으로
    응답 늦는 경우 있음 → timeout 기본 180s.
    """
    try:
        r = httpx.post(url, data=data, timeout=timeout)
    except httpx.HTTPError as e:
        raise SocialPostError(f"http_error: {type(e).__name__}: {e}") from e
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
    try:
        r = httpx.get(url, params=params, timeout=30)
    except httpx.HTTPError as e:
        raise SocialPostError(f"http_error: {type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise SocialPostError(f"{r.status_code}: {r.text[:300]}")
    return r.json()


def _container_with_retry(
    create: Callable[[], str], wait: Callable[[str], None], platform: str
) -> str:
    """컨테이너 생성 → 처리 완료 대기. 일시 실패면 새 컨테이너로 재시도.

    ERROR 상태 컨테이너는 되살릴 수 없어 재시도는 반드시 재생성이어야 한다.

    ★ 여기서 돌려준 creation_id로 하는 publish 호출은 절대 재시도하지 말 것 —
      플랫폼이 게시를 끝내고 응답만 유실돼도 같은 영상이 두 번 올라간다.
      재시도가 안전한 구간은 게시 확정 전인 이 함수까지다.
    """
    attempt = 0
    while True:
        attempt += 1
        creation_id = create()
        log.info(f"social.{platform}_container_created", creation_id=creation_id, attempt=attempt)
        try:
            wait(creation_id)
            return creation_id
        except _ContainerRetryableError as e:
            if attempt >= CONTAINER_MAX_TRIES:
                raise SocialPostError(f"{e} — 컨테이너 {attempt}회 재생성 모두 실패") from e
            log.warning(
                f"social.{platform}_container_retry",
                creation_id=creation_id,
                attempt=attempt,
                error=str(e),
            )
            time.sleep(CONTAINER_RETRY_BACKOFF_SEC)


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
            # IG는 스레드의 error_message 같은 enum이 없고 `status`가 자유 문장이라
            # 일시/영구를 구분할 수 없다 → 일단 재시도. 컨테이너 단계라 중복 게시는
            # 없고, 영구 오류(코덱/비율 위반)도 ERROR가 빨리 떨어져서 3회 비용이
            # 분 단위로 묶인다 — 폴링 timeout(5분)과 달리 예산을 잠식하지 않는다.
            raise _ContainerRetryableError(f"IG container ERROR: {data.get('status')}")
    raise SocialPostError("IG container polling timeout (5분)")


def post_instagram_reel(video_url: str, caption: str) -> str:
    """Instagram Reels 게시 (2-step).

    1. POST /{ig-user-id}/media (media_type=REELS, video_url)
    2. polling status FINISHED — 실패 시 컨테이너 재생성으로 재시도
    3. POST /{ig-user-id}/media_publish (creation_id)
    Returns: IG media ID
    """
    if not settings.ig_user_id or not settings.instagram_access_token:
        raise SocialPostError("IG_USER_ID 또는 INSTAGRAM_ACCESS_TOKEN 미설정")

    creation_id = _container_with_retry(
        lambda: str(
            _post(
                f"{IG_GRAPH}/{settings.ig_user_id}/media",
                {
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption[:2200],
                    "share_to_feed": "true",
                    "access_token": settings.instagram_access_token,
                },
            )["id"]
        ),
        _wait_for_ig_container,
        "ig",
    )

    # publish는 재시도하지 않는다 — IG가 게시한 뒤 응답만 유실되면 중복 게시된다.
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
            reason = str(data.get("error_message"))
            msg = f"Threads container ERROR: {reason}"
            if reason in THREADS_RETRYABLE_CONTAINER_ERRORS:
                raise _ContainerRetryableError(msg)
            raise SocialPostError(msg)
    raise SocialPostError("Threads container polling timeout (5분)")


def post_threads_video(video_url: str, text: str) -> str:
    """Threads 영상 게시 (2-step). `me` endpoint 사용 (token user 자동 식별).

    1. POST /me/threads (media_type=VIDEO, video_url, text)
    2. polling status FINISHED — 일시 오류면 1로 돌아가 컨테이너 재생성
    3. POST /me/threads_publish (creation_id)
    Returns: Threads media ID
    """
    if not settings.threads_access_token:
        raise SocialPostError("THREADS_ACCESS_TOKEN 미설정")

    creation_id = _container_with_retry(
        lambda: str(
            _post(
                f"{THREADS_GRAPH}/me/threads",
                {
                    "media_type": "VIDEO",
                    "video_url": video_url,
                    "text": text[:500],  # Threads 본문 한도
                    "access_token": settings.threads_access_token,
                },
            )["id"]
        ),
        _wait_for_threads_container,
        "threads",
    )

    # publish는 재시도하지 않는다 — 스레드가 게시한 뒤 응답만 유실되면 중복 게시된다.
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
