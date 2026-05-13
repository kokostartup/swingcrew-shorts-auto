"""Scene 자동 분류 (YOLOv8-face lindevs 기반).

결정 트리 (가장 큰 얼굴 우선 — 메인 화자 따라감):
  1. 얼굴 미검출 → letterbox_4_5 (안전 fallback)
  2. 얼굴 검출 → face_centered_4_5 + 가장 큰 얼굴 평균 center x

영빈은 노션 Scene Type 컬럼에서 override 가능 (poll_status_from_notion이 sync).
talking_head_crop_static / split_right / split_left는 override 전용.

2026-05-13 영빈 결정: OpenCV Haar cascade는 골프 스튜디오 영상에서 false positive 다발
(커튼 패턴, 시뮬레이션 화면 영역, 매트 가장자리 등을 얼굴로 오인) → YOLOv8l-face로 교체.
모델 가중치: data/models/yolov8l-face-lindevs.pt (github.com/lindevs/yolov8-face v1.0.1).
"""
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.logger import get_logger
from app.utils.video import probe_dimensions

log = get_logger(__name__)

_face_detector: Any = None
YOLO_WEIGHTS_PATH = Path("data/models/yolov8l-face-lindevs.pt")
# YOLOv8 confidence threshold — false positive 차단 (영빈 영상 환경 검증).
YOLO_CONF_THRESHOLD = 0.5

LETTERBOX = "letterbox_4_5"
TALKING_HEAD = "talking_head_crop_static"
SPLIT_LEFT = "split_left"
SPLIT_RIGHT = "split_right"
FACE_CENTERED = "face_centered_4_5"
FACE_CENTERED_DYNAMIC = "face_centered_dynamic"

# cx 변동 큰 영상 (layout 전환) 판정 임계값.
DYNAMIC_STD_THRESHOLD = 0.10
# segment 분할 시 인접 cx 차이 임계값.
SEGMENT_CUT_THRESHOLD = 0.15
# 너무 짧은 segment 병합 임계값 (초).
SEGMENT_MIN_LENGTH = 2.0
# 첫 segment가 다음 segment와 cx 차이 이 이상이면 false positive 가능성 → 강제 병합.
# 영빈 영상 초반 3초는 후킹 가장 중요한 구간이라 신뢰성 우선.
FIRST_SEGMENT_OUTLIER_THRESHOLD = 0.20


def _get_face_detector() -> Any:
    """YOLOv8-face (lindevs l) lazy load + singleton."""
    global _face_detector
    if _face_detector is not None:
        return _face_detector

    if not YOLO_WEIGHTS_PATH.exists():
        raise RuntimeError(
            f"YOLOv8-face weights 없음: {YOLO_WEIGHTS_PATH}. "
            "github.com/lindevs/yolov8-face/releases v1.0.1에서 다운로드 필요."
        )

    from ultralytics import YOLO

    _face_detector = YOLO(str(YOLO_WEIGHTS_PATH))
    log.info(
        "scene.face_detector_loaded",
        model="yolov8l-face-lindevs",
        path=str(YOLO_WEIGHTS_PATH),
        conf_threshold=YOLO_CONF_THRESHOLD,
    )
    return _face_detector


def _sample_frames(
    video_path: Path, start: float, end: float, fps: int,
) -> Iterator[Any]:
    """ffmpeg로 1fps 프레임 추출 + numpy 배열 yield (RGB)."""
    import numpy as np

    width, height = probe_dimensions(video_path)
    frame_bytes = width * height * 3
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(start), "-to", str(end),
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-f", "image2pipe",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=10**7,
    )
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(
                (height, width, 3),
            )
    finally:
        proc.stdout.close() if proc.stdout else None
        proc.wait()


def _face_metrics(frame: Any, detector: Any) -> tuple[float, float | None]:
    """YOLOv8-face 단일 프레임 metrics → (area_ratio, center_x_ratio 또는 None).

    conf >= YOLO_CONF_THRESHOLD 박스만 채택. 여러 얼굴 검출 시 가장 큰 박스 기준.
    frame은 numpy ndarray (RGB or BGR 무관, ultralytics 자체 처리).
    """
    h, w = frame.shape[:2]
    frame_area = w * h
    if frame_area <= 0:
        return 0.0, None

    results = detector.predict(
        frame, conf=YOLO_CONF_THRESHOLD, verbose=False,
    )
    boxes: list[tuple[float, float, float, float]] = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            boxes.append((x1, y1, x2 - x1, y2 - y1))

    if not boxes:
        return 0.0, None

    largest = max(boxes, key=lambda b: b[2] * b[3])
    bx, _, bw, bh = largest
    center_x_ratio = (bx + bw / 2) / w
    return min(bw * bh / frame_area, 1.0), center_x_ratio


def _face_area_ratio(frame: Any, detector: Any) -> float:
    """단일 프레임 얼굴 면적 / 전체 면적 (테스트 호환용)."""
    area, _ = _face_metrics(frame, detector)
    return area


