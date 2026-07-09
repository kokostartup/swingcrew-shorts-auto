"""1회용: P021 압축 prototype — 옵션 1 person tracking 버전.

YOLOv8 person detection (yolov8n.pt 자동 다운로드) → 각 segment의 평균 person cx 계산
→ cx 기반 crop 적용. face 아닌 인물 전체 bbox 사용해서 머리 숙임/측면 무관 안정적.

segments는 동일 (curator 설계):
  A (29.82~39.35) HOOK / B (61.57~100.26) INSIGHT+DEMO
  C (609.67~630.58) DRILL / D (707.47~718.47) RESULT

출력: outputs/shorts/26-P021-compressed_person_sample.mp4
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import cv2
from ultralytics import YOLO

from app.config import settings
from app.pipeline.template import (
    CANVAS_W,
    VIDEO_H,
    signature_filter_segment_ass,
    write_signature_ass,
)
from app.utils.video import detect_hwaccel, probe_dimensions

SRC = settings.samples_dir / "mAFLAosow9M.mp4"
OUTPUT = Path("outputs/shorts/26-P021-compressed_person_sample.mp4")
XFADE_DUR = 0.3
SEGMENTS = [
    (29.82, 39.35),
    (61.57, 100.26),
    (609.67, 630.58),
    (707.47, 718.47),
]
COPY1 = "백스윙만 바꿨더니"
COPY2 = "볼스피드 78.8 찍었습니다"

# 4:5 crop 비율
CROP_RATIO = 4 / 5  # crop_w = src_h * 4/5


def extract_person_cx(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    n_samples: int = 20,
    conf_threshold: float = 0.4,
) -> tuple[float, int]:
    """segment 구간 동안 person detection 평균 cx (0~1) + sample frame 수 반환.

    person이 안 잡힌 frame은 skip. 잡힌 frame들의 모든 person bbox cx 평균.
    """
    model = YOLO("yolov8n.pt")  # 첫 호출 시 자동 다운로드 (~6MB)
    cap = cv2.VideoCapture(str(video_path))
    duration = end_sec - start_sec

    cxs: list[float] = []
    for i in range(n_samples):
        t = start_sec + (i + 0.5) * duration / n_samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret:
            continue
        h, w = frame.shape[:2]
        # class=0 (person), conf>=threshold
        results = model(frame, classes=[0], conf=conf_threshold, verbose=False)
        frame_cxs: list[float] = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes.xyxy:
                x1, _, x2, _ = box.cpu().numpy()
                frame_cxs.append(((x1 + x2) / 2) / w)
        if frame_cxs:
            # 같은 frame 안의 모든 person cx 평균 (양쪽 다 잡음)
            cxs.append(sum(frame_cxs) / len(frame_cxs))
    cap.release()
    if not cxs:
        return 0.5, 0
    return sum(cxs) / len(cxs), len(cxs)


def _crop_x_pixel(cx_ratio: float, src_w: int, crop_w: int) -> int:
    cx_ratio = max(0.0, min(1.0, cx_ratio))
    x = int(cx_ratio * src_w - crop_w / 2)
    return max(0, min(x, src_w - crop_w))


def _seg_filter(
    idx: int, start: float, end: float, cx: float, src_w: int, src_h: int,
) -> str:
    """cx-based crop chain."""
    e_ext = end + (XFADE_DUR if idx < len(SEGMENTS) - 1 else 0.0)
    crop_w = int(src_h * CROP_RATIO)
    crop_x = _crop_x_pixel(cx, src_w, crop_w)
    return (
        f"[v{idx}]trim=start={start:.3f}:end={e_ext:.3f},"
        f"setpts=PTS-STARTPTS,fps=30000/1001,"
        f"crop={crop_w}:{src_h}:{crop_x}:0,"
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

    src_w, src_h = probe_dimensions(SRC)
    crop_w_px = int(src_h * CROP_RATIO)
    print(f"원본: {src_w}x{src_h}, crop window: {crop_w_px}x{src_h}")
    print()

    print("=== Person detection 시작 (segment별 평균 cx) ===")
    seg_cxs: list[float] = []
    for i, (s, e) in enumerate(SEGMENTS):
        cx, n = extract_person_cx(SRC, s, e)
        crop_x = _crop_x_pixel(cx, src_w, crop_w_px)
        print(f"  Seg {chr(65+i)} ({s:.1f}~{e:.1f}): cx={cx:.3f} ({n} samples) → crop x={crop_x}")
        seg_cxs.append(cx)
    print()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT.with_suffix(".sig.ass")
    write_signature_ass(COPY1, COPY2, ass_path)

    n = len(SEGMENTS)
    parts: list[str] = []

    v_outs = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"[0:v]split={n}{v_outs}")
    for i, (s, e) in enumerate(SEGMENTS):
        parts.append(_seg_filter(i, s, e, seg_cxs[i], src_w, src_h))

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

    print("=== ffmpeg 인코딩 ===")
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
