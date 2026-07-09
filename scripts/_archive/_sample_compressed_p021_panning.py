"""1회용: P021 압축 prototype — 옵션 A frame-by-frame panning.

각 segment 안에서:
  1. 0.2초 간격 dense person detection → cx 시계열
  2. 1.5초 window moving average smoothing (부드럽게)
  3. 1초 간격 key point로 down-sample
  4. ffmpeg crop=w:h:expr:0 piecewise linear expression → crop window가 사람 따라
     부드럽게 panning

화면 사이즈 1080×1920 / 시그니처 박스 (TOP_BAND_H=480 + VIDEO_H=1350) 그대로 유지.
출력: outputs/shorts/26-P021-compressed_panning_sample.mp4
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
OUTPUT = Path("outputs/shorts/26-P021-compressed_panning_sample.mp4")
XFADE_DUR = 0.3
SAMPLE_STEP = 0.2          # dense detection 0.2초 간격
SMOOTH_WINDOW = 1.5        # moving average 1.5초 window
KEY_STEP = 1.0             # key point 1초 간격 (expression 크기 제한)
PERSON_CONF = 0.4
CROP_RATIO = 4 / 5

SEGMENTS = [
    (29.82, 39.35),
    (61.57, 100.26),
    (609.67, 630.58),
    (707.47, 718.47),
]
COPY1 = "백스윙만 바꿨더니"
COPY2 = "볼스피드 78.8 찍었습니다"


def dense_person_cx(
    model: YOLO, video_path: Path, start: float, end: float,
) -> list[tuple[float, float]]:
    """0.2초 간격 dense person detection → [(t_in_segment, cx), ...].

    여러 person 있으면 cx 평균. 없으면 마지막 cx 유지 (skip 안 함).
    """
    cap = cv2.VideoCapture(str(video_path))
    samples: list[tuple[float, float]] = []
    last_cx = 0.5
    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret:
            samples.append((t - start, last_cx))
            t += SAMPLE_STEP
            continue
        h, w = frame.shape[:2]
        results = model(frame, classes=[0], conf=PERSON_CONF, verbose=False)
        frame_cxs: list[float] = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes.xyxy:
                x1, _, x2, _ = box.cpu().numpy()
                frame_cxs.append(((x1 + x2) / 2) / w)
        if frame_cxs:
            cx = sum(frame_cxs) / len(frame_cxs)
            last_cx = cx
        else:
            cx = last_cx
        samples.append((t - start, cx))
        t += SAMPLE_STEP
    cap.release()
    return samples


def smooth_series(samples: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """moving average smoothing (window = SMOOTH_WINDOW 초)."""
    if not samples:
        return []
    half_w = SMOOTH_WINDOW / 2
    out: list[tuple[float, float]] = []
    for i, (t, _) in enumerate(samples):
        window_vals = [
            cx for tt, cx in samples
            if t - half_w <= tt <= t + half_w
        ]
        out.append((t, sum(window_vals) / len(window_vals)))
    return out


def downsample_keys(samples: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """KEY_STEP 간격으로 key points만 추출."""
    if not samples:
        return []
    keys: list[tuple[float, float]] = []
    next_t = 0.0
    for t, cx in samples:
        if t >= next_t:
            keys.append((t, cx))
            next_t += KEY_STEP
    # 마지막 sample도 포함 (panning 끝부분 보장)
    last_t, last_cx = samples[-1]
    if not keys or keys[-1][0] < last_t - 0.05:
        keys.append((last_t, last_cx))
    return keys


def _crop_x_pixel(cx_ratio: float, src_w: int, crop_w: int) -> int:
    cx_ratio = max(0.0, min(1.0, cx_ratio))
    x = int(cx_ratio * src_w - crop_w / 2)
    return max(0, min(x, src_w - crop_w))


def build_crop_x_expr(
    keys: list[tuple[float, float]], src_w: int, crop_w: int,
) -> str:
    """piecewise linear ffmpeg crop x expression.

    각 key (t_in_segment, cx_ratio) → crop_x_pixel
    t가 [t_i, t_{i+1}] 사이면 (x_i, x_{i+1}) 선형 보간.
    expression은 안에서 밖으로 중첩 if 구성.
    """
    if not keys:
        return str((src_w - crop_w) // 2)
    if len(keys) == 1:
        return str(_crop_x_pixel(keys[0][1], src_w, crop_w))

    # px 시리즈
    key_px = [(t, _crop_x_pixel(cx, src_w, crop_w)) for t, cx in keys]

    # 끝값 (마지막 key 이후)
    expr = str(key_px[-1][1])
    # 뒤에서 앞으로 if 중첩
    for i in range(len(key_px) - 1, 0, -1):
        t1, x1 = key_px[i - 1]
        t2, x2 = key_px[i]
        if t2 <= t1:
            continue
        lerp = f"({x1}+({x2}-{x1})*(t-{t1:.3f})/{t2 - t1:.3f})"
        expr = f"if(lt(t\\,{t2:.3f})\\,{lerp}\\,{expr})"
    # 첫 key 이전
    expr = f"if(lt(t\\,{key_px[0][0]:.3f})\\,{key_px[0][1]}\\,{expr})"
    return expr


def _seg_filter(
    idx: int, start: float, end: float, crop_x_expr: str,
    src_w: int, src_h: int, n_total: int,
) -> str:
    e_ext = end + (XFADE_DUR if idx < n_total - 1 else 0.0)
    crop_w = int(src_h * CROP_RATIO)
    return (
        f"[v{idx}]trim=start={start:.3f}:end={e_ext:.3f},"
        f"setpts=PTS-STARTPTS,fps=30000/1001,"
        f"crop={crop_w}:{src_h}:'{crop_x_expr}':0,"
        f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H},setsar=1[s{idx}]"
    )


def _audio_seg_filter(idx: int, start: float, end: float, n_total: int) -> str:
    e_ext = end + (XFADE_DUR if idx < n_total - 1 else 0.0)
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
    print(f"원본: {src_w}x{src_h}, crop window: {crop_w_px}x{src_h}\n")

    print("=== Dense panning 시계열 추출 ===")
    model = YOLO("yolov8n.pt")
    seg_exprs: list[str] = []
    for i, (s, e) in enumerate(SEGMENTS):
        dense = dense_person_cx(model, SRC, s, e)
        smoothed = smooth_series(dense)
        keys = downsample_keys(smoothed)
        expr = build_crop_x_expr(keys, src_w, crop_w_px)
        print(
            f"  Seg {chr(65+i)} ({s:.1f}~{e:.1f}, {e-s:.1f}s): "
            f"dense={len(dense)}, keys={len(keys)}"
        )
        for t, cx in keys[:3]:
            print(f"    key t={t:.2f}s cx={cx:.3f} → x={_crop_x_pixel(cx, src_w, crop_w_px)}")
        if len(keys) > 3:
            print(f"    ... ({len(keys)-3} more)")
        seg_exprs.append(expr)
    print()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT.with_suffix(".sig.ass")
    write_signature_ass(COPY1, COPY2, ass_path)

    n = len(SEGMENTS)
    parts: list[str] = []
    v_outs = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"[0:v]split={n}{v_outs}")
    for i, (s, e) in enumerate(SEGMENTS):
        parts.append(_seg_filter(i, s, e, seg_exprs[i], src_w, src_h, n))

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
        parts.append(_audio_seg_filter(i, s, e, n))
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
        print(result.stderr[-3000:])
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
