"""YOLOv8-face 사전 평가: P002-S02, P003-S05의 face detection 결과 비교 (1회용).

Haar vs YOLOv8 — 각 영상의 5초 단위 1fps 샘플링 후 결과 print:
- Haar: 기존 minSize=40, minNeighbors=5
- YOLOv8: conf=0.5 (lindevs l)

영빈이 본 false positive (P002-S02 80% hook ratio, P003-S05 모든 segment area<1%)이
YOLOv8에서는 깨끗하게 사라지는지 검증.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2  # noqa: F401  (Haar 비교용)

from app.config import settings
from app.pipeline.scene import _get_face_detector, _sample_frames

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


YOLO_WEIGHTS = Path("data/models/yolov8l-face-lindevs.pt")
TARGETS: list[tuple[str, str, float, float]] = [
    ("26-P002-S02", "TBD", 350.454, 425.6),  # youtube_id 채워야
    ("26-P003-S05", "TBD", 720.697, 775.0),
]


def haar_detect(frame, detector) -> list[tuple[int, int, int, int]]:
    import cv2 as _cv2
    gray = _cv2.cvtColor(frame, _cv2.COLOR_RGB2GRAY)
    faces = detector.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40),
    )
    return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]


def yolo_detect(frame, model, conf: float = 0.5) -> list[tuple[float, float, float, float, float]]:
    """YOLOv8 face detection → [(x, y, w, h, conf), ...]."""
    # ultralytics는 BGR or RGB 둘 다 OK (numpy 배열). conf threshold 적용.
    results = model.predict(frame, conf=conf, verbose=False)
    out: list[tuple[float, float, float, float, float]] = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            c = float(box.conf[0])
            out.append((x1, y1, x2 - x1, y2 - y1, c))
    return out


def main() -> None:
    import sqlite3
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row

    from ultralytics import YOLO
    yolo = YOLO(str(YOLO_WEIGHTS))
    haar = _get_face_detector()

    for iid, _placeholder, start, end in TARGETS:
        r = conn.execute(
            "SELECT v.youtube_id, v.duration FROM shorts s JOIN videos v "
            "ON s.source_video_id = v.id WHERE s.internal_id = ?",
            (iid,),
        ).fetchone()
        if r is None:
            print(f"{iid}: not found in db")
            continue
        yid = r["youtube_id"]
        mp4 = settings.samples_dir / f"{yid}.mp4"
        print(f"\n=== {iid} ({yid}) start={start} end={end} ===")
        print(f"{'time':6s} {'haar':35s} {'yolo':50s}")
        for i, frame in enumerate(_sample_frames(mp4, start, end, 1)):
            t = start + i
            haar_boxes = haar_detect(frame, haar)
            yolo_boxes = yolo_detect(frame, yolo, conf=0.5)
            h, w = frame.shape[:2]
            haar_summary = (
                f"{len(haar_boxes)} boxes "
                + ", ".join(
                    f"size={bw}x{bh}, cx={(bx+bw/2)/w:.2f}"
                    for bx, by, bw, bh in haar_boxes[:2]
                )
            ) if haar_boxes else "0"
            yolo_summary = (
                f"{len(yolo_boxes)} boxes "
                + ", ".join(
                    f"conf={c:.2f}, size={int(bw)}x{int(bh)}, cx={(bx+bw/2)/w:.2f}"
                    for bx, by, bw, bh, c in yolo_boxes[:2]
                )
            ) if yolo_boxes else "0"
            print(f"{t:6.1f} {haar_summary[:35]:35s} {yolo_summary[:50]:50s}")
            if i >= 10:  # 처음 10초만
                break

    conn.close()


if __name__ == "__main__":
    main()
