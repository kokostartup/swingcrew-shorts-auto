"""YouTube Data + Analytics API 클라이언트.

OAuth lifecycle: 첫 실행 브라우저 인증 → refresh token 저장 → 자동 갱신.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config import settings
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

log = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]

# channel별 credentials 캐시. ko/en 동시 사용 가능 (예: ingest는 ko로 detect 호출,
# upload는 영상 channel로).
_credentials_cache: dict[str, Credentials] = {}


def _client_config() -> dict[str, Any]:
    if not settings.youtube_oauth_client_id or not settings.youtube_oauth_client_secret:
        raise RuntimeError(
            "YOUTUBE_OAUTH_CLIENT_ID / SECRET 미설정. .env 확인."
        )
    return {
        "installed": {
            "client_id": settings.youtube_oauth_client_id,
            "client_secret": settings.youtube_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _token_path_for(channel: str) -> Any:
    if channel == "en":
        return settings.youtube_token_path_en
    return settings.youtube_token_path


def get_credentials(channel: str = "ko") -> Credentials:
    """OAuth credentials 획득. 첫 호출 시 브라우저 인증, 이후 캐시 + refresh.

    channel별로 별도 token 파일 + 캐시. 첫 영어 채널 호출 시 브라우저 인증 1회 필요
    (영어 채널 Google 계정으로 로그인).
    """
    cached = _credentials_cache.get(channel)
    if cached is not None and cached.valid:
        return cached

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = _token_path_for(channel)
    creds: OAuthCredentials | None = None

    if token_path.exists():
        try:
            creds = OAuthCredentials.from_authorized_user_file(
                str(token_path), SCOPES,
            )
        except Exception as e:
            log.warning("youtube.oauth.token_load_failed", channel=channel, error=str(e))
            creds = None

    if creds and creds.valid:
        _credentials_cache[channel] = creds
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            log.info("youtube.oauth.token_refreshed", channel=channel)
            _credentials_cache[channel] = creds
            return creds
        except Exception as e:
            log.warning("youtube.oauth.refresh_failed", channel=channel, error=str(e))
            creds = None

    log.info(
        "youtube.oauth.browser_required",
        channel=channel,
        message=f"[{channel}] 브라우저 인증 필요. 5분 안에 Google 로그인 + 권한 부여 완료.",
    )
    flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    log.info("youtube.oauth.token_saved", channel=channel, path=str(token_path))
    _credentials_cache[channel] = creds
    return creds


def build_data_client(channel: str = "ko") -> Any:
    """YouTube Data API v3 client (videos.list, search.list 등)."""
    from googleapiclient.discovery import build

    return build(
        "youtube", "v3",
        credentials=get_credentials(channel), cache_discovery=False,
    )


def build_analytics_client(channel: str = "ko") -> Any:
    """YouTube Analytics API v2 client (audienceWatchRatio 등)."""
    from googleapiclient.discovery import build

    return build(
        "youtubeAnalytics", "v2",
        credentials=get_credentials(channel), cache_discovery=False,
    )


def detect_channel(video_id: str) -> str:
    """YouTube video_id → channel 'ko'/'en'. youtube_api_key (public read)로 호출.

    config.youtube_channel_id (한국) / youtube_channel_id_en (영어) 매핑 기반.
    매칭 안 되면 ValueError.
    """
    if not settings.youtube_api_key:
        raise RuntimeError("YOUTUBE_API_KEY 미설정 — channel 자동 감지 불가.")
    from googleapiclient.discovery import build

    client = build("youtube", "v3", developerKey=settings.youtube_api_key, cache_discovery=False)
    resp = client.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"YouTube video {video_id} not found")
    cid = items[0]["snippet"]["channelId"]
    if cid == settings.youtube_channel_id:
        return "ko"
    if cid == settings.youtube_channel_id_en:
        return "en"
    raise ValueError(
        f"video {video_id} belongs to channel {cid} — not in ko/en mapping. "
        f"config의 youtube_channel_id (ko) / youtube_channel_id_en (en) 확인."
    )


class YouTubeUploadError(RuntimeError):
    """YouTube 영상 업로드 실패."""


def upload_short(
    *,
    video_path: Any,
    title: str,
    description: str,
    tags: list[str] | None = None,
    publish_at_utc: str,
    category_id: str = "17",  # Sports
    channel: str = "ko",
) -> str:
    """YouTube에 영상을 private으로 업로드 + publishAt 예약 → video_id 반환.

    publish_at_utc: ISO 8601 UTC (예: "2026-05-15T09:00:00Z"). publishAt 시간에
    YouTube가 자동 public 전환.
    channel: 'ko' (한국) / 'en' (영어). channel별 OAuth credentials로 업로드.
    """
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    client = build_data_client(channel)
    body = {
        "snippet": {
            "title": title[:100],  # YouTube 제목 100자 한도
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_utc,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4", resumable=True,
    )
    request = client.videos().insert(
        part="snippet,status", body=body, media_body=media,
    )
    log.info(
        "youtube.upload_start",
        title=title[:50], publish_at=publish_at_utc,
    )
    try:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("youtube.upload_progress", progress=int(status.progress() * 100))
    except HttpError as e:
        raise YouTubeUploadError(f"videos.insert failed: {e}") from e
    video_id = str(response["id"])
    log.info("youtube.upload_done", video_id=video_id)
    return video_id


def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def delete_video(video_id: str, channel: str = "ko") -> bool:
    """YouTube video 삭제 (private/scheduled 영상 교체 시 사용).

    Returns: True if deleted, False if not found (404). 다른 에러는 raise.
    """
    from googleapiclient.errors import HttpError

    client = build_data_client(channel)
    try:
        client.videos().delete(id=video_id).execute()
        log.info("youtube.delete_done", video_id=video_id)
        return True
    except HttpError as e:
        if getattr(e, "resp", None) is not None and e.resp.status == 404:
            log.info("youtube.delete_not_found", video_id=video_id)
            return False
        raise


def list_channel_uploads(
    channel_id: str, max_results: int = 50, channel: str = "ko",
) -> list[dict[str, Any]]:
    """채널 uploads playlist에서 최신 N개 영상 메타.

    반환: [{video_id, title, description, published_at}, ...]
    description은 일부만 (snippet에서 잘림 가능). 정확한 ID 추출은 ingest에서 다시.
    """
    client = build_data_client(channel)
    ch_resp = client.channels().list(
        part="contentDetails", id=channel_id,
    ).execute()
    items = ch_resp.get("items", [])
    if not items:
        return []
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    results: list[dict[str, Any]] = []
    next_page: str | None = None
    while len(results) < max_results:
        params: dict[str, Any] = {
            "part": "snippet",
            "playlistId": uploads_id,
            "maxResults": min(50, max_results - len(results)),
        }
        if next_page:
            params["pageToken"] = next_page
        resp = client.playlistItems().list(**params).execute()
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            results.append({
                "video_id": sn.get("resourceId", {}).get("videoId"),
                "title": sn.get("title"),
                "description": sn.get("description") or "",
                "published_at": sn.get("publishedAt"),
            })
        next_page = resp.get("nextPageToken")
        if not next_page:
            break
    log.info(
        "youtube.uploads_listed",
        channel_id=channel_id, count=len(results),
    )
    return results


__all__ = [
    "YouTubeUploadError",
    "build_analytics_client",
    "build_data_client",
    "delete_video",
    "detect_channel",
    "get_credentials",
    "list_channel_uploads",
    "upload_short",
    "video_url",
]
