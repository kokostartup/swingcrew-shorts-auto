"""1회용: P021 압축 prototype — person-filter 버전.

각 segment를 1초 chunk로 쪼개고:
  - chunk 안에 person 검출되면 → keep + 그 chunk의 person cx 사용
  - person 없는 chunk → drop
연속 keep chunk를 micro-segment로 묶고 xfade chain.

사람 없는 frame 아예 안 나오게.
영상 길이는 자동 단축됨 (사람 없는 chunk 빠진 만큼).

출력: outputs/shorts/26-P021-compressed_person_filter.mp4
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
OUTPUT = Path("outputs/shorts/26-P021-compressed_person_filter.mp4")
XFADE_DUR = 0.3
CHUNK_SEC = 1.0           # 1초 단위 person detection
SAMPLES_PER_CHUNK = 5      # chunk 안에서 5 frame 샘플링
PERSON_CONF = 0.4
CROP_RATIO = 4 / 5         # crop_w = src_h * 4/5

SEGMENTS = [
    (29.82, 39.35),
    (61.57, 100.26),
    (609.67, 630.58),
    (707.47, 718.47),
]
COPY1 = "백스윙만 바꿨더니"
COPY2 = "볼스피드 78.8 찍었습니다"


def detect_person_chunks(
    video_path: Path,
    start_sec: float,
    end_sec: float,
) -> list[tuple[float, float, float]]:
    """1초 chunk별 person detection → (chunk_start, chunk_end, cx) 리스트.

    person 없는 chunk는 결과에 미포함. 연속된 chunk는 연결 가능.
    """
    model = YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(str(video_path))

    chunks: list[tuple[float, float, float]] = []
    t = start_sec
    while t < end_sec:
        chunk_end = min(t + CHUNK_SEC, end_sec)
        chunk_cxs: list[float] = []
        for i in range(SAMPLES_PER_CHUNK):
            sample_t = t + (i + 0.5) * (chunk_end - t) / SAMPLES_PER_CHUNK
            cap.set(cv2.CAP_PROP_POS_MSEC, sample_t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue
            h, w = frame.shape[:2]
            results = model(frame, classes=[0], conf=PERSON_CONF, verbose=False)
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes.xyxy:
                    x1, _, x2, _ = box.cpu().numpy()
                    chunk_cxs.append(((x1 + x2) / 2) / w)
        if chunk_cxs:
            chunks.append((t, chunk_end, sum(chunk_cxs) / len(chunk_cxs)))
        t = chunk_end
    cap.release()
    return chunks


def merge_contiguous(chunks: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """연속된 chunk들을 하나로 합침 (인접 chunk가 같은 cx 부근이면 단일 micro-segment)."""
    if not chunks:
        return []
    merged: list[tuple[float, float, float]] = []
    cur_start, cur_end, cur_cxs = chunks[0][0], chunks[0][1], [chunks[0][2]]
    for s, e, cx in chunks[1:]:
        # 시간 연속 + cx 차이 작으면 merge
        if abs(s - cur_end) < 0.05 and abs(cx - sum(cur_cxs) / len(cur_cxs)) < 0.1:
            cur_end = e
            cur_cxs.append(cx)
        else:
            merged.append((cur_start, cur_end, sum(cur_cxs) / len(cur_cxs)))
            cur_start, cur_end, cur_cxs = s, e, [cx]
    merged.append((cur_start, cur_end, sum(cur_cxs) / len(cur_cxs)))
    return merged


def _crop_x_pixel(cx_ratio: float, src_w: int, crop_w: int) -> int:
    cx_ratio = max(0.0, min(1.0, cx_ratio))
    x = int(cx_ratio * src_w - crop_w / 2)
    return max(0, min(x, src_w - crop_w))


def _seg_filter(
    idx: int, start: float, end: float, cx: float, src_w: int, src_h: int, n_total: int,
) -> str:
    e_ext = end + (XFADE_DUR if idx < n_total - 1 else 0.0)
    crop_w = int(src_h * CROP_RATIO)
    crop_x = _crop_x_pixel(cx, src_w, crop_w)
    return (
        f"[v{idx}]trim=start={start:.3f}:end={e_ext:.3f},"
        f"setpts=PTS-STARTPTS,fps=30000/1001,"
        f"crop={crop_w}:{src_h}:{crop_x}:0,"
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
    print(f"원본: {src_w}x{src_h}\n")

    print("=== Person filter (1초 chunk) ===")
    all_micro: list[tuple[float, float, float]] = []
    for i, (s, e) in enumerate(SEGMENTS):
        chunks = detect_person_chunks(SRC, s, e)
        merged = merge_contiguous(chunks)
        seg_dur = sum(end - start for start, end, _ in merged)
        orig_dur = e - s
        print(f"  Seg {chr(65+i)} ({orig_dur:.1f}s): chunks={len(chunks)}, "
              f"merged={len(merged)}, kept={seg_dur:.1f}s ({100*seg_dur/orig_dur:.0f}%)")
        for j, (ms, me, mcx) in enumerate(merged):
            print(f"    [{j}] {ms:.1f}~{me:.1f} ({me-ms:.1f}s) cx={mcx:.3f}")
        all_micro.extend(merged)
    print()

    if not all_micro:
        print("ERROR: person 검출된 chunk 없음")
        return 1

    n = len(all_micro)
    total_dur = sum(e - s for s, e, _ in all_micro) - (n - 1) * XFADE_DUR
    print(f"=== 총 {n}개 micro-segment, 예상 길이 {total_dur:.1f}s ===\n")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT.with_suffix(".sig.ass")
    write_signature_ass(COPY1, COPY2, ass_path)

    parts: list[str] = []
    v_outs = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"[0:v]split={n}{v_outs}")
    for i, (s, e, cx) in enumerate(all_micro):
        parts.append(_seg_filter(i, s, e, cx, src_w, src_h, n))

    prev_lbl = "[s0]"
    prev_dur = all_micro[0][1] - all_micro[0][0]
    for i in range(1, n):
        cur_lbl = f"[s{i}]"
        out_lbl = "[reframed]" if i == n - 1 else f"[xfv{i}]"
        parts.append(
            f"{prev_lbl}{cur_lbl}xfade=transition=fade:"
            f"duration={XFADE_DUR}:offset={prev_dur:.3f}{out_lbl}"
        )
        prev_lbl = out_lbl
        prev_dur += all_micro[i][1] - all_micro[i][0]

    a_outs = "".join(f"[a{i}]" for i in range(n))
    parts.append(f"[0:a]asplit={n}{a_outs}")
    for i, (s, e, _) in enumerate(all_micro):
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
