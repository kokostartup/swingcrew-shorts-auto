"""Gemini로 게시용 메타데이터 생성 (제목/설명/해시태그)."""
from __future__ import annotations

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


SYSTEM_INSTRUCTION = """너는 SwingCrew 골프 채널의 SNS 카피라이터다.
YouTube Shorts / Instagram Reels / TikTok 모두에 공통으로 쓸 메타데이터를 생성한다.

규칙:
- 한국어 위주. 핵심 키워드는 한국어로.
- 제목 (title): 100자 이내. 영상 핵심 메시지 압축. 끝에 " #Shorts" 포함 (YouTube Shorts 분류용).
- 설명 (description): 2~4문장. 영상 내용 요약 + 영빈 채널 톤 (친근하고 실용적). 끝에 빈 줄 + 해시태그 5~10개 (`#골프 #골프레슨 #골프스윙 #shorts ...`).
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


def _build_user_prompt(moment: MagicMoment) -> str:
    return f"""
다음 매직 모먼트로 SNS 메타데이터 생성:

copy1: {moment.copy1}
copy2: {moment.copy2}
hook: {moment.hook_text}
reasoning: {moment.reasoning}

위 정보로 제목/설명/태그/해시태그를 한국어 골프 채널 톤으로 만들어줘.
"""


def generate_publish_meta(moment: MagicMoment) -> PublishMeta:
    """모먼트 → SNS 메타 (제목/설명/태그/해시태그)."""
    prompt = _build_user_prompt(moment)
    raw = generate_json(
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=prompt,
        temperature=settings.gemini_temperature,
        model_name=settings.gemini_model,
    )
    meta = PublishMeta(
        title=str(raw.get("title", ""))[:100],
        description=str(raw.get("description", ""))[:2000],
        tags=[str(t) for t in (raw.get("tags") or [])][:30],
        hashtags=[str(t) for t in (raw.get("hashtags") or [])][:15],
    )
    log.info(
        "publish_meta.generated",
        title_len=len(meta.title),
        desc_len=len(meta.description),
        tags=len(meta.tags),
        hashtags=len(meta.hashtags),
    )
    return meta


__all__ = ["PublishMeta", "generate_publish_meta"]
