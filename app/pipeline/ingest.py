"""YouTube 미드폼 수집 (yt-dlp) + SQLite 메타 저장."""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.storage.db import (
    get_connection,
    get_video_by_youtube_id,
    upsert_video,
)
from app.storage.models import Video
from app.utils.logger import get_logger

log = get_logger(__name__)

_YOUTUBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_PATTERNS = [
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]{11})"),
]
# 영빈 내부 영상 ID: YY-{B|P}NNN (예: 26-B001, 26-P003).
# YouTube description 맨 하단에 영빈이 직접 적어둠.
_INTERNAL_ID_PATTERN = re.compile(r"\b(\d{2}-[BP]\d{3})\b")


def _extract_internal_id(description: str) -> str | None:
    """영상 description에서 내부 ID(YY-B|P NNN) 추출. 없으면 None."""
    if not description:
        return None
    match = _INTERNAL_ID_PATTERN.search(description)
    return match.group(1) if match else None


def _extract_youtube_id(s: str) -> str:
    """URL 또는 ID에서 youtube_id 추출."""
    if not s:
        raise ValueError("Empty input")
    s = s.strip()
    for pattern in _URL_PATTERNS:
        match = pattern.search(s)
        if match:
            return match.group(1)
    if _YOUTUBE_ID_PATTERN.fullmatch(s):
        return s
    raise ValueError(f"Cannot extract YouTube ID from: {s}")


def _ytdlp_cookies_args() -> list[str]:
    """settings.yt_dlp_browser_cookies 설정 시 --cookies-from-browser 옵션 반환."""
    browser = (settings.yt_dlp_browser_cookies or "").strip()
    return ["--cookies-from-browser", browser] if browser else []


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(subprocess.CalledProcessError),
)
def _fetch_metadata(youtube_id: str) -> dict[str, Any]:
    """yt-dlp --dump-json 으로 메타 추출."""
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    cmd = [
        "yt-dlp", "--no-playlist", "--skip-download",
        *_ytdlp_cookies_args(),
        # n challenge solver(deno/EJS) 없어도 metadata는 추출. format URL 불필요
        # (다운로드는 별도 _download에서 처리).
        "--ignore-no-formats-error",
        "--dump-json", "--", url,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True,
        encoding="utf-8", timeout=300,
    )
    return json.loads(result.stdout)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(subprocess.CalledProcessError),
)
def _download(youtube_id: str, output: Path) -> None:
    """yt-dlp로 최대 4K mp4 + m4a 다운로드 + merge.

    영빈 결정 2026-07-09: 풀스크린 9:16 crop 화질 확보를 위해 4K 우선
    (1080p 소스는 crop 후 2배 업스케일로 화질 열화). YouTube 4K는 AV1(mp4)/VP9만
    제공되므로 mp4 우선 → 없으면 코덱 무관 최고 화질 → 1080p fallback.
    """
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        *_ytdlp_cookies_args(),
        "-f",
        "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=2160]+bestaudio"
        "/best[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", str(output),
        "--", url,
    ]
    log.info("ingest.download_start", youtube_id=youtube_id)
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=1800,
    )
    if result.returncode != 0:
        log.error("ingest.download_failed", stderr=result.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr,
        )


