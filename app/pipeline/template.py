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

# 한글 평균 글자 폭 ÷ fontsize (Pretendard-Black, ASS libass rendering 기준).
# 이전 drawtext 추정값(0.90)이 ASS 실제 너비(~0.60)보다 크게 잡혀 fit fontsize가
# 보수적으로 결정됨 → 양옆 여유 과다 (P005 사례). ASS 실측 기준으로 calibrate.
KOREAN_RATIO = 0.62
ASCII_RATIO = 0.42
SPACE_RATIO = 0.22
QUOTE_RATIO = 0.22
# ASS Spacing=0 (libass 폰트 metric 기반 자연 자간) → fit 식에서도 추가 spacing 없음.
LETTER_SPACING_RATIO = 0.0
# ASS는 글리프 metric 기반이라 한글<->ASCII 경계도 자연스러움. 추가 padding 불필요.
BOUNDARY_PADDING_RATIO = 0.0
# ASCII baseline 보정: drawtext y는 bounding box 상단 기준이라
# 한글(em-square)과 ASCII(cap-height)가 같은 y면 ASCII가 위로 떠 보임.
# Pretendard-Black 기준 fit 값.
ASCII_Y_OFFSET_RATIO = 0.15
# 상단 카피 좌우 여백 (폰트 metric 추정 오차 + 시각적 숨통).
COPY_SIDE_MARGIN = 50
# 상단 카피 가용 폭.
COPY_MAX_WIDTH = CANVAS_W - 2 * COPY_SIDE_MARGIN
COPY_MAX_FONTSIZE = 200
# 두 줄 텍스트를 검정박스 하단 (영상 영역 바로 위)에 정렬할 때 검정박스 끝~둘째줄 baseline까지 여백.
COPY_BOTTOM_MARGIN = 40

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
    # '%'는 ASCII지만 Pretendard-Black에서 글리프 시각 폭이 한글급. ASCII_RATIO로
    # 잡으면 다음 글자가 너무 붙음 (P003-S02 "60%만" 케이스). 한글로 취급.
    if c == "%":
        return KOREAN_RATIO
    return KOREAN_RATIO if ord(c) > 127 else ASCII_RATIO


def _is_korean_group(c: str) -> bool:
    """boundary padding 판정 — 한글 + '%'가 한글 그룹. ASCII 알파벳/숫자는 별도 그룹.

    그룹 경계(한글<->ASCII)에 padding 추가로 ASCII 글자가 한글에 너무 붙는 케이스 보정.
    """
    if c == "%":
        return True
    if c in (LEFT_QUOTE, RIGHT_QUOTE):
        return True
    return ord(c) > 127


def _count_boundaries(text: str) -> int:
    """한글<->ASCII 그룹 경계 개수 (공백은 그룹 구분 reset)."""
    count = 0
    prev_group: bool | None = None
    for c in text:
        if c == " ":
            prev_group = None
            continue
        cur_group = _is_korean_group(c)
        if prev_group is not None and prev_group != cur_group:
            count += 1
        prev_group = cur_group
    return count


def _text_width(text: str, size: int) -> int:
    """주어진 fontsize에서 자간 포함 텍스트 폭 추정."""
    glyph_total = sum(_glyph_width_ratio(c) for c in text) * size
    spacing_total = LETTER_SPACING_RATIO * size * max(len(text) - 1, 0)
    boundary_padding = _count_boundaries(text) * size * BOUNDARY_PADDING_RATIO
    return int(glyph_total + spacing_total + boundary_padding)


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
        + LETTER_SPACING_RATIO * max(len(n1) - 1, 0)
        + _count_boundaries(n1) * BOUNDARY_PADDING_RATIO,
        sum(_glyph_width_ratio(c) for c in n2)
        + LETTER_SPACING_RATIO * max(len(n2) - 1, 0)
        + _count_boundaries(n2) * BOUNDARY_PADDING_RATIO,
    )
    if longer_factor <= 0:
        return max_size
    return max(min(max_size, int(max_width / longer_factor)), 32)


