"""클립 컷 + 9:16 reframe + 시그니처 합성을 단일 filter_complex로 처리."""
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from app.config import settings
from app.pipeline.template import CANVAS_W, VIDEO_H, signature_filter_segment
from app.utils.logger import get_logger
from app.utils.video import assert_video_meta, detect_hwaccel, probe_dimensions

log = get_logger(__name__)

Strategy = Literal[
    "letterbox_4_5",
    "talking_head_crop_static",
    "split_right",
    "split_left",
    "face_centered_4_5",
    "face_centered_dynamic",
]


def _letterbox_4_5(in_label: str, out_label: str) -> str:
    """원본을 4:5로 crop + 1080×1350 채움 (16:9 기준 좌우 약 55% crop)."""
    return (
        f"{in_label}scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H}{out_label}"
    )


def _talking_head_crop_static(in_label: str, out_label: str) -> str:
    """원본 가운데 9:16 → 1080×1350. 인물 풀스크린 (좌우 약 75% crop)."""
    return (
        f"{in_label}crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H}{out_label}"
    )


def _split_right(in_label: str, out_label: str) -> str:
    """원본 우측 9:16 → 1080×1350. split-screen에서 화자가 우측인 경우."""
    return (
        f"{in_label}crop=ih*9/16:ih:iw-ih*9/16:0,"
        f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H}{out_label}"
    )


def _split_left(in_label: str, out_label: str) -> str:
    """원본 좌측 9:16 → 1080×1350. split-screen에서 화자가 좌측인 경우."""
    return (
        f"{in_label}crop=ih*9/16:ih:0:0,"
        f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H}{out_label}"
    )


REFRAME_FILTERS: dict[Strategy, Callable[[str, str], str]] = {
    "letterbox_4_5": _letterbox_4_5,
    "talking_head_crop_static": _talking_head_crop_static,
    "split_right": _split_right,
    "split_left": _split_left,
}


def _crop_x_pixel(cx_ratio: float, src_w: int, crop_w: int) -> int:
    """cx_ratio (0~1)와 영상 폭으로 crop x 시작 픽셀 계산 (clamp)."""
    cx_ratio = max(0.0, min(1.0, cx_ratio))
    x = int(cx_ratio * src_w - crop_w / 2)
    return max(0, min(x, src_w - crop_w))


def _face_centered_dynamic(
    in_label: str, out_label: str,
    segments: list[tuple[float, float, float]],
    src_w: int, src_h: int,
) -> str:
    """segment별로 다른 cx로 4:5 crop → trim/concat. layout 전환 영상 대응.

    segments: [(start_offset, end_offset, cx_ratio), ...] 모먼트 시작 기준 상대 시간.
    각 segment를 trim + crop + scale → concat. audio는 별도 처리 (원본 그대로).
    """
    if not segments:
        raise ValueError("segments empty — face_centered_dynamic 불가")
    crop_w = int(src_h * 4 / 5)
    n = len(segments)

    # split N개 + 각각 trim/crop/scale + concat.
    split_outs = "".join(f"[v{i}]" for i in range(n))
    parts: list[str] = [f"{in_label}split={n}{split_outs}"]
    for i, (s_start, s_end, cx) in enumerate(segments):
        crop_x = _crop_x_pixel(cx, src_w, crop_w)
        parts.append(
            f"[v{i}]trim=start={s_start:.3f}:end={s_end:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"crop={crop_w}:{src_h}:{crop_x}:0,"
            f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{VIDEO_H}[s{i}]"
        )
    concat_inputs = "".join(f"[s{i}]" for i in range(n))
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0{out_label}")
    return ";".join(parts) + ";"


