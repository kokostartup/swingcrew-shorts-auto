# Skill: youtube-api

## When to Use

- `app/integrations/youtube.py` — YouTube Data/Analytics 클라이언트
- `app/pipeline/retention.py` — 잔존율 fetch
- `app/pipeline/publish.py` — Shorts 업로드 + 고정 댓글
- `app/pipeline/ingest.py` — 신규 미드폼 감지
- `api-integrator` 서브에이전트 호출 시 함께 전달

## How It Works

YouTube API 사용 원칙:
1. **OAuth refresh token 영구 저장** — `data/youtube_token.json` (gitignored)
2. **Quota 관리** — 일일 10,000 units. 업로드=1600, search=100
3. **Tenacity retry** — `HttpError` 시 exponential backoff
4. **Batch when possible** — Analytics는 다중 video 한 번에

## Pattern: OAuth Token Lifecycle

```python
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

TOKEN_PATH = Path("data/youtube_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # 댓글
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

def get_credentials() -> Credentials:
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(
                {"installed": {
                    "client_id": settings.youtube_oauth_client_id,
                    "client_secret": settings.youtube_oauth_client_secret,
                    "redirect_uris": ["http://localhost"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }},
                SCOPES,
            )
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
    return creds
```

## Pattern: 잔존율 (Retention) 가져오기

```python
from googleapiclient.discovery import build

def fetch_retention(video_id: str, published_at: datetime) -> dict | None:
    """발행 7일+ 영상에 대해 잔존율 곡선 fetch."""
    if (datetime.utcnow() - published_at).days < 7:
        return None  # cold start

    analytics = build("youtubeAnalytics", "v2", credentials=get_credentials())
    resp = analytics.reports().query(
        ids=f"channel=={settings.youtube_channel_id}",
        startDate=published_at.strftime("%Y-%m-%d"),
        endDate=datetime.utcnow().strftime("%Y-%m-%d"),
        metrics="audienceWatchRatio,relativeRetentionPerformance",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={video_id}",
        sort="elapsedVideoTimeRatio",
    ).execute()
    return resp  # 100개 시점 (0.00~1.00) × 2 metric
```

## Pattern: Shorts 업로드 + 고정 댓글

```python
from googleapiclient.http import MediaFileUpload
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
def upload_short(video_path: Path, title: str, description: str,
                 source_video_id: str) -> str:
    youtube = build("youtube", "v3", credentials=get_credentials())

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["골프", "스윙", "SwingCrew", "shorts"],
            "categoryId": "17",  # Sports
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                            resumable=True, chunksize=4*1024*1024)

    request = youtube.videos().insert(part="snippet,status",
                                       body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()

    short_id = response["id"]
    pin_source_comment(youtube, short_id, source_video_id)
    return short_id

def pin_source_comment(youtube, short_id: str, source_video_id: str) -> None:
    """미드폼 링크를 숏츠에 고정 댓글로 추가."""
    source_url = f"https://youtu.be/{source_video_id}"
    youtube.commentThreads().insert(
        part="snippet",
        body={"snippet": {
            "videoId": short_id,
            "topLevelComment": {"snippet": {
                "textOriginal": f"전체 영상은 여기 ▶ {source_url}",
            }},
        }},
    ).execute()
```

## Pattern: Quota 모니터링

```python
QUOTA_COSTS = {
    "videos.insert": 1600,
    "videos.list": 1,
    "search.list": 100,
    "commentThreads.insert": 50,
    "playlistItems.list": 1,
}

def estimate_daily_quota(plan: list[str]) -> int:
    return sum(QUOTA_COSTS.get(op, 1) for op in plan)
```

매일 한도: 10,000 units → 숏츠 6개 업로드 (1600×6=9600) + 약간의 list/comment.

## Pattern: 신규 영상 감지 (Ingest)

```python
def list_new_midform(since: datetime) -> list[dict]:
    """채널 신규 영상 중 8분+ (미드폼) 반환."""
    youtube = build("youtube", "v3", credentials=get_credentials())
    search = youtube.search().list(
        part="id", channelId=settings.youtube_channel_id,
        type="video", order="date", maxResults=50,
        publishedAfter=since.isoformat() + "Z",
    ).execute()
    ids = [item["id"]["videoId"] for item in search["items"]]
    if not ids:
        return []
    details = youtube.videos().list(
        part="contentDetails,snippet", id=",".join(ids),
    ).execute()
    return [v for v in details["items"]
            if parse_iso_duration(v["contentDetails"]["duration"]) >= 480]
```

## Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| 401 Unauthorized | refresh token 만료 (≥6개월 미사용) | OAuth flow 재실행 |
| 403 quotaExceeded | 일일 한도 초과 | 다음날까지 대기, batch 최적화 |
| Comment 작성 401 | scope `force-ssl` 누락 | SCOPES 재확인 + 토큰 재발급 |
| 업로드 stuck | 네트워크 단절 | `resumable=True` + chunk_size 명시 |
| 미드폼 링크 댓글 안 달림 | 비디오 인덱싱 지연 | 업로드 후 30~60초 대기 |
| Analytics 빈 응답 | 발행 7일 미만 | `fetch_retention` cold start guard |

## Reference

- API docs: https://developers.google.com/youtube/v3
- Analytics: https://developers.google.com/youtube/analytics
- Quota calculator: https://developers.google.com/youtube/v3/determine_quota_cost
