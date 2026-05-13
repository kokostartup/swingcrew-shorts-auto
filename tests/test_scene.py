"""Phase 4 scene 분류 테스트."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.pipeline.scene import (
    FACE_CENTERED,
    LETTERBOX,
    SPLIT_LEFT,
    SPLIT_RIGHT,
    TALKING_HEAD,
    _face_area_ratio,
    classify_scene,
    classify_scene_with_metrics,
)

SAMPLE_VIDEO = Path("data/samples/1wwEY0KEkoA.mp4")


def _mk_cv_detector(faces: list[tuple[int, int, int, int]]) -> MagicMock:
    """OpenCV CascadeClassifier mock — detectMultiScale 반환."""
    detector = MagicMock()
    if faces:
        detector.detectMultiScale.return_value = np.array(faces, dtype=np.int32)
    else:
        # OpenCV는 얼굴 없으면 빈 튜플 또는 ndarray 반환
        detector.detectMultiScale.return_value = ()
    return detector


def test_face_area_ratio_no_face_returns_zero() -> None:
    detector = _mk_cv_detector(faces=[])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert _face_area_ratio(frame, detector) == 0.0


def test_face_area_ratio_single_face() -> None:
    # 100x100 frame, face 50x50 = 2500/10000 = 0.25
    detector = _mk_cv_detector(faces=[(25, 25, 50, 50)])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    result = _face_area_ratio(frame, detector)
    assert abs(result - 0.25) < 1e-6


def test_face_area_ratio_capped_at_one() -> None:
    # 10 faces of 50x50 each = 25000 > 10000 → cap to 1.0
    detector = _mk_cv_detector(faces=[(0, 0, 50, 50)] * 10)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert _face_area_ratio(frame, detector) == 1.0


def test_classify_scene_invalid_range_returns_letterbox(tmp_path: Path) -> None:
    fake = tmp_path / "v.mp4"
    fake.touch()
    assert classify_scene(fake, 10, 5) == LETTERBOX


def test_classify_scene_missing_file_returns_letterbox(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    assert classify_scene(missing, 0, 5) == LETTERBOX


def test_classify_scene_face_detected_returns_face_centered(
    tmp_path: Path,
) -> None:
    """얼굴 안정 + 검출 → face_centered_4_5 + cx 평균. segments None."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    detector = _mk_cv_detector(faces=[(75, 30, 20, 30)])

    def fake_frames(*a, **kw):  # noqa: ANN001, ARG001
        yield np.zeros((100, 100, 3), dtype=np.uint8)
        yield np.zeros((100, 100, 3), dtype=np.uint8)

    with patch("app.pipeline.scene._get_face_detector", return_value=detector), \
         patch("app.pipeline.scene._sample_frames", side_effect=fake_frames):
        strategy, cx, segments = classify_scene_with_metrics(fake, 0, 5)
    assert strategy == FACE_CENTERED
    assert cx is not None and abs(cx - 0.85) < 1e-6
    assert segments is None  # std=0, 안정


def test_classify_scene_largest_face_wins(tmp_path: Path) -> None:
    """여러 얼굴 중 가장 큰 얼굴의 cx만 사용 (작은 얼굴 무시)."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    detector = _mk_cv_detector(faces=[(10, 30, 10, 10), (75, 30, 20, 30)])

    def fake_frames(*a, **kw):  # noqa: ANN001, ARG001
        yield np.zeros((100, 100, 3), dtype=np.uint8)

    with patch("app.pipeline.scene._get_face_detector", return_value=detector), \
         patch("app.pipeline.scene._sample_frames", side_effect=fake_frames):
        strategy, cx, _ = classify_scene_with_metrics(fake, 0, 5)
    assert strategy == FACE_CENTERED
    assert cx is not None and abs(cx - 0.85) < 1e-6


def test_classify_scene_no_face_returns_letterbox(tmp_path: Path) -> None:
    """얼굴 미검출 → letterbox + cx None + segments None."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    detector = _mk_cv_detector(faces=[])

    def fake_frames(*a, **kw):  # noqa: ANN001, ARG001
        yield np.zeros((100, 100, 3), dtype=np.uint8)

    with patch("app.pipeline.scene._get_face_detector", return_value=detector), \
         patch("app.pipeline.scene._sample_frames", side_effect=fake_frames):
        strategy, cx, segments = classify_scene_with_metrics(fake, 0, 5)
    assert strategy == LETTERBOX
    assert cx is None
    assert segments is None


def test_classify_scene_dynamic_when_cx_varies(tmp_path: Path) -> None:
    """cx 변동 큰 영상 → face_centered_dynamic + segments 분할."""
    from app.pipeline.scene import FACE_CENTERED_DYNAMIC

    fake = tmp_path / "v.mp4"
    fake.touch()
    # 프레임마다 cx 다름: 좌측(10), 좌측(10), 우측(80), 우측(80) → std 큼
    frames = [
        np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(4)
    ]
    # 각 프레임 호출마다 다른 detect 결과를 위해 side_effect 사용
    cx_sequence = [
        np.array([(5, 30, 20, 30)], dtype=np.int32),  # cx=0.15
        np.array([(5, 30, 20, 30)], dtype=np.int32),  # cx=0.15
        np.array([(75, 30, 20, 30)], dtype=np.int32),  # cx=0.85
        np.array([(75, 30, 20, 30)], dtype=np.int32),  # cx=0.85
    ]
    detector = MagicMock()
    detector.detectMultiScale.side_effect = cx_sequence

    def fake_frames_gen(*a, **kw):  # noqa: ANN001, ARG001
        yield from frames

    with patch("app.pipeline.scene._get_face_detector", return_value=detector), \
         patch("app.pipeline.scene._sample_frames", side_effect=fake_frames_gen):
        strategy, cx, segments = classify_scene_with_metrics(fake, 0, 4)
    # cx 표준편차 > 0.10 → dynamic. segments 1개 이상 (첫 outlier는 병합 가능).
    assert strategy == FACE_CENTERED_DYNAMIC
    assert segments is not None and len(segments) >= 1


def test_classify_scene_wrapper_returns_string_only(tmp_path: Path) -> None:
    """기존 classify_scene wrapper는 strategy str만 반환 (호환성)."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    detector = _mk_cv_detector(faces=[])

    def fake_frames(*a, **kw):  # noqa: ANN001, ARG001
        yield np.zeros((100, 100, 3), dtype=np.uint8)

    with patch("app.pipeline.scene._get_face_detector", return_value=detector), \
         patch("app.pipeline.scene._sample_frames", side_effect=fake_frames):
        result = classify_scene(fake, 0, 5)
    assert result == LETTERBOX


@pytest.mark.slow
@pytest.mark.skipif(not SAMPLE_VIDEO.exists(), reason="sample video 없음")
def test_classify_scene_real_video(tmp_path: Path) -> None:
    result = classify_scene(SAMPLE_VIDEO, 5.0, 8.0)
    assert result in (LETTERBOX, FACE_CENTERED, TALKING_HEAD, SPLIT_LEFT, SPLIT_RIGHT)