def _face_centered_4_5(
    in_label: str, out_label: str,
    cx_ratio: float, src_w: int, src_h: int,
) -> str:
    """얼굴 중심 x 위치 기반 4:5 crop. 위아래 잘림 없음.

    cx_ratio: 0~1 (얼굴 평균 center x / 영상 폭).
    crop 좌표는 python에서 정수 픽셀로 미리 계산 (ffmpeg expression 콤마 escape 회피).
    """
    cx_ratio = max(0.0, min(1.0, cx_ratio))
    crop_w = int(src_h * 4 / 5)
    crop_x = int(cx_ratio * src_w - crop_w / 2)
    crop_x = max(0, min(crop_x, src_w - crop_w))
    return (
        f"{in_label}crop={crop_w}:{src_h}:{crop_x}:0,"
        f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{VIDEO_H}{out_label}"
    )


def make_short(
    src: Path,
    start: float,
    end: float,
    strategy: Strategy,
    copy1: str,
    copy2: str,
    output: Path,
    face_center_x: float | None = None,
    face_segments: list[tuple[float, float, float]] | None = None,
) -> Path:
    """미드폼 [start, end] 구간을 시그니처 9:16 숏츠로 변환.

    face_center_x: strategy='face_centered_4_5'일 때 crop 중심 (0~1).
    face_segments: strategy='face_centered_dynamic'일 때 segment 리스트.
    """
    if not settings.font_path.exists():
        raise FileNotFoundError(
            f"FONT_PATH not found: {settings.font_path}. "
            "Pretendard-Bold.otf를 app/utils/fonts/ 폴더에 넣어주세요."
        )
    if strategy == "face_centered_4_5":
        if face_center_x is None:
            log.warning(
                "make_short.face_centered_no_cx_fallback_letterbox",
                src=str(src), start=start,
            )
            strategy = "letterbox_4_5"
    elif strategy == "face_centered_dynamic":
        if not face_segments:
            log.warning(
                "make_short.dynamic_no_segments_fallback_letterbox",
                src=str(src), start=start,
            )
            strategy = "letterbox_4_5"
    elif strategy not in REFRAME_FILTERS:
        raise ValueError(f"Unknown strategy: {strategy}")
    if end <= start:
        raise ValueError(f"end ({end}) must be > start ({start})")

    duration = end - start
    output.parent.mkdir(parents=True, exist_ok=True)

    if strategy == "face_centered_4_5":
        assert face_center_x is not None
        src_w, src_h = probe_dimensions(src)
        reframe_segment = _face_centered_4_5(
            "[0:v]", "[reframed];", face_center_x, src_w, src_h,
        )
    elif strategy == "face_centered_dynamic":
        assert face_segments is not None
        src_w, src_h = probe_dimensions(src)
        reframe_segment = _face_centered_dynamic(
            "[0:v]", "[reframed]", face_segments, src_w, src_h,
        )
    else:
        reframe = REFRAME_FILTERS[strategy]
        reframe_segment = reframe("[0:v]", "[reframed];")
    signature = signature_filter_segment("[reframed]", "[out]", copy1, copy2)
    filter_complex = reframe_segment + signature

    hwaccel = detect_hwaccel()
    input_decoder = ["-hwaccel", "cuda"] if hwaccel == "cuda" else []

    encoder: list[str]
    if settings.use_nvenc and hwaccel == "cuda":
        encoder = ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "5M"]
    else:
        encoder = ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]

    # 보통: 정확한 seek (-ss를 -i 뒤). dynamic은 filter trim이 cut 처리하므로
    # input 측 fast seek로 두어야 trim 시간(모먼트 상대)이 정확히 매칭됨.
    if strategy == "face_centered_dynamic":
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *input_decoder,
            "-ss", str(start), "-to", str(end),
            "-i", str(src),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            *encoder,
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *input_decoder,
            "-i", str(src),
            "-ss", str(start), "-to", str(end),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            *encoder,
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output),
        ]

    log.info(
        "ffmpeg.run", strategy=strategy, src=str(src), output=str(output),
        start=start, end=end, encoder=encoder[1], hwaccel=hwaccel,
    )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg.failed", stderr=result.stderr)
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}):\n{result.stderr}")

    assert_video_meta(output, expected_dur=duration)
    log.info("ffmpeg.done", output=str(output), duration=duration)
    return output