def _yt_upload_date_to_iso(upload_date: str | None) -> str | None:
    """yt-dlp의 'upload_date' (YYYYMMDD) → ISO (YYYY-MM-DD)."""
    if not upload_date or len(upload_date) != 8:
        return None
    try:
        return datetime.strptime(upload_date, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _detect_channel_safe(youtube_id: str) -> str:
    """YouTube Data API로 channel 자동 감지. 실패 시 'ko' fallback (legacy 호환)."""
    try:
        from app.integrations.youtube import detect_channel
        return detect_channel(youtube_id)
    except Exception as e:
        log.warning("ingest.detect_channel_failed", youtube_id=youtube_id, error=str(e))
        return "ko"


def _next_en_internal_id(conn: Any) -> str:
    """EN 채널 자동 internal_id 부여. videos.channel='en' 중 max 26-E### + 1.

    영빈이 한국 채널 영상엔 description에 26-B###/26-P###를 직접 적지만 영어 채널은
    자동 흐름이라 SQLite가 순차 부여 (예: 26-E001, 26-E002, ...). 연도 prefix는 한국
    채널과 동일하게 '26'으로.
    """
    rows = conn.execute(
        "SELECT internal_id FROM videos "
        "WHERE channel = 'en' AND internal_id LIKE '26-E%'"
    ).fetchall()
    used: set[int] = set()
    for r in rows:
        iid = r["internal_id"] or ""
        suffix = iid[len("26-E"):]
        if suffix.isdigit():
            used.add(int(suffix))
    n = 1
    while n in used:
        n += 1
    return f"26-E{n:03d}"


def ingest(youtube_id_or_url: str) -> Video:
    """YouTube URL/ID로 영상 다운로드 + SQLite 등록.

    Cache: data/samples/<id>.mp4 + SQLite row 둘 다 존재 시 skip.
    channel은 YouTube API videos.snippet.channelId로 자동 감지 (ko/en).
    """
    youtube_id = _extract_youtube_id(youtube_id_or_url)
    local_path = settings.samples_dir / f"{youtube_id}.mp4"

    conn = get_connection()
    try:
        existing = get_video_by_youtube_id(conn, youtube_id)
        if existing is not None and local_path.exists():
            # EN 채널인데 internal_id 누락(legacy 행) → 자동 부여 + 반영.
            if existing.channel == "en" and not existing.internal_id:
                new_iid = _next_en_internal_id(conn)
                conn.execute(
                    "UPDATE videos SET internal_id = ? WHERE youtube_id = ?",
                    (new_iid, youtube_id),
                )
                conn.commit()
                log.info(
                    "ingest.en_internal_id_assigned_legacy",
                    youtube_id=youtube_id, internal_id=new_iid,
                )
                existing = get_video_by_youtube_id(conn, youtube_id)
            log.info(
                "ingest.cache_hit",
                youtube_id=youtube_id,
                channel=existing.channel if existing else "ko",
                internal_id=existing.internal_id if existing else None,
                path=str(local_path),
            )
            return existing

        channel = _detect_channel_safe(youtube_id)
        log.info("ingest.channel_detected", youtube_id=youtube_id, channel=channel)

        log.info("ingest.fetch_metadata", youtube_id=youtube_id)
        meta = _fetch_metadata(youtube_id)
        title = str(meta.get("title") or "")
        duration = int(meta.get("duration") or 0)
        published_at = _yt_upload_date_to_iso(meta.get("upload_date"))
        description = str(meta.get("description") or "")
        internal_id = _extract_internal_id(description)
        if internal_id is None:
            if channel == "en":
                internal_id = _next_en_internal_id(conn)
                log.info(
                    "ingest.en_internal_id_auto_assigned",
                    youtube_id=youtube_id, internal_id=internal_id,
                )
            else:
                log.warning(
                    "ingest.internal_id_missing",
                    youtube_id=youtube_id,
                    hint="description 맨 하단에 'YY-B001' 같은 ID 추가 필요",
                )

        if not local_path.exists():
            _download(youtube_id, local_path)

        upsert_video(
            conn,
            youtube_id=youtube_id,
            title=title,
            duration=duration,
            published_at=published_at,
            internal_id=internal_id,
            channel=channel,
        )
        log.info(
            "ingest.done", youtube_id=youtube_id, channel=channel,
            title=title, duration=duration, internal_id=internal_id,
            path=str(local_path),
        )
        result = get_video_by_youtube_id(conn, youtube_id)
        if result is None:
            raise RuntimeError(
                f"Failed to retrieve video after upsert: {youtube_id}"
            )
        return result
    finally:
        conn.close()
