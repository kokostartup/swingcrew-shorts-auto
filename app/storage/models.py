"""Pydantic 모델 (Phase 2: ingest + transcribe)."""

from pathlib import Path  # noqa: F401  (Video.local_path 사용)

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TranscriptWord(BaseModel):
    """WhisperX word-level token."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    text: str
    score: float | None = None


class TranscriptSegment(BaseModel):
    """WhisperX segment (여러 단어 포함)."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)


class Transcript(BaseModel):
    """전체 영상 transcript (JSON 저장 단위)."""

    model_config = ConfigDict(frozen=True)

    language: str
    segments: list[TranscriptSegment] = Field(default_factory=list)


class Video(BaseModel):
    """SQLite videos 행 + 로컬 파일 경로."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    youtube_id: str
    title: str
    duration: int
    published_at: str | None = None
    retention_fetched_at: str | None = None
    processed_at: str | None = None
    internal_id: str | None = None
    local_path: Path
    channel: str = "ko"  # ko/en. 영어 채널은 ingest에서 자동 감지.


class MagicMoment(BaseModel):
    """Gemini가 transcript에서 추출한 숏츠 후보 (Phase 3 + Phase 4 enrichment).

    Phase 3 필드: start/end/hook/copy1/copy2/score(Gemini)/reasoning
    Phase 4 enrichment (옵션): scene_type/retention_uplift/density/final_score
    """

    model_config = ConfigDict(frozen=True)

    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    hook_text: str = Field(max_length=24)
    copy1: str = Field(max_length=30)
    copy2: str = Field(max_length=30)
    score: float = Field(ge=0, le=10)
    reasoning: str
    scene_type: str | None = None
    retention_uplift: float | None = None
    density: float | None = None
    final_score: float | None = None
    face_center_x: float | None = None  # 0~1, 가장 큰 얼굴 중심 x 평균
    # 시간별 segment 분할 — face_centered_dynamic 전용.
    # 각 항목: (start_offset, end_offset, cx_ratio, mode_face_count), 모먼트 시작 기준 상대 시간.
    # mode_face_count: segment 내 얼굴 수 최빈값 (1=영빈 1명, 2+=2명+ 모드 → fit letterbox).
    # 기존 3-tuple 데이터 호환: model_validator가 4번째 값 default 1로 채움.
    face_segments: list[tuple[float, float, float, int]] | None = None
    # 모먼트 시작 부분의 첫 문장 (영빈 hook 검토용).
    opening_line: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_face_segments(cls, data: object) -> object:
        """기존 3-tuple (start, end, cx) 데이터 → 4-tuple (start, end, cx, fc=1) 호환."""
        if isinstance(data, dict):
            segs = data.get("face_segments")
            if isinstance(segs, list):
                fixed: list[tuple] = []
                for s in segs:
                    if isinstance(s, (list, tuple)):
                        if len(s) == 3:
                            fixed.append((float(s[0]), float(s[1]), float(s[2]), 1))
                        elif len(s) == 4:
                            fixed.append((float(s[0]), float(s[1]), float(s[2]), int(s[3])))
                data["face_segments"] = fixed
        return data

    @model_validator(mode="after")
    def _check_time_range(self) -> "MagicMoment":
        if self.end_sec <= self.start_sec:
            raise ValueError(f"end_sec ({self.end_sec}) must be > start_sec ({self.start_sec})")
        if self.end_sec - self.start_sec > 80:
            raise ValueError(f"duration ({self.end_sec - self.start_sec}s) must be ≤ 80s")
        return self


class AnalysisResult(BaseModel):
    """analyze() 결과: 매직 모먼트 후보 리스트 (캐시 단위)."""

    model_config = ConfigDict(frozen=True)

    youtube_id: str
    model: str
    moments: list[MagicMoment] = Field(default_factory=list)


class RetentionCurve(BaseModel):
    """YouTube Analytics 잔존율 곡선 (Day 7+ 영상)."""

    model_config = ConfigDict(frozen=True)

    youtube_id: str
    elapsed_ratios: list[float] = Field(default_factory=list)
    audience_watch_ratio: list[float] = Field(default_factory=list)
    relative_retention_performance: list[float] = Field(default_factory=list)
    fetched_at: str
