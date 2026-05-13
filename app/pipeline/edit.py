"""클립 컷 + 9:16 reframe + 시그니처 합성을 단일 filter_complex로 처리."""
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from app.config import settings
from app.pipeline.template import (
    CANVAS_W,
    VIDEO_H,
    signature_filter_segment_ass,
    write_signature_ass,
)
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


XFADE_DURATION = 0.3  # 부드러운 전환 (sec). segment 사이 cross-fade 길이.


def _face_centered_dynamic(
    in_label: str, out_label: str,
    segments: list[tuple[float, float, float, int]],
    src_w: int, src_h: int,
) -> str:
    """segment별 face_count로 cover scale vs fit letterbox 자동 분기 + xfade chain.

    segments: [(start_offset, end_offset, cx_ratio, face_count), ...]
      - face_count 1: cover scale + cx crop (영상 가득)
      - face_count >= 2: fit scale + 상하 검정 padding (wide letterbox)

    segment 사이 XFADE_DURATION 초 cross-fade로 부드럽게 전환 (영빈 요구).
    audio도 같은 timing acrossfade chain — 영상 길이 = sum(seg_dur) - (N-1)*XFADE.
    """
    if not segments:
        raise ValueError("segments empty — face_centered_dynamic 불가")
    crop_w = int(src_h * 4 / 5)
    n = len(segments)

    parts: list[str] = []

    # Segment 끝을 XFADE_DURATION만큼 연장해서 xfade overlap 처리 (마지막 segment 제외).
    # → 영상 + audio 최종 길이 = sum(original) 유지 (어미 안 잘림, sync OK).
    def _extended_end(i: int) -> float:
        return segments[i][1] + (XFADE_DURATION if i < n - 1 else 0.0)

    # === Video chain ===
    v_split_outs = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{in_label}split={n}{v_split_outs}")
    for i, seg in enumerate(segments):
        s_start, cx = seg[0], seg[2]
        e_end = _extended_end(i)
        fc = seg[3] if len(seg) >= 4 else 1
        if fc >= 2:
            # Wide letterbox (3:2 = 6:4 비율) — 영상 좌우 약간 crop + 상하 약한 padding.
            # 1) crop iw=ih*1.5 (16:9→3:2, 양옆 ~16% 잘림 — 영빈 "양옆 조금 잘려도 OK")
            # 2) scale 1080:720 후 1080×1350 영역 center pad (위/아래 315px씩)
            parts.append(
                f"[v{i}]trim=start={s_start:.3f}:end={e_end:.3f},"
                f"setpts=PTS-STARTPTS,"
                f"crop=ih*1.5:ih:(iw-ih*1.5)/2:0,"
                f"scale={CANVAS_W}:-2,"
                f"pad={CANVAS_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1[s{i}]"
            )
        else:
            crop_x = _crop_x_pixel(cx, src_w, crop_w)
            parts.append(
                f"[v{i}]trim=start={s_start:.3f}:end={e_end:.3f},"
                f"setpts=PTS-STARTPTS,"
                f"crop={crop_w}:{src_h}:{crop_x}:0,"
                f"scale={CANVAS_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{VIDEO_H},setsar=1[s{i}]"
            )

    # Video xfade chain — offset = "원래 segment duration" (확장 안 한 길이). 영상 끝부분에서 fade 시작.
    if n == 1:
        parts.append(f"[s0]copy{out_label}")
    else:
        prev_lbl = "[s0]"
        prev_orig_dur = segments[0][1] - segments[0][0]
        for i in range(1, n):
            next_lbl = f"[s{i}]"
            cur_out = out_label if i == n - 1 else f"[xfv{i}]"
            parts.append(
                f"{prev_lbl}{next_lbl}xfade=transition=fade:"
                f"duration={XFADE_DURATION}:offset={prev_orig_dur:.3f}{cur_out}"
            )
            prev_lbl = cur_out
            prev_orig_dur += segments[i][1] - segments[i][0]

    # === Audio chain ===
    a_split_outs = "".join(f"[a{i}]" for i in range(n))
    parts.append(f"[0:a]asplit={n}{a_split_outs}")
    for i, seg in enumerate(segments):
        s_start = seg[0]
        e_end = _extended_end(i)
        parts.append(
            f"[a{i}]atrim=start={s_start:.3f}:end={e_end:.3f},"
            f"asetpts=PTS-STARTPTS[as{i}]"
        )
    if n == 1:
        parts.append("[as0]acopy[aout]")
    else:
        prev_lbl = "[as0]"
        for i in range(1, n):
            next_lbl = f"[as{i}]"
            cur_out = "[aout]" if i == n - 1 else f"[xfa{i}]"
            parts.append(
                f"{prev_lbl}{next_lbl}acrossfade=duration={XFADE_DURATION}{cur_out}"
            )
            prev_lbl = cur_out

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
    face_segments: list[tuple[float, float, float, int]] | None = None,
    internal_id: str | None = None,
) -> Path:
    """미드폼 [start, end] 구간을 시그니처 9:16 숏츠로 변환.

    face_center_x: strategy='face_centered_4_5'일 때 crop 중심 (0~1).
    face_segments: strategy='face_centered_dynamic'일 때 segment 리스트.
    internal_id: B 시리즈(`26-B*`)면 영빈 영상의 상하 burn-in 자막/카피를 자르고
        나머지를 영상 영역에 채움 (상하 각 10%).
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

    # B 시리즈는 영빈 영상 상하 burn-in (위 노란 카피 + 아래 흰 자막) 잘라내고 영상 영역에 채움.
    # P 시리즈는 영상 자체 자막이 정보라 그대로 (영빈 결정).
    is_b_series = bool(internal_id and internal_id.startswith("26-B"))
    burn_crop = "[0:v]crop=iw:ih*0.8:0:ih*0.1[bc];" if is_b_series else ""
    in_label = "[bc]" if is_b_series else "[0:v]"

    if strategy == "face_centered_4_5":
        assert face_center_x is not None
        src_w, src_h = probe_dimensions(src)
        effective_src_h = int(src_h * 0.8) if is_b_series else src_h
        reframe_segment = _face_centered_4_5(
            in_label, "[reframed];", face_center_x, src_w, effective_src_h,
        )
    elif strategy == "face_centered_dynamic":
        assert face_segments is not None
        src_w, src_h = probe_dimensions(src)
        effective_src_h = int(src_h * 0.8) if is_b_series else src_h
        reframe_segment = _face_centered_dynamic(
            in_label, "[reframed]", face_segments, src_w, effective_src_h,
        )
    else:
        reframe = REFRAME_FILTERS[strategy]
        reframe_segment = reframe(in_label, "[reframed];")
    # 시그니처 카피는 ASS subtitles(libass) 렌더링 — 자간/kerning 자동, 깔끔.
    ass_path = output.with_suffix(".sig.ass")
    write_signature_ass(copy1, copy2, ass_path)
    signature = signature_filter_segment_ass("[reframed]", "[out]", ass_path)
    filter_complex = burn_crop + reframe_segment + signature

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
            "-map", "[out]", "-map", "[aout]",  # video + audio xfade chain
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
        # 디버깅 위해 ass 파일은 실패 시 유지 (성공 시에만 정리).
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}):\n{result.stderr}")

    # 성공 시 임시 ASS 파일 정리.
    if ass_path.exists():
        try:
            ass_path.unlink()
        except OSError as e:
            log.warning("ass.cleanup_failed", path=str(ass_path), error=str(e))

    assert_video_meta(output, expected_dur=duration)
    log.info("ffmpeg.done", output=str(output), duration=duration)
    return output
