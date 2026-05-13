# Skill: ffmpeg-patterns

## When to Use

- `app/pipeline/edit.py` — 클립 컷 + 합성
- `app/pipeline/template.py` — 9:16 시그니처 레이아웃
- `app/pipeline/scene.py` — 시각 분석을 위한 프레임 샘플링
- `video-engineer` 서브에이전트 호출 시 함께 전달

## How It Works

모든 FFmpeg 호출은 다음 원칙을 따른다:
1. Python에서 `subprocess.run(cmd_list, check=True)` — `shell=True` 금지
2. 가능한 한 단일 `filter_complex` 그래프 (중간 파일 X)
3. 입력 분석은 `ffprobe -print_format json`
4. 출력 검증은 `ffprobe`로 메타 assert

## Patterns

### Pattern 1: 9:16 시그니처 캔버스 (3-zone, 4:5 영상 영역)

```python
# 1080×1920 캔버스
# - top_band: y=0..480     (검정박스, 2줄 카피)
# - video:    y=480..1830  (1080×1350, 4:5)
# - bottom:   y=1830..1920 (검정 padding, Shorts UI overlay 영역)
#
# 입력 영상은 reframe 함수가 미리 1080×1350으로 변환해서 [reframed] 라벨로 넘김.

filter_complex = (
    "[reframed]"  # 1080×1350 결과물 (letterbox_4_5 또는 talking_head_crop_static)
    "pad=1080:1920:0:480:color=black[canvas];"
    "[canvas]drawtext=fontfile={font}:text='{copy1}':"
    "fontcolor=white:fontsize=88:x=(w-text_w)/2:y=130,"
    "drawtext=fontfile={font}:text='{copy2}':"
    "fontcolor=#FFE500:fontsize=88:x=(w-text_w)/2:y=270[out]"
).format(font=font_path, copy1=copy_line1, copy2=copy_line2)

cmd = [
    "ffmpeg", "-y", "-i", str(src),
    "-filter_complex", filter_complex,
    "-map", "[out]", "-map", "0:a?",
    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
    "-c:a", "aac", "-b:a", "128k",
    "-r", "30", "-pix_fmt", "yuv420p",
    str(dst),
]
```

**중요**: 상단 검정박스 카피가 1줄에 안 들어가면 자동 줄바꿈 + fontsize 동적 축소.
폰트 크기 산출 헬퍼:

```python
def fit_fontsize(text: str, max_width: int = 1000, base_size: int = 88) -> int:
    """한글 평균 폭 ≈ fontsize × 1.0, ASCII ≈ × 0.55 가정."""
    est = sum(1.0 if ord(c) > 127 else 0.55 for c in text) * base_size
    return base_size if est <= max_width else int(base_size * max_width / est)
```

**중요**: 상단 검정박스 카피가 1줄에 안 들어가면 자동 줄바꿈 + fontsize 동적 축소.
폰트 크기 산출 헬퍼:

```python
def fit_fontsize(text: str, max_width: int = 1000, base_size: int = 88) -> int:
    """한글 평균 폭 ≈ fontsize × 1.0, ASCII ≈ × 0.55 가정."""
    est = sum(1.0 if ord(c) > 127 else 0.55 for c in text) * base_size
    return base_size if est <= max_width else int(base_size * max_width / est)
```

### Pattern 2: letterbox_4_5 (4:5 crop + 영상 영역 채움)

```python
# 원본 → 1080×1350 (4:5) 영역 꽉 채움. 좌우 약 55% crop (16:9 원본 기준).
# scale 단계에서 4:5 영역의 "더 큰 쪽"에 맞추고 (increase), 넘치는 쪽을 가운데 crop.
#
# 원본 1920×1080 (16:9) 기준 계산:
#   scale=1080:1350:force_original_aspect_ratio=increase → 2400×1350
#   crop=1080:1350 → 가운데 1080 추출 (좌우 660px씩 제거)

filter_complex_segment = (
    "[0:v]scale=1080:1350:force_original_aspect_ratio=increase,"
    "crop=1080:1350[reframed]"
)
```

