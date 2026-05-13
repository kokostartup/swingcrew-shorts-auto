"""Phase 1 + 1.5 시그니처 레이아웃 + reframe 엔진 테스트."""
from pathlib import Path

import pytest

from app.pipeline.edit import (
    REFRAME_FILTERS,
    _face_centered_4_5,
    _letterbox_4_5,
    _split_left,
    _split_right,
    _talking_head_crop_static,
    make_short,
)
from app.pipeline.template import (
    LEFT_QUOTE,
    RIGHT_QUOTE,
    YELLOW,
    _escape_drawtext,
    _line_colors,
    _normalize_quotes,
    fit_fontsize,
    fit_two_lines_fontsize,
    signature_filter_segment,
)
from app.utils.video import assert_video_meta

SAMPLE_VIDEO = Path("data/samples/1wwEY0KEkoA.mp4")


# ----- Fast unit tests -----


def test_fit_fontsize_short_returns_base() -> None:
    assert fit_fontsize("짧은") == 88


def test_fit_fontsize_long_korean_shrinks() -> None:
    long = "아주아주아주아주아주아주아주아주아주아주아주아주긴 텍스트"
    result = fit_fontsize(long)
    assert result < 88
    assert result >= 32


def test_fit_fontsize_returns_at_least_32() -> None:
    very_long = "가" * 200
    assert fit_fontsize(very_long) >= 32


def test_fit_two_lines_uses_longer_line() -> None:
    short_long = fit_two_lines_fontsize("짧음", "아주아주아주아주긴 텍스트입니다")
    short_short = fit_two_lines_fontsize("짧음", "짧음")
    assert short_long <= short_short
    assert short_long >= 32


def test_fit_two_lines_returns_max_when_both_short() -> None:
    assert fit_two_lines_fontsize("짧음", "짧음", max_size=120) == 120


def test_normalize_quotes_alternates_pair() -> None:
    assert _normalize_quotes("'수직낙하'") == f"{LEFT_QUOTE}수직낙하{RIGHT_QUOTE}"


def test_normalize_quotes_multiple_pairs() -> None:
    out = _normalize_quotes("a 'one' b 'two' c")
    assert out == f"a {LEFT_QUOTE}one{RIGHT_QUOTE} b {LEFT_QUOTE}two{RIGHT_QUOTE} c"


def test_line_colors_no_quotes_uses_default() -> None:
    text = "이 영상만 보세요"
    colors = list(_line_colors(text, default_color=YELLOW))
    assert all(c == YELLOW for c in colors)


def test_line_colors_with_quotes_still_uniform() -> None:
    """인라인 강조 폐기 — 따옴표가 있어도 줄 전체가 단색."""
    text = f"{LEFT_QUOTE}수직낙하{RIGHT_QUOTE} 연습"
    colors = list(_line_colors(text, default_color=YELLOW))
    assert all(c == YELLOW for c in colors)


def test_escape_drawtext_backslash_first() -> None:
    assert _escape_drawtext(r"C:\users") == r"C\:\\users"


def test_escape_drawtext_colon() -> None:
    assert _escape_drawtext("프로 vs 아마: 차이") == r"프로 vs 아마\: 차이"


def test_letterbox_filter_segment_format() -> None:
    f = _letterbox_4_5("[0:v]", "[reframed];")
    assert f == (
        "[0:v]scale=1080:1350:force_original_aspect_ratio=increase,"
        "crop=1080:1350[reframed];"
    )


def test_talking_head_filter_segment_format() -> None:
    f = _talking_head_crop_static("[0:v]", "[reframed];")
    assert f == (
        "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        "scale=1080:1350:force_original_aspect_ratio=increase,"
        "crop=1080:1350[reframed];"
    )


def test_split_right_filter_segment_format() -> None:
    f = _split_right("[0:v]", "[reframed];")
    assert f == (
        "[0:v]crop=ih*9/16:ih:iw-ih*9/16:0,"
        "scale=1080:1350:force_original_aspect_ratio=increase,"
        "crop=1080:1350[reframed];"
    )


def test_split_left_filter_segment_format() -> None:
    f = _split_left("[0:v]", "[reframed];")
    assert f == (
        "[0:v]crop=ih*9/16:ih:0:0,"
        "scale=1080:1350:force_original_aspect_ratio=increase,"
        "crop=1080:1350[reframed];"
    )


def test_reframe_filters_registry_complete() -> None:
    """face_centered_4_5는 dynamic (cx 인자)이라 registry 밖 — 분기 처리."""
    assert set(REFRAME_FILTERS.keys()) == {
        "letterbox_4_5", "talking_head_crop_static",
        "split_right", "split_left",
    }


def test_face_centered_4_5_computes_pixel_crop() -> None:
    """1920×1080, cx=0.7 → crop_w=864, crop_x=0.7*1920-432=912."""
    f = _face_centered_4_5("[0:v]", "[reframed];", 0.7, 1920, 1080)
    assert "crop=864:1080:912:0" in f


def test_face_centered_4_5_clamps_to_left_edge() -> None:
    """cx=0.0 또는 음수 → crop_x=0 (좌측 경계)."""
    f = _face_centered_4_5("[0:v]", "[reframed];", -0.3, 1920, 1080)
    assert "crop=864:1080:0:0" in f


def test_face_centered_4_5_clamps_to_right_edge() -> None:
    """cx=1.0 → crop_x = src_w - crop_w = 1920 - 864 = 1056."""
    f = _face_centered_4_5("[0:v]", "[reframed];", 1.0, 1920, 1080)
    assert "crop=864:1080:1056:0" in f


