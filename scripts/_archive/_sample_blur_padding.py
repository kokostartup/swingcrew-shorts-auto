"""1회용: 26-P020-S06으로 blur padding 샘플 생성.

목적:
  현재 wide letterbox (검정 바)를 blur padding (소스 zoom+blur 배경)으로 교체했을 때
  영빈이 시각적으로 비교 검토하도록 별도 mp4 생성.

출력: outputs/shorts/26-P020-S06_blur_sample.mp4 (기존 scheduled mp4와 별개)

흐름:
  1. SQLite에서 moment 정보 fetch
  2. 시그니처 ASS 파일 생성 (기존 파이프라인 재사용)
  3. blur padding filter_complex 직접 구성 후 ffmpeg
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.pipeline.template import (
    CANVAS_W,
    VIDEO_H,
    signature_filter_segment_ass,
    write_signature_ass,
)
from app.utils.video import detect_hwaccel

INTERNAL_ID = "26-P020-S06"
OUTPUT = Path("outputs/shorts/26-P020-S06_blur_sample.mp4")


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT s.*, v.youtube_id FROM shorts s "
        "JOIN videos v ON v.id = s.source_video_id "
        "WHERE s.internal_id = ?",
        (INTERNAL_ID,),
    ).fetchone()
    conn.close()
    if not r:
        print(f"no moment: {INTERNAL_ID}")
        return 1

    src = settings.samples_dir / f"{r['youtube_id']}.mp4"
    if not src.exists():
        print(f"source mp4 not found: {src}")
        return 1

    start = float(r["start_time"])
    end = float(r["end_time"])
    copy1 = r["copy1"] or ""
    copy2 = r["copy2"] or ""

    print(f"=== {INTERNAL_ID} blur padding sample ===")
    print(f"src: {src}")
    print(f"range: {start:.2f} ~ {end:.2f} ({end - start:.2f}s)")
    print(f"copy1: {copy1}")
    print(f"copy2: {copy2}")
    print(f"output: {OUTPUT}")
    print()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # 시그니처 ASS 파일 (기존 파이프라인과 동일).
    ass_path = OUTPUT.with_suffix(".sig.ass")
    write_signature_ass(copy1, copy2, ass_path)

    # blur padding filter_complex:
    #   1) [0:v] split 2 → 전경 + 배경
    #   2) 전경: 좌우 약간 crop (16:9 → 3:2) + 1080 wide scale (~720h)
    #   3) 배경: 1080×1350 채우게 zoom + blur
    #   4) 배경에 전경 중앙 overlay
    #   5) 시그니처 자막 ASS subtitles
    fg_chain = f"[fg]crop=ih*1.5:ih:(iw-ih*1.5)/2:0,scale={CANVAS_W}:-2[fgs]"
    bg_chain = (
        "[bg]scale=1080:1350:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H},"
        "boxblur=40:2[bgblur]"
    )
    reframe = (
        "[0:v]split=2[fg][bg];"
        f"{fg_chain};"
        f"{bg_chain};"
        "[bgblur][fgs]overlay=0:(H-h)/2,setsar=1[reframed];"
    )
    signature = signature_filter_segment_ass("[reframed]", "[out]", ass_path)
    filter_complex = reframe + signature

    audio_pan = "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"

    hwaccel = detect_hwaccel()
    input_decoder = ["-hwaccel", "cuda"] if hwaccel == "cuda" else []
    if settings.use_nvenc and hwaccel == "cuda":
        encoder = ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "5M"]
    else:
        encoder = ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *input_decoder,
        "-i",
        str(src),
        "-ss",
        str(start),
        "-to",
        str(end),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-map",
        "0:a?",
        "-af",
        audio_pan,
        *encoder,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-r",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(OUTPUT),
    ]

    print(f"ffmpeg encoder={encoder[1]} hwaccel={hwaccel}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ffmpeg FAILED:")
        print(result.stderr)
        return 1

    if ass_path.exists():
        try:
            ass_path.unlink()
        except OSError:
            pass

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\nDONE: {OUTPUT} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
