"""P시리즈 풀스크린 9:16 렌더러 — framing spec 기반 (영빈 확정 2026-07-09).

golf-framing-director(컷 설계) + golf-subtitle-editor(자막 교정) 에이전트가
만든 spec JSON을 받아 렌더한다. B시리즈는 기존 edit.make_short 경로 유지.

구성: 커버 카드 1.5초 (히어로 프레임 + 로고 + 큰 카피 + BGM 페이드아웃)
→ 본편 풀 9:16 (원본 자막 띠 크롭 제거, 교정 자막 재버닝, 상단 카피 없음).
오디오: 발화는 커버만큼 delay, 커버 동안 합성 리저 BGM (정식 BGM 파일 나오면 교체).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import settings
from app.pipeline.template import _ass_color, _ass_escape_text, _ass_path_for_filter
from app.utils.logger import get_logger
from app.utils.video import assert_video_meta, probe_dimensions

log = get_logger(__name__)

COVER_DUR = 1.5
COVER_FADE = 0.2
SUB_FONTSIZE = 74
SUB_MARGIN_V = 420
SUB_HOLD_MAX = 1.0  # 다음 자막까지 유지 상한 (sec)
COVER_TEXT_MAX_W = 940
COVER_TEXT_MAX_SIZE = 130
COVER_MARGIN_L = 70
COVER_BLOCK_BOTTOM = 1920 - 300  # 그리드 조회수 오버레이 회피
LOGO_PATH = Path(__file__).parents[1] / "assets" / "swingcrew_logo.png"
# 4K 소스 + 야외 잔디/숲은 crf22 기준 비트레이트가 높음 — legacy 30MB/90s 대신 완화.
FULLSCREEN_SIZE_MB_PER_90S = 130.0


class CutSpec(BaseModel):
    """감독 컷 1개. end=None이면 구간 끝까지. cx_end 있으면 선형 패닝."""

    end: float | None = None
    cx: float = Field(ge=0.0, le=1.0)
    cx_end: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str = ""


class SubChunk(BaseModel):
    start: float
    end: float
    text: str


class FramingSpec(BaseModel):
    """golf-framing-director + golf-subtitle-editor 출력 통합 spec.

    시간은 모두 source 초. crop_y0/crop_h는 1080p 기준 단위 (해상도별 자동 스케일).
    cover_lines는 1~3줄, 첫 줄 노랑/나머지 흰색 (seyugolf 레퍼런스 룰).
    """

    crop_y0: int = 0
    crop_h: int = 966
    cuts: list[CutSpec] = Field(min_length=1)
    hero_t: float
    hero_cx: float = Field(ge=0.0, le=1.0)
    cover_lines: list[str] = Field(min_length=1, max_length=3)
    subs: list[SubChunk] = Field(default_factory=list)


def load_framing_spec(internal_id: str) -> FramingSpec:
    path = settings.framing_dir / f"{internal_id}.json"
    return FramingSpec.model_validate_json(path.read_text(encoding="utf-8"))


def _crop_x(cx: float, crop_w: int, src_w: int) -> int:
    x = int(cx * src_w - crop_w / 2)
    return max(0, min(x, src_w - crop_w))


def build_crop_expr(
    cuts: list[CutSpec],
    seg_start: float,
    seg_end: float,
    crop_w: int,
    src_w: int,
) -> str:
    """컷 리스트 → ffmpeg crop x 표현식 (t는 trim 후 0 기준).

    정적 컷은 상수, 패닝(cx_end)은 컷 구간 내 선형 보간. 컷 경계는 하드 컷.
    """
    resolved = [(c.end if c.end is not None else seg_end, c) for c in cuts]
    starts = [seg_start] + [end for end, _ in resolved[:-1]]

    def piece(c: CutSpec, t_start: float, t_end: float) -> str:
        x0 = _crop_x(c.cx, crop_w, src_w)
        if c.cx_end is None:
            return str(x0)
        x1 = _crop_x(c.cx_end, crop_w, src_w)
        a, b = t_start - seg_start, t_end - seg_start
        return f"{x0}+({x1}-{x0})*(t-{a:.3f})/{b - a:.3f}"

    expr = piece(resolved[-1][1], starts[-1], resolved[-1][0])
    for (end, c), t_start in zip(reversed(resolved[:-1]), reversed(starts[:-1]), strict=True):
        expr = f"if(lt(t,{end - seg_start:.3f}),{piece(c, t_start, end)},{expr})"
    return expr


def merge_sub_chunks(
    subs: list[SubChunk],
    seg_start: float,
    dur: float,
) -> list[tuple[float, float, str]]:
    """source 초 → 출력 t 변환 + hold/최소표시 적용. 겹침 방지가 최우선.

    (짧은 청크 연속 시 최소표시 0.9s가 다음 청크를 침범해 두 줄로 쌓였던
    사고 방지 — 겹침 금지 > 최소표시 > hold 순서.)
    """
    base = [
        (max(0.0, c.start - seg_start), min(dur, c.end - seg_start), c.text)
        for c in subs
        if c.start - seg_start < dur and c.end > seg_start
    ]
    merged: list[tuple[float, float, str]] = []
    for i, (s, e, t) in enumerate(base):
        nxt = base[i + 1][0] if i + 1 < len(base) else dur
        desired = max(e, s + 0.9, min(nxt - 0.05, e + SUB_HOLD_MAX))
        merged.append((s, max(min(desired, nxt - 0.05, dur), e), t))
    return merged


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def write_fullscreen_ass(
    subs: list[tuple[float, float, str]],
    ass_path: Path,
) -> None:
    """본편 자막 ASS — 문맥 단위 chunk, 한 줄 ≤13자, 상단 카피 없음 (영빈 지시)."""
    white, black = _ass_color("#FFFFFF"), _ass_color("#000000")
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,Pretendard Black,{SUB_FONTSIZE},{white},{white},{black},{black},"
        f"1,0,0,0,100,100,0,0,1,4,2,2,40,40,{SUB_MARGIN_V},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for s, e, t in subs:
        lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Sub,,0,0,0,,{_ass_escape_text(t)}")
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _logo_white_lineart(height: int):  # noqa: ANN202 — PIL lazy import
    """로고 (검정 라인아트/흰 배경) → 흰 라인 + 투명 배경."""
    from PIL import Image

    src = Image.open(LOGO_PATH).convert("L")
    alpha = src.point(lambda v: 255 - v)
    alpha = alpha.crop(alpha.getbbox())
    w = int(alpha.width * height / alpha.height)
    alpha = alpha.resize((w, height), Image.LANCZOS)
    out = Image.new("RGBA", (w, height), (255, 255, 255, 0))
    out.putalpha(alpha)
    return out


def split_cover_line(text: str, max_chars: int = 10) -> list[str]:
    """max_chars 초과 카피를 공백 경계(중앙 근처)에서 분할. 커버 큰 글자용."""
    if len(text) <= max_chars or " " not in text:
        return [text]
    mid = len(text) / 2
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    best = min(spaces, key=lambda i: abs(i - mid))
    return [text[:best], text[best + 1 :]]


def render_cover_png(
    video_path: Path,
    spec: FramingSpec,
    crop_w: int,
    crop_h: int,
    crop_y0: int,
    src_w: int,
    png_path: Path,
) -> None:
    """커버 카드 PIL 합성 — 히어로 프레임 + 그라데이션 dim + 로고 락업 + 큰 카피.

    seyugolf 레퍼런스: 하단 1/3 좌측 정렬, 첫 줄 노랑/나머지 흰색, 아웃라인 없음
    (dim이 대비 담당), 모든 줄 동일 fontsize.
    """
    from PIL import Image, ImageDraw, ImageFont

    hero_raw = png_path.with_suffix(".hero.png")
    x = _crop_x(spec.hero_cx, crop_w, src_w)
    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(spec.hero_t),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"crop={crop_w}:{crop_h}:{x}:{crop_y0},scale=1080:1920,setsar=1",
            str(hero_raw),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"hero frame extract failed:\n{r.stderr[-2000:]}")

    img = Image.open(hero_raw).convert("RGBA")
    # 세로 그라데이션 dim — 텍스트 존(하단)일수록 어둡게
    rows = bytearray()
    for y in range(1920):
        rows += bytes([45 + int(125 * (y / 1919) ** 1.4)]) * 1080
    shade = Image.new("RGBA", (1080, 1920), (0, 0, 0, 255))
    shade.putalpha(Image.frombytes("L", (1080, 1920), bytes(rows)))
    img.alpha_composite(shade)

    def fit(text: str, size: int) -> float:
        return ImageFont.truetype(str(settings.font_path), size).getlength(text)

    size = COVER_TEXT_MAX_SIZE
    while size > 60 and any(fit(t, size) > COVER_TEXT_MAX_W for t in spec.cover_lines):
        size -= 2
    font = ImageFont.truetype(str(settings.font_path), size)
    line_h = int(size * 1.22)
    block_top = COVER_BLOCK_BOTTOM - line_h * len(spec.cover_lines)

    logo_h = 88
    logo = _logo_white_lineart(logo_h)
    logo_y = block_top - logo_h - 44
    img.alpha_composite(logo, (COVER_MARGIN_L, logo_y))
    draw = ImageDraw.Draw(img)
    wm_font = ImageFont.truetype(str(settings.font_path), 46)
    draw.text(
        (COVER_MARGIN_L + logo.width + 24, logo_y + logo_h / 2),
        "스윙크루",
        font=wm_font,
        fill=(255, 255, 255, 235),
        anchor="lm",
    )
    yellow, white = (255, 229, 0), (255, 255, 255)
    for i, text in enumerate(spec.cover_lines):
        color = yellow if i == 0 else white
        draw.text((COVER_MARGIN_L, block_top + i * line_h), text, font=font, fill=color)

    img.convert("RGB").save(png_path)
    hero_raw.unlink(missing_ok=True)


def render_fullscreen(
    video_path: Path,
    spec: FramingSpec,
    start_sec: float,
    end_sec: float,
    output: Path,
) -> None:
    """풀스크린 9:16 렌더 — 커버 1.5초 freeze + 본편. 완료 후 메타 검증."""
    dur = end_sec - start_sec
    src_w, src_h = probe_dimensions(video_path)
    scale = src_h / 1080
    crop_h = int(spec.crop_h * scale)
    crop_y0 = int(spec.crop_y0 * scale)
    crop_w = int(crop_h * 9 / 16)

    output.parent.mkdir(parents=True, exist_ok=True)
    ass_path = output.with_suffix(".sub.ass")
    write_fullscreen_ass(merge_sub_chunks(spec.subs, start_sec, dur), ass_path)
    cover_png = output.with_suffix(".cover.png")
    render_cover_png(video_path, spec, crop_w, crop_h, crop_y0, src_w, cover_png)

    expr = build_crop_expr(spec.cuts, start_sec, end_sec, crop_w, src_w)
    ass_esc = _ass_path_for_filter(ass_path)
    fonts_esc = _ass_path_for_filter(str(settings.font_path.parent.resolve()))
    delay_ms = int(COVER_DUR * 1000)
    fc = (
        f"[0:v]crop={crop_w}:{crop_h}:x='{expr}':y={crop_y0},"
        f"scale=1080:1920,setsar=1,"
        f"subtitles=filename='{ass_esc}':fontsdir='{fonts_esc}',"
        f"tpad=start_duration={COVER_DUR}:start_mode=clone[main];"
        f"[1:v]setsar=1,fade=out:st={COVER_DUR - COVER_FADE}:d={COVER_FADE}:alpha=1[cover];"
        f"[main][cover]overlay=0:0:enable='lt(t,{COVER_DUR})'[vout];"
        f"[0:a]pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
        f"adelay={delay_ms}:all=1[speech];"
        # TODO: 영빈 정식 인트로 BGM 파일 받으면 anoisesrc 대신 파일 입력으로 교체
        f"anoisesrc=d={COVER_DUR + 0.25}:color=pink:amplitude=0.28,"
        f"highpass=f=180,lowpass=f=3500,"
        f"afade=t=in:st=0:d=0.15,afade=t=out:st={COVER_DUR - 0.45:.2f}:d=0.7,"
        f"aformat=sample_rates=44100:channel_layouts=stereo[bgm];"
        f"[speech][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start_sec),
        "-t",
        f"{dur:.3f}",
        "-i",
        str(video_path),
        "-loop",
        "1",
        "-t",
        str(COVER_DUR),
        "-i",
        str(cover_png),
        "-filter_complex",
        fc,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-r",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    for tmp in (ass_path, cover_png):
        tmp.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"fullscreen ffmpeg failed:\n{result.stderr[-3000:]}")

    assert_video_meta(
        output,
        expected_dur=dur + COVER_DUR,
        size_mb_per_90s=FULLSCREEN_SIZE_MB_PER_90S,
    )
    log.info(
        "fullscreen.rendered",
        output=str(output),
        dur=round(dur + COVER_DUR, 2),
        size_mb=round(output.stat().st_size / 1024 / 1024, 1),
        cuts=len(spec.cuts),
        subs=len(spec.subs),
    )


__all__ = [
    "COVER_DUR",
    "CutSpec",
    "FramingSpec",
    "SubChunk",
    "build_crop_expr",
    "load_framing_spec",
    "merge_sub_chunks",
    "render_fullscreen",
    "split_cover_line",
]