### Pattern 3: talking_head_crop_static (사람 클로즈업, 정적 중앙 crop)

```python
# 원본 가운데 9:16 영역(607×1080)만 추출 → 1080×1350 영역에 fit.
# 사람이 영상 가운데 있다는 가정 (인터뷰/설명 클로즈업).
# 좌우 약 75% 정보 손실되는 대신 인물이 영역 꽉 채움.
#
# 원본 1920×1080 기준:
#   crop=ih*9/16:ih → 607×1080 가운데
#   scale=1080:1350:force_original_aspect_ratio=increase → 1080×1920 (height 1920 > 1350)
#   crop=1080:1350 → 가운데 1080×1350 추출

filter_complex_segment = (
    "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
    "scale=1080:1350:force_original_aspect_ratio=increase,"
    "crop=1080:1350[reframed]"
)
```

### Pattern 3b: (Phase 4 예정) fullbody_track + split_4panel_sequence

MediaPipe Pose / 분할화면 검출 후 동적 crop. Phase 4 자동 분류 도입 시 구현.

### Pattern 4: Split 4-Panel Sequence

```python
# 2×2 분할화면 영상 → 4개 quadrant를 순차 재생 (각 quad_dur 초)
# 입력 1920×1080 가정, 각 panel 960×540

panels = []
for i, (x, y) in enumerate([(0,0), (960,0), (0,540), (960,540)]):
    panels.append(
        f"[0:v]crop=960:540:{x}:{y},trim=duration={quad_dur},"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920[p{i}]"
    )
concat = "[p0][p1][p2][p3]concat=n=4:v=1:a=0[out]"
filter_complex = ";".join(panels + [concat])
```

### Pattern 5: 자막 — 별도 burn-in 불필요

**SwingCrew 미드폼은 자막이 이미 영상에 burn-in되어 있다.** 9:16 letterbox/pad로 영상 영역 1080×1440에 fit시키면 원본 자막이 자동으로 보존되므로, 숏츠 측에서 ASS 합성을 추가하지 않는다.

WhisperX transcript는 다른 용도로만 사용:
- Gemini 매직 모먼트 추출 (시작/끝 시각 식별)
- 상단 카피 2줄 자동 생성 (LLM 요약 → 카피라이팅)
- Phase 4+ 의 multi-signal scoring (transcript_density)

향후 자막이 없는 영상을 다루게 되면 이 자리에 ASS burn-in 패턴 추가.

### Pattern 6: Frame Sampling for Scene Analysis

```python
# 1 fps로 PNG 프레임 추출 → OpenCV/MediaPipe 분석 입력
cmd = ["ffmpeg", "-i", str(video), "-vf", "fps=1",
       "-q:v", "2", str(frame_dir / "frame_%04d.png")]
```

### Pattern 7: 하드웨어 가속 감지

```python
def detect_hwaccel() -> str:
    result = subprocess.run(["ffmpeg", "-hwaccels"],
                            capture_output=True, text=True)
    available = result.stdout.lower()
    if "cuda" in available:
        return "cuda"
    if "videotoolbox" in available:
        return "videotoolbox"
    return "none"

# Encoder 선택
def video_codec(hwaccel: str) -> list[str]:
    if hwaccel == "cuda":
        return ["-c:v", "h264_nvenc", "-preset", "p4"]
    if hwaccel == "videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "5M"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]
```

### Pattern 8: 출력 검증

```python
def validate_output(path: Path, expected_dur: float) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    data = json.loads(subprocess.run(cmd, capture_output=True,
                                     text=True, check=True).stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert video["width"] == 1080 and video["height"] == 1920
    assert video["codec_name"] == "h264"
    assert abs(float(data["format"]["duration"]) - expected_dur) < 0.5
    return data
```

## Reference

- FFmpeg filter docs: https://ffmpeg.org/ffmpeg-filters.html
- 폰트 경로는 `$FONT_PATH` (.env)
- 색상: 흰색 `#FFFFFF`, 노란 강조 `#FFD400`
