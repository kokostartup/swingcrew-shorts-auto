"""1회용: P021 압축 prototype — 옵션 A 풀스크린 crop 버전.

blur padding 대신 _letterbox_4_5 패턴 (scale increase + center crop) 적용 →
영상이 1080×1350 영역 풀로 채움 (좌우 약 28%씩 잘림). 시뮬레이터 화면 일부 + 인물 잘 보임.

segments는 동일 (curator 설계 그대로):
  A (29.82~39.35) HOOK / B (61.57~100.26) INSIGHT+DEMO
  C (609.67~630.58) DRILL / D (707.47~718.47) RESULT
총 80.1초.

출력: outputs/shorts/26-P021-compressed_crop_sample.mp4 (별도 파일, blur 버전과 비교)
"""

from __future__ import annotations

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

SRC = settings.samples_dir / "mAFLAosow9M.mp4"
OUTPUT = Path("outputs/shorts/26-P021-compressed_crop_sample.mp4")
XFADE_DUR = 0.3
SEGMENTS = [
    (29.82, 39.35),
    (61.57, 100.26),
    (609.67, 630.58),
    (707.47, 718.47),
]
COPY1 = "백스윙만 바꿨더니"
COPY2 = "볼스피드 78.8 찍었습니다"


def _seg_filter(idx: int, start: float, end: float) -> str:
    """단일 segment 풀스크린 crop chain (_letterbox_4_5 패턴)."""
    e_ext = end + (XFADE_DUR if idx < len(SEGMENTS) - 1 else 0.0)
    return (
        f"[v{idx}]trim=start={start:.3f}:end={e_ext:.3f},"
        f"setpts=PTS-STARTPTS,fps=30000/1001,"
        f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H},setsar=1[s{idx}]"
    )


def _audio_seg_filter(idx: int, start: float, end: float) -> str:
    e_ext = end + (XFADE_DUR if idx < len(SEGMENTS) - 1 else 0.0)
    return (
        f"[a{idx}]atrim=start={start:.3f}:end={e_ext:.3f},"
        f"asetpts=PTS-STARTPTS[as{idx}]"
    )


def main() -> int:
    if not SRC.exists():
        print(f"source mp4 not found: {SRC}")
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT.with_suffix(".sig.ass")
    write_signature_ass(COPY1, COPY2, ass_path)

    n = len(SEGMENTS)
    parts: list[str] = []

    v_outs = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"[0:v]split={n}{v_outs}")
    for i, (s, e) in enumerate(SEGMENTS):
        parts.append(_seg_filter(i, s, e))

    prev_lbl = "[s0]"
    prev_dur = SEGMENTS[0][1] - SEGMENTS[0][0]
    for i in range(1, n):
        cur_lbl = f"[s{i}]"
        out_lbl = "[reframed]" if i == n - 1 else f"[xfv{i}]"
        parts.append(
            f"{prev_lbl}{cur_lbl}xfade=transition=fade:"
            f"duration={XFADE_DUR}:offset={prev_dur:.3f}{out_lbl}"
        )
        prev_lbl = out_lbl
        prev_dur += SEGMENTS[i][1] - SEGMENTS[i][0]

    a_outs = "".join(f"[a{i}]" for i in range(n))
    parts.append(f"[0:a]asplit={n}{a_outs}")
    for i, (s, e) in enumerate(SEGMENTS):
        parts.append(_audio_seg_filter(i, s, e))
    prev_lbl = "[as0]"
    for i in range(1, n):
        cur_lbl = f"[as{i}]"
        out_lbl = "[aout]" if i == n - 1 else f"[xfa{i}]"
        parts.append(
            f"{prev_lbl}{cur_lbl}acrossfade=duration={XFADE_DUR}{out_lbl}"
        )
        prev_lbl = out_lbl

    parts.append(signature_filter_segment_ass("[reframed]", "[out]", ass_path).rstrip(";"))
    audio_pan = "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
    parts.append(f"[aout]{audio_pan}[aout_mixed]")

    filter_complex = ";".join(parts)

    hwaccel = detect_hwaccel()
    input_decoder = ["-hwaccel", "cuda"] if hwaccel == "cuda" else []
    if settings.use_nvenc and hwaccel == "cuda":
        encoder = ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "5M"]
    else:
        encoder = ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *input_decoder,
        "-i", str(SRC),
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "[aout_mixed]",
        *encoder,
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(OUTPUT),
    ]

    print(f"=== 26-P021 compressed crop prototype ===")
    print(f"segments: {SEGMENTS}")
    total_dur = sum(e - s for s, e in SEGMENTS) - (n - 1) * XFADE_DUR
    print(f"expected duration: ~{total_dur:.1f}s")
    print(f"reframe: scale+center crop (좌우 ~28% 잘림, 인물 풀로 채움)")
    print(f"copy: {COPY1} / {COPY2}")
    print(f"output: {OUTPUT}")
    print()

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
    print(f"DONE: {OUTPUT} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
