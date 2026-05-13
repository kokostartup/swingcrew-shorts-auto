"""P002-S02 + P003-S05의 첫 frame에 YOLOv8 + Haar box overlay 저장 (영빈 확인용)."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from app.config import settings
from app.pipeline.scene import _get_face_detector

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


OUT_DIR = Path("outputs/eval_frames")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_frame(mp4: Path, t: float) -> np.ndarray:
    import subprocess
    from app.utils.video import probe_dimensions

    w, h = probe_dimensions(mp4)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(t), "-i", str(mp4),
        "-vframes", "1", "-f", "image2pipe",
        "-vcodec", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape((h, w, 3))


def overlay(frame: np.ndarray, label: str, out_path: Path) -> None:
    from ultralytics import YOLO

    yolo = YOLO("data/models/yolov8l-face-lindevs.pt")
    haar = _get_face_detector()

    img = frame.copy()
    # Haar — red
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    haar_boxes = haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    for x, y, w, h in haar_boxes:
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), (0, 0, 255), 3)
        cv2.putText(img, "Haar", (int(x), int(y) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    # YOLO — green, with confidence
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = yolo.predict(rgb, conf=0.5, verbose=False)
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            c = float(box.conf[0])
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 3)
            cv2.putText(img, f"YOLO {c:.2f}", (x1, max(y2 + 25, 30)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    cv2.putText(img, label, (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 0), 3)
    cv2.imwrite(str(out_path), img)
    print(f"saved: {out_path}  haar={len(haar_boxes)} yolo={sum(len(r.boxes) for r in results)}")


def main() -> None:
    import sqlite3
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row

    targets = [
        ("26-P002-S02", 350.5),
        ("26-P003-S05", 720.7),
    ]
    for iid, t in targets:
        r = conn.execute(
            "SELECT v.youtube_id FROM shorts s JOIN videos v "
            "ON s.source_video_id = v.id WHERE s.internal_id = ?",
            (iid,),
        ).fetchone()
        yid = r["youtube_id"]
        mp4 = settings.samples_dir / f"{yid}.mp4"
        frame = extract_frame(mp4, t)
        out = OUT_DIR / f"{iid}_at_{int(t)}s.jpg"
        overlay(frame, f"{iid} t={t}s", out)
    conn.close()


if __name__ == "__main__":
    main()