def _escape_drawtext(s: str) -> str:
    """drawtext 컨텍스트에서 문제 일으키는 글자를 escape.

    per-glyph drawtext에는 expansion=none 옵션 적용 → '%' 단독도 literal로 처리되어
    별도 escape 불필요. backslash/colon은 single-quote 안이라 ffmpeg가 literal로 처리.
    %를 fullwidth '％'로 대체했더니 Pretendard에서 작게 렌더링되어 자간 어색
    (영빈 P003-S02 사례) — expansion=none + ASCII % 가 가장 깔끔.
    font_path 등 path 인자에는 Windows 경로 escape가 필요해서 backslash/colon은 유지.
    """
    return (
        s.replace("\\", "\\\\")
        .replace(":", r"\:")
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

    advance를 float로 누적 — int truncation 누적 방지 (P003 자간 균일성 fix).
    """
    spacing = LETTER_SPACING_RATIO * size  # float spacing
    line_width = _text_width(text, size)
    start_x = (CANVAS_W - line_width) // 2

    parts: list[str] = []
    x_f = float(start_x)
    ascii_offset = int(size * ASCII_Y_OFFSET_RATIO)
    boundary_padding = size * BOUNDARY_PADDING_RATIO
    colors = list(_line_colors(text, default_color))
    prev_group: bool | None = None
    for c, color in zip(text, colors, strict=True):
        if c == " ":
            x_f += SPACE_RATIO * size + spacing
            prev_group = None
            continue
        # 한글<->ASCII 그룹 경계에 padding 추가 (시각적으로 안 닿게).
        cur_group = _is_korean_group(c)
        if prev_group is not None and prev_group != cur_group:
            x_f += boundary_padding
        escaped = _escape_drawtext(c)
        glyph_y = y
        # ASCII baseline offset 적용 (drawtext y가 bbox 상단 → ASCII가 위로 떠 보이는 보정).
        # '%'는 너비는 한글급(_glyph_width_ratio)이지만 baseline은 숫자와 같은 줄에 와야
        # "100%" 같이 ASCII 연속에서 자연스러움 (영빈 기존 작품 레퍼런스 기준).
        if ord(c) < 128 and c not in (LEFT_QUOTE, RIGHT_QUOTE):
            glyph_y = y + ascii_offset
        parts.append(
            f"drawtext=fontfile='{font_path}':text='{escaped}':"
            f"fontcolor={color}:fontsize={size}:x={int(x_f)}:y={glyph_y}:"
            f"expansion=none"
        )
        x_f += _glyph_width_ratio(c) * size + spacing
        prev_group = cur_group
    return ",".join(parts)


def signature_filter_segment(
    input_label: str,
    output_label: str,
    copy1: str,
    copy2: str,
) -> str:
    """1080×1350 reframe 영상에 시그니처 캔버스 + 카피 합성 — drawtext 기반 (legacy).

    copy1: 1줄, 항상 흰색.
    copy2: 2줄, 작은따옴표 있으면 그 안만 노랑(B형), 없으면 전체 노랑(A형).

    ASS 기반 signature_filter_segment_ass(libass)가 출시되어 typography 품질이 더 좋음.
    이 함수는 후방 호환용으로 유지 — 신규 호출은 ASS 흐름 사용 권장.
    """
    if "\n" in copy1 or "\n" in copy2:
        raise ValueError("카피에 줄바꿈은 사용 불가. 각 줄은 별도 인자.")

    copy1 = _normalize_quotes(copy1)
    copy2 = _normalize_quotes(copy2)
    font_path = _escape_drawtext(str(settings.font_path.resolve()))
    size = fit_two_lines_fontsize(copy1, copy2)
    line_height = int(size * 1.15)
    # 둘째 줄 baseline (= 그 줄 y) 이 검정박스 하단에서 COPY_BOTTOM_MARGIN + size 위에.
    # 영빈 요구: 카피가 영상 영역 바로 위에 위치 (검정박스 하단 정렬).
    y2 = TOP_BAND_H - COPY_BOTTOM_MARGIN - size
    y1 = y2 - line_height

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


# ─────────────────────────────── ASS (libass) signature ───────────────────────────────

def _ass_color(hex_rgb: str) -> str:
    """'#FFE500' → '&H0000E5FF' (ASS BGR 형식)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_escape_text(text: str) -> str:
    """ASS Dialogue text 안의 특수문자 escape. Comma는 dialogue field separator라 escape."""
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def _ass_fit_single_line(
    text: str,
    max_width: int = COPY_MAX_WIDTH,
    max_size: int = COPY_MAX_FONTSIZE,
) -> int:
    """단일 줄이 max_width 안에 fit하는 최대 fontsize (PIL getlength 기준)."""
    from PIL import ImageFont

    font_path = str(settings.font_path.resolve())
    lo, hi = 32, max_size
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            font = ImageFont.truetype(font_path, mid)
        except Exception:
            return lo
        if font.getlength(text) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _ass_fit_fontsize(
    copy1: str,
    copy2: str,
    max_width: int = COPY_MAX_WIDTH,
    max_size: int = COPY_MAX_FONTSIZE,
) -> int:
    """두 줄 동일 fontsize fit (legacy — drawtext signature_filter_segment용)."""
    return min(
        _ass_fit_single_line(copy1, max_width, max_size),
        _ass_fit_single_line(copy2, max_width, max_size),
    )


def write_signature_ass(
    copy1: str,
    copy2: str,
    ass_path: object,  # Path
) -> None:
    """시그니처 카피 2줄 ASS subtitle 파일 작성 — libass가 자간/kerning 자동 처리.

    파일 구조:
    - PlayResX=1080, PlayResY=1920 (영상 캔버스와 동일 좌표)
    - Style: Copy1 (흰색), Copy2 (노랑) — Bold, 자간 0 (libass 폰트 metric 따름)
    - Alignment 8 (top-center), MarginV로 검정박스 하단 정렬
    - WrapStyle 2: no auto-wrap (이미 두 줄 분리)

    카피는 검정 박스(1080×TOP_BAND_H) 하단에 정렬 — 영상 영역 바로 위.
    """
    from pathlib import Path
    p = ass_path if isinstance(ass_path, Path) else Path(str(ass_path))

    if "\n" in copy1 or "\n" in copy2:
        raise ValueError("카피에 줄바꿈은 사용 불가. 각 줄은 별도 인자.")

    n1 = _normalize_quotes(copy1)
    n2 = _normalize_quotes(copy2)
    # copy1, copy2 각자 독립 fit — 짧은 줄은 더 크게, 긴 줄은 자기 max에 맞춰서.
    size1 = _ass_fit_single_line(n1)
    size2 = _ass_fit_single_line(n2)
    line1_height = int(size1 * 1.15)
    line2_height = int(size2 * 1.15)
    # 검정박스(높이 TOP_BAND_H) 하단에서 COPY_BOTTOM_MARGIN 띄우고 copy2가 위치.
    # ASS alignment 8 = top-center, MarginV = text top edge부터 PlayResY top까지.
    copy2_top = TOP_BAND_H - COPY_BOTTOM_MARGIN - line2_height
    copy1_top = copy2_top - line1_height

    primary_white = _ass_color("#FFFFFF")
    primary_yellow = _ass_color("#FFE500")
    outline_color = _ass_color("#000000")

    e1 = _ass_escape_text(n1)
    e2 = _ass_escape_text(n2)

    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {CANVAS_W}\n"
        f"PlayResY: {CANVAS_H}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Copy1,Pretendard Black,{size1},{primary_white},{primary_white},"
        f"{outline_color},{outline_color},1,0,0,0,100,100,0,0,1,0,0,"
        f"8,{COPY_SIDE_MARGIN},{COPY_SIDE_MARGIN},{copy1_top},1\n"
        f"Style: Copy2,Pretendard Black,{size2},{primary_yellow},{primary_yellow},"
        f"{outline_color},{outline_color},1,0,0,0,100,100,0,0,1,0,0,"
        f"8,{COPY_SIDE_MARGIN},{COPY_SIDE_MARGIN},{copy2_top},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,9:59:59.99,Copy1,,0,0,0,,{e1}\n"
        f"Dialogue: 0,0:00:00.00,9:59:59.99,Copy2,,0,0,0,,{e2}\n"
    )
    p.write_text(content, encoding="utf-8-sig")


