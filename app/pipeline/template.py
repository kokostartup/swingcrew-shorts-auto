"""시그니처 9:16 캔버스 합성 (3-zone, 4:5 영상 영역).

캔버스 1080×1920:
- 상단 검정박스 1080×480 (드로우텍스트 2줄, per-glyph 자간 + 인라인 색상)
- 영상 영역  1080×1350 (4:5)
- 하단 padding 1080×90 (Shorts/Reels/TikTok UI overlay 영역)

색상 룰: copy1=흰색, copy2=노랑 (줄 단위 단색).
"""
from collections.abc import Iterable

from app.config import settings

CANVAS_W = 1080
CANVAS_H = 1920
TOP_BAND_H = 480
VIDEO_H = 1350

# 한글 평균 글자 폭 ÷ fontsize (Pretendard-Black 기준 추정).
KOREAN_RATIO = 0.90
ASCII_RATIO = 0.55
SPACE_RATIO = 0.30
QUOTE_RATIO = 0.30
# 자간 (음수 = 좁힘, 폰트 사이즈 대비 비율). 너무 좁히면 답답해 보여서 약하게.
LETTER_SPACING_RATIO = -0.04
# ASCII baseline 보정: drawtext y는 bounding box 상단 기준이라
# 한글(em-square)과 ASCII(cap-height)가 같은 y면 ASCII가 위로 떠 보임.
# Pretendard-Black 기준 fit 값.
ASCII_Y_OFFSET_RATIO = 0.15
# 상단 카피 좌우 여백 (폰트 metric 추정 오차 + 시각적 숨통).
COPY_SIDE_MARGIN = 50
# 상단 카피 가용 폭.
COPY_MAX_WIDTH = CANVAS_W - 2 * COPY_SIDE_MARGIN
COPY_MAX_FONTSIZE = 200
# 첫 줄 y 시작 (상단 검정박스 480 안에서 살짝 내려서 배치).
COPY_Y_TOP = 100

WHITE = "white"
YELLOW = "#FFE500"

# 유니코드 quotation pair (drawtext escape 우회용).
LEFT_QUOTE = "‘"
RIGHT_QUOTE = "’"


def _normalize_quotes(text: str) -> str:
    """ASCII 작은따옴표(') → 유니코드 quotation pair로 자동 변환.

    drawtext의 text='...' 컨텍스트에서 작은따옴표는 escape 불가.
    typographic quotation은 일반 글자로 취급되어 호환.
    홀수번째 ' → '(left), 짝수번째 → '(right).
    """
    result: list[str] = []
    open_q = True
    for c in text:
        if c == "'":
            result.append(LEFT_QUOTE if open_q else RIGHT_QUOTE)
            open_q = not open_q
        else:
            result.append(c)
    return "".join(result)


def _glyph_width_ratio(c: str) -> float:
    if c == " ":
        return SPACE_RATIO
    if c in (LEFT_QUOTE, RIGHT_QUOTE):
        return QUOTE_RATIO
    return KOREAN_RATIO if ord(c) > 127 else ASCII_RATIO


def _text_width(text: str, size: int) -> int:
    """주어진 fontsize에서 자간 포함 텍스트 폭 추정."""
    glyph_total = sum(_glyph_width_ratio(c) for c in text) * size
    spacing_total = LETTER_SPACING_RATIO * size * max(len(text) - 1, 0)
    return int(glyph_total + spacing_total)


def fit_fontsize(text: str, max_width: int = 1000, base_size: int = 88) -> int:
    """단일 라인 fit. 호환성용 (Phase 1 외 단순 케이스)."""
    est = sum(1.0 if ord(c) > 127 else 0.55 for c in text) * base_size
    if est <= max_width:
        return base_size
    return max(int(base_size * max_width / est), 32)