def test_signature_filter_contains_pad_and_colors() -> None:
    f = signature_filter_segment(
        "[reframed]", "[out]", "테스트 카피", "강조"
    )
    assert "pad=1080:1920:0:480:color=black" in f
    assert "fontcolor=white" in f
    assert "fontcolor=#FFE500" in f
    assert "[out]" in f


def test_signature_filter_copy1_all_white() -> None:
    """copy1 글자별 drawtext는 모두 fontcolor=white (줄 단위 단색)."""
    import re

    f = signature_filter_segment(
        "[reframed]", "[out]",
        copy1="흰색 윗줄",
        copy2="노랑 아래줄",
    )
    # copy1 글자만 추출 (y 좌표가 더 작은 쪽 = 첫째 줄)
    pattern = re.compile(
        r"text='(?P<c>[^']+)':fontcolor=(?P<col>[^:]+):"
        r"fontsize=\d+:x=\d+:y=(?P<y>\d+)"
    )
    matches = [m.groupdict() for m in pattern.finditer(f)]
    y_values = sorted({int(m["y"]) for m in matches})
    line1_y = y_values[0]
    line1_colors = {m["col"] for m in matches if int(m["y"]) == line1_y}
    # ASCII baseline 보정 때문에 한글 라인의 정확한 y 매치만 (보정값 미포함)
    # → line1의 한글들이 같은 y. 그 y에 속한 색은 모두 white.
    assert line1_colors == {"white"}


def test_signature_filter_copy2_all_yellow_even_with_quotes() -> None:
    """copy2는 따옴표가 들어와도 줄 전체 노랑 (인라인 강조 폐기)."""
    import re

    f = signature_filter_segment(
        "[reframed]", "[out]",
        copy1="윗줄 카피",
        copy2="'키워드' 강조 폐기",
    )
    pattern = re.compile(
        r"text='(?P<c>[^']+)':fontcolor=(?P<col>[^:]+):"
        r"fontsize=\d+:x=\d+:y=(?P<y>\d+)"
    )
    matches = [m.groupdict() for m in pattern.finditer(f)]
    y_values = sorted({int(m["y"]) for m in matches})
    line2_y = y_values[-1]
    line2_colors = {m["col"] for m in matches if int(m["y"]) == line2_y}
    assert line2_colors == {"#FFE500"}


def test_ascii_glyph_y_offset_relative_to_korean() -> None:
    """ASCII 글자는 한글 baseline에 맞추기 위해 y가 더 큼 (아래로 보정)."""
    import re

    from app.pipeline.template import ASCII_Y_OFFSET_RATIO

    f = signature_filter_segment(
        "[reframed]", "[out]", copy1="A 가", copy2="B",
    )
    pattern = re.compile(
        r"text='(?P<c>[^']+)':fontcolor=[^:]+:fontsize=(?P<s>\d+):"
        r"x=\d+:y=(?P<y>\d+)"
    )
    y_by_char: dict[str, tuple[int, int]] = {}
    for m in pattern.finditer(f):
        y_by_char[m.group("c")] = (int(m.group("y")), int(m.group("s")))
    y_a, size = y_by_char["A"]
    y_korean, _ = y_by_char["가"]
    assert y_a == y_korean + int(size * ASCII_Y_OFFSET_RATIO)


def test_korean_glyphs_share_same_y() -> None:
    """한글 글자끼리는 동일한 y (베이스라인 동일)."""
    import re

    f = signature_filter_segment(
        "[reframed]", "[out]", copy1="가나다", copy2="라마",
    )
    pattern = re.compile(
        r"text='(?P<c>[^']+)':fontcolor=[^:]+:fontsize=\d+:x=\d+:y=(?P<y>\d+)"
    )
    y_by_char: dict[str, int] = {}
    for m in pattern.finditer(f):
        y_by_char[m.group("c")] = int(m.group("y"))
    assert y_by_char["가"] == y_by_char["나"] == y_by_char["다"]


def test_signature_filter_normalizes_ascii_quotes() -> None:
    # 입력에 ASCII ' → 유니코드 quotation pair
    f = signature_filter_segment(
        "[reframed]", "[out]",
        copy1="골프 스윙",
        copy2="'수직낙하'",
    )
    # 결과 필터에는 typographic quotation
    assert LEFT_QUOTE in f or RIGHT_QUOTE in f
    # ASCII ' 는 drawtext text=' 내에서만 (구문) 등장. 텍스트 자체엔 없음.


# ----- Slow integration tests -----


@pytest.mark.slow
@pytest.mark.skipif(not SAMPLE_VIDEO.exists(), reason="샘플 영상 없음")
def test_make_short_letterbox_produces_valid_output(tmp_path: Path) -> None:
    output = tmp_path / "letterbox.mp4"
    result = make_short(
        src=SAMPLE_VIDEO, start=30, end=33,
        strategy="letterbox_4_5",
        copy1="테스트 카피", copy2="강조 단어",
        output=output,
    )
    assert result.exists()
    assert_video_meta(result, expected_dur=3.0)


@pytest.mark.slow
@pytest.mark.skipif(not SAMPLE_VIDEO.exists(), reason="샘플 영상 없음")
def test_make_short_talking_head_produces_valid_output(tmp_path: Path) -> None:
    output = tmp_path / "talking_head.mp4"
    result = make_short(
        src=SAMPLE_VIDEO, start=30, end=33,
        strategy="talking_head_crop_static",
        copy1="테스트 카피", copy2="강조 단어",
        output=output,
    )
    assert result.exists()
    assert_video_meta(result, expected_dur=3.0)
