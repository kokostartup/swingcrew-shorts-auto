"""ffprobe 메타 조회 + 출력 검증 + 하드웨어 가속 감지."""
import json
import subprocess
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

log = get_logger(__name__)


def ffprobe_meta(path: Path) -> dict[str, Any]:
    """영상 메타데이터를 JSON으로 가져옴."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def assert_video_meta(
    path: Path,
    *,
    expected_dur: float,
    tolerance: float = 0.5,
    size_mb_per_90s: float = 30.0,
) -> None:
    """1080×1920 / H.264+AAC / 30fps / duration·size 검증.

    size_mb_per_90s: 90초 기준 최대 MB. 짧은 클립도 mp4 헤더 비용을 위해 최소 3MB 허용.
    움직임 많은 골프 스윙 클립은 crf22 + 30fps 기준 ~28 MB/90s까지 정상 — 30으로 여유.
    """
    meta = ffprobe_meta(path)
    video = next((s for s in meta["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in meta["streams"] if s["codec_type"] == "audio"), None)

    if video is None:
        raise AssertionError(f"No video stream: {path}")
    if video["width"] != 1080:
        raise AssertionError(f"width={video['width']}, expected 1080")
    if video["height"] != 1920:
        raise AssertionError(f"height={video['height']}, expected 1920")
    if video["codec_name"] != "h264":
        raise AssertionError(f"codec={video['codec_name']}, expected h264")

    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    fps = num / den if den else 0
    if abs(fps - 30) >= 0.1:
        raise AssertionError(f"fps={fps:.3f}, expected 30")

    duration = float(meta["format"]["duration"])
    if abs(duration - expected_dur) > tolerance:
        raise AssertionError(
            f"duration={duration:.2f}s, expected {expected_dur}±{tolerance}s"
        )

    size_mb = int(meta["format"]["size"]) / (1024 * 1024)
    max_size_mb = max(size_mb_per_90s * expected_dur / 90, 3.0)
    if size_mb > max_size_mb:
        raise AssertionError(
            f"size={size_mb:.2f}MB > {max_size_mb:.2f}MB (dur {expected_dur}s)"
        )

    if audio is not None and audio["codec_name"] != "aac":
        raise AssertionError(f"audio codec={audio['codec_name']}, expected aac")


def probe_dimensions(path: Path) -> tuple[int, int]:
    """ffprobe로 영상 해상도 (width, height) 반환."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def detect_hwaccel() -> str:
    """ffmpeg에서 사용 가능한 하드웨어 가속 감지. cuda / videotoolbox / none."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-hwaccels"],
        capture_output=True, text=True, check=True,
    )
    available = result.stdout.lower()
    if "cuda" in available:
        return "cuda"
    if "videotoolbox" in available:
        return "videotoolbox"
    return "none"