def fit_two_lines_fontsize(
    line1: str,
    line2: str,
    max_width: int = COPY_MAX_WIDTH,
    max_size: int = COPY_MAX_FONTSIZE,
) -> int:
    """두 줄을 같은 fontsize로, 자간 고려해서 더 긴 줄이 max_width에 fit."""
    n1 = _normalize_quotes(line1)
    n2 = _normalize_quotes(line2)
    longer_factor = max(
        sum(_glyph_width_ratio(c) for c in n1)
        + LETTER_SPACING_RATIO * max(len(n1) - 1, 0),
        sum(_glyph_width_ratio(c) for c in n2)
        + LETTER_SPACING_RATIO * max(len(n2) - 1, 0),
    )
    if longer_factor <= 0:
        return max_size
    return max(min(max_size, int(max_width / longer_factor)), 32)


def _escape_drawtext(s: str) -> str:
    """drawtext 컨텍스트에서 문제 일으키는 글자를 escape."""
    return (
        s.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


def _line_colors(text: str, default_color: str) -> Iterable[str]:
    """글자별 색상 — 줄 단위 단색만 (인라인 강조 폐기)."""
    for _ in text:
        yield default_color


def _per_glyph_drawtext_line(
    text: str,
    size: int,
    y: int,
    font_path: str,
    default_color: str,
) -> str:
    """글자 하나하나를 별도 drawtext로 그려 자간 + 인라인 색상 조절.

    text: 유니코드 quotation이 이미 적용된 (normalize 후) 문자열.
    default_color: 라인의 기본 색상 (WHITE 또는 YELLOW).
    """
    spacing = int(LETTER_SPACING_RATIO * size)
    line_width = _text_width(text, size)
    start_x = (CANVAS_W - line_width) // 2

    parts: list[str] = []
    x = start_x
    ascii_offset = int(size * ASCII_Y_OFFSET_RATIO)
    colors = list(_line_colors(text, default_color))
    for c, color in zip(text, colors, strict=True):
        if c == " ":
            x += int(SPACE_RATIO * size) + spacing
            continue
        escaped = _escape_drawtext(c)
        # ASCII (영문/숫자/기호)는 cap-height만 위로 차지하므로 y 보정.
        # 한글, 유니코드 quotation은 em-square 전체를 차지하므로 y 그대로.
        glyph_y = y
        if ord(c) < 128 and c not in (LEFT_QUOTE, RIGHT_QUOTE):
            glyph_y = y + ascii_offset
        parts.append(
            f"drawtext=fontfile='{font_path}':text='{escaped}':"
            f"fontcolor={color}:fontsize={size}:x={x}:y={glyph_y}"
        )
        x += int(_glyph_width_ratio(c) * size) + spacing
    return ",".join(parts)


def signature_filter_segment(
    input_label: str,
    output_label: str,
    copy1: str,
    copy2: str,
) -> str:
    """1080×1350 reframe 영상에 시그니처 캔버스 + 카피 합성.

    copy1: 1줄, 항상 흰색.
    copy2: 2줄, 작은따옴표 있으면 그 안만 노랑(B형), 없으면 전체 노랑(A형).
    """
    if "\n" in copy1 or "\n" in copy2:
        raise ValueError("카피에 줄바꿈은 사용 불가. 각 줄은 별도 인자.")

    copy1 = _normalize_quotes(copy1)
    copy2 = _normalize_quotes(copy2)
    font_path = _escape_drawtext(str(settings.font_path.resolve()))
    size = fit_two_lines_fontsize(copy1, copy2)
    y1 = COPY_Y_TOP
    y2 = y1 + int(size * 1.15)

    line1_filter = _per_glyph_drawtext_line(
        copy1, size, y1, font_path, default_color=WHITE,
    )
    line2_filter = _per_glyph_drawtext_line(
        copy2, size, y2, font_path, default_color=YELLOW,
    )

    return (
        f"{input_label}pad={CANVAS_W}:{CANVAS_H}:0:{TOP_BAND_H}:color=black,"
        f"{line1_filter},{line2_filter}{output_label}"
    )