def _ass_path_for_filter(ass_path: object) -> str:
    """ffmpeg subtitles filter용 path escape — colon/backslash 처리."""
    s = str(ass_path).replace("\\", "/")
    # Windows drive colon `C:` → `C\:` (ffmpeg filter colon escape)
    if len(s) >= 2 and s[1] == ":":
        s = s[0] + r"\:" + s[2:]
    return s


def signature_filter_segment_ass(
    input_label: str,
    output_label: str,
    ass_path: object,
) -> str:
    """drawbox + subtitles(libass) 기반 시그니처 합성 — ASS 파일 미리 작성 필요.

    pad로 검정 박스(1080×TOP_BAND_H) 위쪽 채움 + 영상 영역 1080×VIDEO_H 아래.
    그 위에 libass로 카피 두 줄 렌더링 — 폰트 metric 기반 자연스러운 자간/baseline.
    """
    ass_esc = _ass_path_for_filter(ass_path)
    fonts_dir = settings.font_path.parent.resolve()
    fonts_esc = _ass_path_for_filter(str(fonts_dir))
    return (
        f"{input_label}pad={CANVAS_W}:{CANVAS_H}:0:{TOP_BAND_H}:color=black,"
        f"subtitles=filename='{ass_esc}':fontsdir='{fonts_esc}'{output_label}"
    )
