"""1회용: 26-P020-S06으로 새 포맷 통합 샘플 생성.

새 포맷 요소:
  1. 풀 캔버스 1080×1920 (상단 검정 박스 제거)
  2. blur padding (letterbox 대체)
  3. 인트로 텍스트 (copy1/copy2, 1.5초 + 0.3초 fade out, 화면 하단 중앙)
  4. 우상단 placeholder 로고 ("SwingCrew" 텍스트)
  5. 발화 자막 burn-in (WhisperX 전사 → ASS)

출력: outputs/shorts/26-P020-S06_fullformat_sample.mp4
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.utils.video import detect_hwaccel

INTERNAL_ID = "26-P020-S06"
OUTPUT = Path("outputs/shorts/26-P020-S06_fullformat_sample.mp4")
CANVAS_W = 1080
CANVAS_H = 1920

# ── 인트로 ──
INTRO_END = 1.5  # 끝나는 시각 (초)
INTRO_FADE_MS = 300  # fade out duration (ms)
INTRO_COPY1_SIZE = 110
INTRO_COPY2_SIZE = 110
INTRO_COPY1_COLOR = "&H0000FFFF"  # ASS BGR: yellow
INTRO_COPY2_COLOR = "&H00FFFFFF"  # white

# ── 로고 ──
LOGO_TEXT = "SwingCrew"
LOGO_SIZE = 44

# ── 자막 ──
SUBTITLE_SIZE = 56
SUBTITLE_MAX_CHARS = 14  # 한 줄 최대 글자수 (대략)


def _sec_to_ass(t: float) -> str:
    """0:00:00.00 형식."""
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _split_lines(words: list[dict], max_chars: int) -> list[tuple[float, float, str]]:
    """words 리스트 → (start, end, text) 자막 라인 리스트. 길이 기반 분절."""
    lines: list[tuple[float, float, str]] = []
    buf: list[dict] = []
    buf_chars = 0
    for w in words:
        wtxt = (w.get("text") or "").strip()
        if not wtxt:
            continue
        # 다음 단어 합치면 max_chars 초과 → flush
        if buf and buf_chars + len(wtxt) + 1 > max_chars:
            start = float(buf[0]["start"])
            end = float(buf[-1]["end"])
            text = " ".join((b.get("text") or "").strip() for b in buf)
            lines.append((start, end, text))
            buf = []
            buf_chars = 0
        buf.append(w)
        buf_chars += len(wtxt) + (1 if buf else 0)
    if buf:
        start = float(buf[0]["start"])
        end = float(buf[-1]["end"])
        text = " ".join((b.get("text") or "").strip() for b in buf)
        lines.append((start, end, text))
    return lines


def _build_combined_ass(
    copy1: str,
    copy2: str,
    subtitle_lines: list[tuple[float, float, str]],
    duration: float,
    out_path: Path,
) -> None:
    """인트로 + 로고 + 자막을 단일 ASS 파일로 합성."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {CANVAS_W}\n"
        f"PlayResY: {CANVAS_H}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        # 인트로 copy1 (노랑) — 화면 하단 중앙
        f"Style: Intro1,Pretendard Black,{INTRO_COPY1_SIZE},{INTRO_COPY1_COLOR},&H00000000,"
        "&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,3,5,40,40,0,1\n"
        # 인트로 copy2 (흰색) — copy1 바로 아래
        f"Style: Intro2,Pretendard Black,{INTRO_COPY2_SIZE},{INTRO_COPY2_COLOR},&H00000000,"
        "&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,3,5,40,40,0,1\n"
        # 로고 — 우상단
        f"Style: Logo,Pretendard Black,{LOGO_SIZE},&H00FFFFFF,&H00000000,&H00000000,"
        "&H80000000,1,0,0,0,100,100,0,0,1,2,2,9,40,40,40,1\n"
        # 자막 — 하단 중앙, 검정 외곽선
        f"Style: Sub,Pretendard Black,{SUBTITLE_SIZE},&H00FFFFFF,&H00000000,&H00000000,"
        "&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,80,80,140,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    events: list[str] = []

    # 인트로 — 0~INTRO_END (fade out 마지막 INTRO_FADE_MS)
    fade_tag = f"{{\\fad(0,{INTRO_FADE_MS})}}"
    # copy1 위치: 화면 중앙 약간 하단 (1100), copy2: 1240
    intro_start = _sec_to_ass(0)
    intro_end = _sec_to_ass(INTRO_END)
    if copy1:
        events.append(
            f"Dialogue: 5,{intro_start},{intro_end},Intro1,,0,0,0,,"
            f"{fade_tag}{{\\pos({CANVAS_W // 2},1100)}}{copy1}"
        )
    if copy2:
        events.append(
            f"Dialogue: 5,{intro_start},{intro_end},Intro2,,0,0,0,,"
            f"{fade_tag}{{\\pos({CANVAS_W // 2},1240)}}{copy2}"
        )

    # 로고 — 0 ~ duration, alignment=9 (top-right)
    events.append(f"Dialogue: 3,{_sec_to_ass(0)},{_sec_to_ass(duration)},Logo,,0,0,0,,{LOGO_TEXT}")

    # 자막 — 인트로 끝난 후부터만 표시 (겹침 방지)
    for s, e, txt in subtitle_lines:
        if e < INTRO_END:
            continue
        adj_s = max(s, INTRO_END)
        if adj_s >= e:
            continue
        events.append(f"Dialogue: 1,{_sec_to_ass(adj_s)},{_sec_to_ass(e)},Sub,,0,0,0,,{txt}")

    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT s.*, v.youtube_id FROM shorts s "
        "JOIN videos v ON v.id=s.source_video_id "
        "WHERE s.internal_id=?",
        (INTERNAL_ID,),
    ).fetchone()
    conn.close()
    if not r:
        print(f"no moment: {INTERNAL_ID}")
        return 1

    src = settings.samples_dir / f"{r['youtube_id']}.mp4"
    if not src.exists():
        print(f"source mp4 not found: {src}")
        return 1

    start = float(r["start_time"])
    end = float(r["end_time"])
    duration = end - start
    copy1 = (r["copy1"] or "").strip()
    copy2 = (r["copy2"] or "").strip()

    # transcript 로드 + 모먼트 구간 단어만 필터
    transcript_path = Path("data/transcripts") / f"{r['youtube_id']}.json"
    if not transcript_path.exists():
        print(f"transcript not found: {transcript_path}")
        return 1
    with transcript_path.open(encoding="utf-8") as f:
        transcript = json.load(f)
    moment_words: list[dict] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            ws = float(w.get("start", 0))
            we = float(w.get("end", 0))
            if ws >= start and we <= end:
                moment_words.append(
                    {
                        "start": ws - start,
                        "end": we - start,
                        "text": w.get("text", ""),
                    }
                )

    subtitle_lines = _split_lines(moment_words, SUBTITLE_MAX_CHARS)
    print(f"=== {INTERNAL_ID} fullformat sample ===")
    print(f"src: {src}")
    print(f"range: {start:.2f} ~ {end:.2f} ({duration:.2f}s)")
    print(f"intro: '{copy1}' / '{copy2}'")
    print(f"transcript words in moment: {len(moment_words)}")
    print(f"subtitle lines: {len(subtitle_lines)}")
    print(f"output: {OUTPUT}")
    print()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT.with_suffix(".combined.ass")
    _build_combined_ass(copy1, copy2, subtitle_lines, duration, ass_path)

    # 풀 캔버스 blur padding (1080×1920):
    #   전경: 16:9 → 3:2 crop, 1080 wide scale (~720h)
    #   배경: 1920 height fill + blur, crop 1080×1920
    #   overlay 중앙
    # 자막 ASS는 fontsdir로 Pretendard-Black.otf 폴더 지정.
    fonts_dir = str(settings.font_path.parent).replace("\\", "/")
    ass_path_norm = str(ass_path).replace("\\", "/").replace(":", "\\:")
    filter_complex = (
        "[0:v]split=2[fg][bg];"
        "[fg]crop=ih*1.5:ih:(iw-ih*1.5)/2:0,"
        f"scale={CANVAS_W}:-2[fgs];"
        f"[bg]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{CANVAS_H},boxblur=40:2[bgblur];"
        "[bgblur][fgs]overlay=0:(H-h)/2,setsar=1[reframed];"
        f"[reframed]subtitles='{ass_path_norm}':fontsdir='{fonts_dir}'[out]"
    )

    audio_pan = "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"

    hwaccel = detect_hwaccel()
    input_decoder = ["-hwaccel", "cuda"] if hwaccel == "cuda" else []
    if settings.use_nvenc and hwaccel == "cuda":
        encoder = ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "5M"]
    else:
        encoder = ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *input_decoder,
        "-i",
        str(src),
        "-ss",
        str(start),
        "-to",
        str(end),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-map",
        "0:a?",
        "-af",
        audio_pan,
        *encoder,
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
        str(OUTPUT),
    ]

    print(f"ffmpeg encoder={encoder[1]} hwaccel={hwaccel}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ffmpeg FAILED:")
        print(result.stderr)
        return 1

    # ASS는 디버깅용으로 유지 (디자인 조정 시 참고).
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\nDONE: {OUTPUT} ({size_mb:.1f} MB)")
    print(f"ASS file (참고용): {ass_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
