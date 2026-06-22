"""TikTok Content Posting API — OAuth + video upload.

흐름:
  1. OAuth 2.0 (initial 인증 1회): scripts/tiktok_auth.py로 영빈 권한 부여
     → access_token + refresh_token + open_id 저장 (data/tiktok_token.json)
  2. 이후 호출: cached token 사용, expired 시 자동 refresh
  3. Video upload (PULL_FROM_URL): R2-hosted mp4 URL → inbox or direct post

API endpoints (open.tiktokapis.com):
  - POST /v2/oauth/token/                            토큰 교환/갱신
  - POST /v2/post/publish/inbox/video/init/          inbox (video.upload)
  - POST /v2/post/publish/video/init/                direct post (video.publish)
  - GET  /v2/post/publish/status/fetch/              상태 polling
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

API_BASE = "https://open.tiktokapis.com"
AUTH_BASE = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_PATH = Path("data/tiktok_token.json")
DEFAULT_REDIRECT_URI = (
    "https://kokostartup.github.io/swingcrew-shorts-auto/oauth-callback.html"
)
DEFAULT_SCOPES = "user.info.basic,video.upload,video.publish"


class TikTokAPIError(RuntimeError):
    """TikTok API 호출 실패."""


def _load_token() -> dict[str, Any] | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_token(data: dict[str, Any]) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("tiktok.token_saved", path=str(TOKEN_PATH))


def build_authorize_url(state: str = "swingcrew_oauth") -> str:
    """OAuth authorize URL 생성. 브라우저에서 이거 열면 영빈 로그인 + 권한 부여."""
    if not settings.tiktok_client_key:
        raise TikTokAPIError("TIKTOK_CLIENT_KEY 미설정 (.env)")
    params = {
        "client_key": settings.tiktok_client_key,
        "scope": DEFAULT_SCOPES,
        "response_type": "code",
        "redirect_uri": DEFAULT_REDIRECT_URI,
        "state": state,
    }
    qs = "&".join(f"{k}={httpx.QueryParams({k: v}).get(k)}" for k, v in params.items())
    return f"{AUTH_BASE}?{qs}"


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """authorization code → access_token. 1회 인증 후 호출."""
    if not settings.tiktok_client_key or not settings.tiktok_client_secret:
        raise TikTokAPIError("TIKTOK_CLIENT_KEY/SECRET 미설정")
    r = httpx.post(
        f"{API_BASE}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": DEFAULT_REDIRECT_URI,
        },
        timeout=30,
    )
    if r.status_code >= 400:
        raise TikTokAPIError(f"token exchange {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("error"):
        raise TikTokAPIError(f"token exchange error: {data}")
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 0)))
    ).isoformat()
    token_data = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "open_id": data["open_id"],
        "expires_at": expires_at,
        "scope": data.get("scope", ""),
    }
    _save_token(token_data)
    return token_data


def _refresh_token(refresh_token: str) -> dict[str, Any]:
    r = httpx.post(
        f"{API_BASE}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if r.status_code >= 400:
        raise TikTokAPIError(f"refresh {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("error"):
        raise TikTokAPIError(f"refresh error: {data}")
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 0)))
    ).isoformat()
    new_data = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "open_id": data.get("open_id", ""),
        "expires_at": expires_at,
        "scope": data.get("scope", ""),
    }
    _save_token(new_data)
    return new_data


def get_access_token() -> str:
    """현재 valid access_token 반환. 만료됐으면 자동 refresh."""
    token = _load_token()
    if not token:
        raise TikTokAPIError(
            "TikTok 토큰 없음. scripts/tiktok_auth.py로 인증 먼저 진행.",
        )
    try:
        expires_at = datetime.fromisoformat(token["expires_at"])
    except Exception:
        expires_at = datetime.now(UTC)
    # 5분 buffer 두고 refresh
    if datetime.now(UTC) + timedelta(minutes=5) >= expires_at:
        log.info("tiktok.token_refreshing")
        token = _refresh_token(token["refresh_token"])
    return token["access_token"]


def upload_to_inbox(video_url: str) -> str:
    """video.upload (Inbox): R2 URL의 mp4를 영빈 TikTok inbox에 보냄.

    영빈이 TikTok 앱 inbox에서 영상 확인 후 manual publish.
    audit 전 sandbox에서 가장 안전한 흐름.

    Returns: publish_id (status polling용)
    """
    token = get_access_token()
    r = httpx.post(
        f"{API_BASE}/v2/post/publish/inbox/video/init/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        },
        timeout=60,
    )
    if r.status_code >= 400:
        raise TikTokAPIError(f"inbox init {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("error", {}).get("code") not in (None, "ok"):
        raise TikTokAPIError(f"inbox init error: {data}")
    publish_id = data["data"]["publish_id"]
    log.info("tiktok.inbox_init", publish_id=publish_id)
    return publish_id


def upload_to_inbox_file(local_path: Path) -> str:
    """video.upload (Inbox) + FILE_UPLOAD: 로컬 mp4를 TikTok에 chunk upload.

    R2 domain ownership verify 안 됐을 때 PULL_FROM_URL 대신 사용.
    영빈 영상 (~10-20MB)은 단일 chunk로 upload.

    Returns: publish_id
    """
    if not local_path.exists():
        raise TikTokAPIError(f"local file missing: {local_path}")
    video_size = local_path.stat().st_size
    if video_size > 64 * 1024 * 1024:
        raise TikTokAPIError(
            f"파일 {video_size} bytes — 64MB 초과는 multi-chunk 필요 (미구현)",
        )
    # 5MB 미만 또는 64MB 이하 → 단일 chunk
    chunk_size = video_size
    total_chunk_count = 1

    token = get_access_token()
    # 1. Init
    r = httpx.post(
        f"{API_BASE}/v2/post/publish/inbox/video/init/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        },
        timeout=60,
    )
    if r.status_code >= 400:
        raise TikTokAPIError(f"inbox file init {r.status_code}: {r.text[:300]}")
    data = r.json()
    publish_id = data["data"]["publish_id"]
    upload_url = data["data"]["upload_url"]
    log.info(
        "tiktok.file_init",
        publish_id=publish_id, video_size=video_size, chunks=total_chunk_count,
    )

    # 2. PUT chunk (single)
    with local_path.open("rb") as f:
        body = f.read()
    put_r = httpx.put(
        upload_url,
        content=body,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        },
        timeout=300,
    )
    if put_r.status_code >= 400:
        raise TikTokAPIError(f"chunk upload {put_r.status_code}: {put_r.text[:300]}")
    log.info("tiktok.chunk_uploaded", publish_id=publish_id, size=video_size)
    return publish_id


def direct_post_file(
    local_path: Path,
    caption: str,
    privacy_level: str = "SELF_ONLY",
    disable_comment: bool = False,
    disable_duet: bool = False,
    disable_stitch: bool = False,
) -> str:
    """video.publish (Direct Post) + FILE_UPLOAD: 로컬 mp4 + caption → 즉시 게시.

    Sandbox: privacy_level은 SELF_ONLY 강제 (PUBLIC_TO_EVERYONE은 audit 후).
    audit 통과 후엔 PUBLIC_TO_EVERYONE으로 변경하면 정상 audience 게시.

    Returns: publish_id
    """
    if not local_path.exists():
        raise TikTokAPIError(f"local file missing: {local_path}")
    video_size = local_path.stat().st_size
    if video_size > 64 * 1024 * 1024:
        raise TikTokAPIError(
            f"파일 {video_size} bytes — 64MB 초과는 multi-chunk 필요 (미구현)",
        )
    chunk_size = video_size
    total_chunk_count = 1

    token = get_access_token()
    r = httpx.post(
        f"{API_BASE}/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": caption[:2200],
                "privacy_level": privacy_level,
                "disable_comment": disable_comment,
                "disable_duet": disable_duet,
                "disable_stitch": disable_stitch,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        },
        timeout=60,
    )
    if r.status_code >= 400:
        raise TikTokAPIError(f"direct post init {r.status_code}: {r.text[:300]}")
    data = r.json()
    publish_id = data["data"]["publish_id"]
    upload_url = data["data"]["upload_url"]
    log.info(
        "tiktok.direct_init",
        publish_id=publish_id, video_size=video_size, privacy=privacy_level,
    )

    with local_path.open("rb") as f:
        body = f.read()
    put_r = httpx.put(
        upload_url,
        content=body,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        },
        timeout=300,
    )
    if put_r.status_code >= 400:
        raise TikTokAPIError(f"chunk upload {put_r.status_code}: {put_r.text[:300]}")
    log.info("tiktok.direct_uploaded", publish_id=publish_id, size=video_size)
    return publish_id


def fetch_publish_status(publish_id: str) -> dict[str, Any]:
    """publish_id의 처리 상태 조회. status: PROCESSING_UPLOAD, SEND_TO_USER_INBOX, ..."""
    token = get_access_token()
    r = httpx.post(
        f"{API_BASE}/v2/post/publish/status/fetch/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={"publish_id": publish_id},
        timeout=30,
    )
    if r.status_code >= 400:
        raise TikTokAPIError(f"status {r.status_code}: {r.text[:300]}")
    return r.json().get("data", {})


def wait_for_completion(publish_id: str, max_attempts: int = 60) -> dict[str, Any]:
    """Status가 terminal state까지 polling. SUCCEEDED/FAILED/EXPIRED 등.

    Inbox: SEND_TO_USER_INBOX = success
    Direct: PUBLISH_COMPLETE = success
    """
    terminal = {
        "SEND_TO_USER_INBOX", "PUBLISH_COMPLETE",  # success
        "FAILED", "EXPIRED",  # failure
    }
    for i in range(max_attempts):
        data = fetch_publish_status(publish_id)
        status = data.get("status", "")
        log.info("tiktok.status_poll", attempt=i + 1, status=status)
        if status in terminal:
            return data
        time.sleep(5)
    raise TikTokAPIError(f"polling timeout ({max_attempts * 5}s) publish_id={publish_id}")


__all__ = [
    "TikTokAPIError",
    "build_authorize_url",
    "exchange_code_for_token",
    "fetch_publish_status",
    "get_access_token",
    "direct_post_file",
    "upload_to_inbox",
    "upload_to_inbox_file",
    "wait_for_completion",
]
