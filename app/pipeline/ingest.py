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
    """yt-dlp로 1080p mp4 + m4a 다운로드 + merge."""
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
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


def ingest(youtube_id_or_url: str) -> Video:
    """YouTube URL/ID로 영상 다운로드 + SQLite 등록.

    Cache: data/samples/<id>.mp4 + SQLite row 둘 다 존재 시 skip.
    """
    youtube_id = _extract_youtube_id(youtube_id_or_url)
    local_path = settings.samples_dir / f"{youtube_id}.mp4"

    conn = get_connection()
    try:
        existing = get_video_by_youtube_id(conn, youtube_id)
        if existing is not None and local_path.exists():
            log.info(
                "ingest.cache_hit",
                youtube_id=youtube_id, path=str(local_path),
            )
            return existing

        log.info("ingest.fetch_metadata", youtube_id=youtube_id)
        meta = _fetch_metadata(youtube_id)
        title = str(meta.get("title") or "")
        duration = int(meta.get("duration") or 0)
        published_at = _yt_upload_date_to_iso(meta.get("upload_date"))
        description = str(meta.get("description") or "")
        internal_id = _extract_internal_id(description)
        if internal_id is None:
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
        )
        log.info(
            "ingest.done", youtube_id=youtube_id,
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