def _build_segments(
    cx_timeline: list[float | None],
    sample_step: float,
    total_duration: float,
) -> list[tuple[float, float, float]]:
    """1초 간격 cx 시계열 → segment 분할.

    cx_timeline[i] = i*sample_step ~ (i+1)*sample_step 구간의 cx (None이면 미검출).
    인접 cx 차이 > SEGMENT_CUT_THRESHOLD이면 segment 경계.
    너무 짧은 segment는 인접 segment에 병합.
    """
    if not cx_timeline:
        return []
    # Leading None은 다음 valid cx로 backward fill (default 0.5 대신).
    # 첫 프레임 얼굴 미검출 시 잘못된 가운데 crop 방지.
    first_valid_idx = next(
        (i for i, c in enumerate(cx_timeline) if c is not None), None,
    )
    if first_valid_idx is None:
        return []
    first_valid_cx = cx_timeline[first_valid_idx]
    assert first_valid_cx is not None
    filled: list[float] = []
    prev: float = first_valid_cx
    for cx in cx_timeline:
        if cx is None:
            filled.append(prev)
        else:
            filled.append(cx)
            prev = cx

    segments: list[tuple[float, float, float]] = []
    seg_start_idx = 0
    for i in range(1, len(filled)):
        if abs(filled[i] - filled[i - 1]) > SEGMENT_CUT_THRESHOLD:
            seg_start = seg_start_idx * sample_step
            seg_end = i * sample_step
            seg_cx = sum(filled[seg_start_idx:i]) / (i - seg_start_idx)
            segments.append((seg_start, seg_end, seg_cx))
            seg_start_idx = i
    # 마지막 segment
    seg_start = seg_start_idx * sample_step
    seg_cx = (
        sum(filled[seg_start_idx:]) / max(1, len(filled) - seg_start_idx)
    )
    segments.append((seg_start, total_duration, seg_cx))

    # 짧은 segment 또는 첫 segment outlier 병합 (가중 평균).
    # 영빈 영상 초반 3초 hook 신뢰성 우선 — false positive (Haar cascade가
    # 측면 얼굴 못 잡고 다른 영역 오인) 시 다음 segment cx로 보정.
    merged: list[tuple[float, float, float]] = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        dur = seg[1] - seg[0]
        is_first_outlier = (
            not merged
            and i + 1 < len(segments)
            and abs(seg[2] - segments[i + 1][2]) > FIRST_SEGMENT_OUTLIER_THRESHOLD
        )
        is_first_short = (
            not merged and dur < SEGMENT_MIN_LENGTH and i + 1 < len(segments)
        )
        if is_first_short or is_first_outlier:
            next_seg = segments[i + 1]
            next_dur = next_seg[1] - next_seg[0]
            total_dur = dur + next_dur
            new_cx = (seg[2] * dur + next_seg[2] * next_dur) / total_dur
            merged.append((seg[0], next_seg[1], new_cx))
            i += 2
            continue
        if merged and dur < SEGMENT_MIN_LENGTH:
            prev_s = merged[-1]
            prev_dur = prev_s[1] - prev_s[0]
            total_dur = prev_dur + dur
            new_cx = (prev_s[2] * prev_dur + seg[2] * dur) / total_dur
            merged[-1] = (prev_s[0], seg[1], new_cx)
        else:
            merged.append(seg)
        i += 1
    return merged


def classify_scene_with_metrics(
    video_path: Path, start: float, end: float,
) -> tuple[str, float | None, list[tuple[float, float, float]] | None]:
    """샘플 프레임에서 얼굴 cx 측정 → strategy + cx + segments.

    반환: (strategy, face_center_x | None, segments | None)
    - cx 표준편차 > DYNAMIC_STD_THRESHOLD → face_centered_dynamic + segments
    - 얼굴 검출 + 안정 → face_centered_4_5 + cx
    - 얼굴 미검출 → letterbox_4_5 + None
    """
    if not video_path.exists():
        log.warning("scene.video_missing", path=str(video_path))
        return LETTERBOX, None, None
    if end <= start:
        log.warning("scene.invalid_range", start=start, end=end)
        return LETTERBOX, None, None

    try:
        detector = _get_face_detector()
        areas: list[float] = []
        cx_per_sample: list[float | None] = []
        for frame in _sample_frames(
            video_path, start, end, settings.scene_sample_fps,
        ):
            area, cx = _face_metrics(frame, detector)
            areas.append(area)
            cx_per_sample.append(cx)

        if not areas:
            log.warning("scene.no_frames", start=start, end=end)
            return LETTERBOX, None, None

        valid_cx = [c for c in cx_per_sample if c is not None]
        if not valid_cx:
            log.info(
                "scene.classified",
                start=start, end=end, frames=len(areas),
                face_frames=0, strategy=LETTERBOX,
            )
            return LETTERBOX, None, None

        avg_cx = sum(valid_cx) / len(valid_cx)
        mean = avg_cx
        variance = sum((c - mean) ** 2 for c in valid_cx) / len(valid_cx)
        std = variance ** 0.5

        decision: str
        segments: list[tuple[float, float, float]] | None = None
        if std > DYNAMIC_STD_THRESHOLD:
            decision = FACE_CENTERED_DYNAMIC
            segments = _build_segments(
                cx_per_sample,
                sample_step=1.0 / settings.scene_sample_fps,
                total_duration=end - start,
            )
        else:
            decision = FACE_CENTERED

        log.info(
            "scene.classified",
            start=start, end=end,
            frames=len(areas), face_frames=len(valid_cx),
            avg_face_center_x=round(avg_cx, 4),
            cx_std=round(std, 4),
            strategy=decision,
            segments=len(segments) if segments else None,
        )
        return decision, avg_cx, segments
    except Exception as e:
        log.warning("scene.classify_failed", error=str(e))
        return LETTERBOX, None, None


def classify_scene(video_path: Path, start: float, end: float) -> str:
    """후방 호환 wrapper — strategy만 반환."""
    strategy, _, _ = classify_scene_with_metrics(video_path, start, end)
    return strategy
