"""Gemini로 게시용 메타데이터 생성 (제목/설명/해시태그)."""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.integrations.gemini import generate_json
from app.storage.models import MagicMoment
from app.utils.logger import get_logger

log = get_logger(__name__)


class PublishMeta(BaseModel):
    model_config = ConfigDict(frozen=True)
    title: str = Field(max_length=100)        # YouTube 제목 한도 100자
    description: str = Field(max_length=2000)  # Buffer/IG/TikTok 캡션 ~2200, 안전 2000
    tags: list[str] = Field(default_factory=list)  # YouTube tags (해시태그 X)
    hashtags: list[str] = Field(default_factory=list)  # IG/TikTok용 #foo


SYSTEM_INSTRUCTION = """너는 스윙크루(SwingCrew) 골프 채널의 SNS 카피라이터다.
YouTube Shorts / Instagram Reels / TikTok 모두에 공통으로 쓸 메타데이터를 생성한다.

규칙:
- 한국어 위주. 핵심 키워드는 한국어로.
- 채널/인물 명칭: 채널 이름은 "스윙크루"만 사용. 진행자/프로 개인 이름(예: "영빈", "영빈 프로") 절대 언급 금지. "영빈프로" 같은 해시태그도 금지.
- 제목 (title): 100자 이내. 영상 핵심 메시지 압축. 해시태그(#) 절대 포함 금지.
- 설명 (description): 2~4문장. 영상 내용 요약 + 친근하고 실용적인 톤. 끝에 빈 줄 + 해시태그 5~10개 (`#골프 #골프레슨 #골프스윙 #shorts ...`).
- 태그 (tags): YouTube videos.insert용. 영문 + 한국어 키워드 10~15개. # 없이 단어만.
- 해시태그 (hashtags): IG/TikTok용. # 포함 5~10개.

응답 JSON schema:
{
  "title": "string (100자 이내)",
  "description": "string (2000자 이내, 끝에 해시태그 포함)",
  "tags": ["string", ...],
  "hashtags": ["#string", ...]
}
"""


SYSTEM_INSTRUCTION_EN = """You are the social copywriter for SwingCrew, an English-language golf channel.
Generate metadata for YouTube Shorts (EN channel only — no IG/TikTok/FB on EN channel).

Rules:
- English only. Concise, actionable, hook-driven tone.
- Channel name: only "SwingCrew". Never mention any presenter/pro personal name.
- Title: ≤ 100 chars. Compress the video's core message. NO hashtags (#) in title.
- Description: 2-4 sentences. Summarize the video + friendly practical tone. End with blank line + 5-10 hashtags (`#golf #golfswing #golftips #shorts ...`).
- Tags: for YouTube videos.insert. 10-15 English keywords. No # — just words.
- Hashtags: 5-10 entries with # prefix (e.g., #golf, #golfswing, #shorts).

Response JSON schema:
{
  "title": "string (≤ 100 chars)",
  "description": "string (≤ 2000 chars, ending with hashtags)",
  "tags": ["string", ...],
  "hashtags": ["#string", ...]
}
"""


def _strip_host_name(s: str) -> str:
    """진행자 이름 "영빈" 관련 표현 제거 — 채널명은 "스윙크루"만 노출.

    "영빈 프로가", "영빈프로의", "영빈이" 등 변형 일괄 처리. 제거 후 발생한 중복
    공백 정리. Gemini prompt 어기는 안전망.
    """
    cleaned = re.sub(r"영빈\s*프로[가는이의을를와이]?", "", s)
    cleaned = re.sub(r"영빈[\s가는이의을를와이]?", "", cleaned)
    cleaned = re.sub(r"#영빈\S*", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _build_user_prompt(moment: MagicMoment, channel: str = "ko") -> str:
    if channel == "en":
        return f"""
Generate SNS metadata for the following magic moment:

copy1: {moment.copy1}
copy2: {moment.copy2}
hook: {moment.hook_text}
reasoning: {moment.reasoning}

Produce title/description/tags/hashtags for SwingCrew (English golf channel) tone.
"""
    return f"""
다음 매직 모먼트로 SNS 메타데이터 생성:

copy1: {moment.copy1}
copy2: {moment.copy2}
hook: {moment.hook_text}
reasoning: {moment.reasoning}

위 정보로 제목/설명/태그/해시태그를 한국어 골프 채널 톤으로 만들어줘.
"""


def generate_publish_meta(
    moment: MagicMoment, channel: str = "ko",
) -> PublishMeta:
    """모먼트 → SNS 메타 (제목/설명/태그/해시태그). channel별 프롬프트."""
    prompt = _build_user_prompt(moment, channel=channel)
    sys_instruction = SYSTEM_INSTRUCTION_EN if channel == "en" else SYSTEM_INSTRUCTION
    raw = generate_json(
        system_instruction=sys_instruction,
        prompt=prompt,
        temperature=settings.gemini_temperature,
        model_name=settings.gemini_model,
    )
    # Title에서 해시태그(#xxx) 토큰 강제 제거 — Gemini가 prompt 어기는 케이스 안전망.
    title_clean = re.sub(r"\s*#\S+", "", str(raw.get("title", ""))).strip()
    desc_raw = str(raw.get("description", ""))
    # 한국 채널만 진행자 이름 sanitization (영어 채널은 한국 이름 등장 가능성 낮음).
    if channel == "ko":
        title_clean = _strip_host_name(title_clean)
        desc_clean = _strip_host_name(desc_raw)
        hashtags_clean = [
            t for t in (raw.get("hashtags") or []) if "영빈" not in str(t)
        ]
        tags_clean = [
            str(t) for t in (raw.get("tags") or []) if "영빈" not in str(t)
        ]
    else:
        desc_clean = desc_raw
        hashtags_clean = list(raw.get("hashtags") or [])
        tags_clean = [str(t) for t in (raw.get("tags") or [])]
    meta = PublishMeta(
        title=title_clean[:100],
        description=desc_clean[:2000],
        tags=tags_clean[:30],
        hashtags=[str(t) for t in hashtags_clean][:15],
    )
    log.info(
        "publish_meta.generated",
        channel=channel,
        title_len=len(meta.title),
        desc_len=len(meta.description),
        tags=len(meta.tags),
        hashtags=len(meta.hashtags),
    )
    return meta


__all__ = ["PublishMeta", "generate_publish_meta"]
