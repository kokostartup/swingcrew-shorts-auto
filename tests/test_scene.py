"""Phase 4 scene 분류 테스트.

2026-06-05 영빈 결정 이후 현재 계약: classify_scene_with_metrics는 face detection을
skip하고 항상 (face_centered_dynamic, 0.5, [(0, duration, 0.5, 2)]) 반환
(모드 플리커 회피). legacy face-detection path(_classify_scene_with_face_detection)는
의도적으로 미사용 — 테스트 대상 아님.
"""

from pathlib import Path

import pytest

from app.pipeline.scene import (
    FACE_CENTERED_DYNAMIC,
    classify_scene,
    classify_scene_with_metrics,
)

SAMPLE_VIDEO = Path("data/samples/1wwEY0KEkoA.mp4")


def test_classify_scene_always_dynamic(tmp_path: Path) -> None:
    fake = tmp_path / "v.mp4"
    fake.touch()
    assert classify_scene(fake, 0, 5) == FACE_CENTERED_DYNAMIC


def test_classify_scene_with_metrics_fixed_contract(tmp_path: Path) -> None:
    """항상 (dynamic, 0.5, 전체 구간 단일 segment face_count=2) — blur padding 유도."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    strategy, cx, segments = classify_scene_with_metrics(fake, 10.0, 55.0)
    assert strategy == FACE_CENTERED_DYNAMIC
    assert cx == 0.5
    assert segments == [(0.0, 45.0, 0.5, 2)]


def test_classify_scene_with_metrics_missing_file_same_contract(tmp_path: Path) -> None:
    """파일 없어도 동일 계약 (face detection 안 하므로 파일 접근 없음)."""
    missing = tmp_path / "missing.mp4"
    strategy, cx, segments = classify_scene_with_metrics(missing, 0, 5)
    assert strategy == FACE_CENTERED_DYNAMIC
    assert segments == [(0.0, 5.0, 0.5, 2)]


@pytest.mark.slow
@pytest.mark.skipif(not SAMPLE_VIDEO.exists(), reason="sample video 없음")
def test_classify_scene_real_video() -> None:
    assert classify_scene(SAMPLE_VIDEO, 5.0, 8.0) == FACE_CENTERED_DYNAMIC
