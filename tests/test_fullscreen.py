"""fullscreen.py 순수 함수 유닛 테스트 — crop expr / 자막 merge / 커버 줄 분할."""

from __future__ import annotations

import pytest

from app.pipeline.fullscreen import (
    CutSpec,
    FramingSpec,
    SubChunk,
    build_crop_expr,
    merge_sub_chunks,
    split_cover_line,
)


def _x(cx: float, crop_w: int = 543, src_w: int = 1920) -> int:
    return max(0, min(int(cx * src_w - crop_w / 2), src_w - crop_w))


class TestBuildCropExpr:
    def test_single_static_cut(self):
        expr = build_crop_expr([CutSpec(end=None, cx=0.5)], 100.0, 140.0, 543, 1920)
        assert expr == str(_x(0.5))

    def test_multi_cut_boundaries_relative_to_seg_start(self):
        cuts = [CutSpec(end=110.0, cx=0.4), CutSpec(end=None, cx=0.6)]
        expr = build_crop_expr(cuts, 100.0, 140.0, 543, 1920)
        # 경계는 trim 후 t 기준 → 110-100=10.000
        assert "lt(t,10.000)" in expr
        assert str(_x(0.4)) in expr
        assert str(_x(0.6)) in expr

    def test_pan_linear_interpolation(self):
        cuts = [CutSpec(end=None, cx=0.2, cx_end=0.8)]
        expr = build_crop_expr(cuts, 0.0, 10.0, 543, 1920)
        x0, x1 = _x(0.2), _x(0.8)
        assert expr == f"{x0}+({x1}-{x0})*(t-0.000)/10.000"

    def test_crop_x_clamped_to_frame(self):
        # cx=0.0 → x는 0 미만이 될 수 없고, cx=1.0 → src_w - crop_w 초과 불가
        expr_left = build_crop_expr([CutSpec(end=None, cx=0.0)], 0, 10, 543, 1920)
        expr_right = build_crop_expr([CutSpec(end=None, cx=1.0)], 0, 10, 543, 1920)
        assert expr_left == "0"
        assert expr_right == str(1920 - 543)


class TestMergeSubChunks:
    def test_no_overlap_even_with_short_chunks(self):
        # 짧은 청크 연속 — 최소표시 0.9s가 다음 청크를 침범하면 두 줄 스택 버그
        subs = [
            SubChunk(start=10.0, end=10.4, text="a"),
            SubChunk(start=10.5, end=10.9, text="b"),
            SubChunk(start=11.0, end=11.4, text="c"),
        ]
        merged = merge_sub_chunks(subs, 10.0, 30.0)
        for (_, e_prev, _), (s_next, _, _) in zip(merged, merged[1:], strict=False):
            assert e_prev < s_next

    def test_hold_until_next_capped(self):
        subs = [
            SubChunk(start=10.0, end=11.0, text="a"),
            SubChunk(start=20.0, end=21.0, text="b"),
        ]
        merged = merge_sub_chunks(subs, 10.0, 30.0)
        # 다음 청크까지 9초 gap — hold는 SUB_HOLD_MAX(1s)까지만
        assert merged[0][1] == pytest.approx(2.0, abs=0.01)

    def test_source_time_shifted_to_output_t(self):
        subs = [SubChunk(start=105.0, end=106.0, text="a")]
        merged = merge_sub_chunks(subs, 100.0, 40.0)
        assert merged[0][0] == pytest.approx(5.0)

    def test_chunk_outside_segment_dropped(self):
        subs = [SubChunk(start=200.0, end=201.0, text="out")]
        assert merge_sub_chunks(subs, 100.0, 40.0) == []


class TestSplitCoverLine:
    def test_short_line_unchanged(self):
        assert split_cover_line("이거 들으면") == ["이거 들으면"]

    def test_long_line_split_near_middle_space(self):
        assert split_cover_line("볼스피드 78.8 찍었습니다") == [
            "볼스피드 78.8",
            "찍었습니다",
        ]

    def test_no_space_long_line_unchanged(self):
        assert split_cover_line("가나다라마바사아자차카타파") == ["가나다라마바사아자차카타파"]


class TestFramingSpec:
    def test_parses_full_spec(self):
        spec = FramingSpec.model_validate(
            {
                "cuts": [
                    {"end": 274.0, "cx": 0.5, "note": "풍경"},
                    {"end": None, "cx": 0.48},
                ],
                "hero_t": 262.0,
                "hero_cx": 0.5,
                "cover_lines": ["뷰는 천국인데", "공략은 지옥입니다"],
                "subs": [{"start": 242.5, "end": 244.0, "text": "코스는 골프한테"}],
            }
        )
        assert spec.crop_h == 966  # 기본값 (1080p 기준 하단 자막 띠 회피)
        assert spec.cuts[1].end is None

    def test_rejects_empty_cuts(self):
        with pytest.raises(ValueError):
            FramingSpec.model_validate(
                {"cuts": [], "hero_t": 0, "hero_cx": 0.5, "cover_lines": ["a"]}
            )
