"""Google Gemini API 클라이언트 wrapper.

JSON mode 강제 + tenacity retry + lazy import (CLI 시작 시간 절감).
"""
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

_configured = False


def _ensure_configured() -> None:
    """genai.configure() 1회 호출."""
    global _configured
    if _configured:
        return
    if not settings.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY 미설정. .env에 추가하세요."
        )
    import google.generativeai as genai

    genai.configure(api_key=settings.google_api_key)
    _configured = True


class GeminiAPIError(RuntimeError):
    """Gemini 호출 실패."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(GeminiAPIError),
)
def generate_json(
    *,
    system_instruction: str,
    prompt: str,
    temperature: float = 0.3,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Gemini로 JSON 응답을 받아 dict 반환.

    response_mime_type=application/json 강제. 파싱 실패 시 GeminiAPIError.
    """
    _ensure_configured()
    import json

    import google.generativeai as genai

    model_name = model_name or settings.gemini_model

    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
        generation_config={
            "temperature": temperature,
            "response_mime_type": "application/json",
        },
    )
    log.info("gemini.request", model=model_name, prompt_chars=len(prompt))
    try:
        resp = model.generate_content(
            prompt,
            request_options={"timeout": settings.gemini_request_timeout_sec},
        )
    except Exception as e:
        raise GeminiAPIError(f"Gemini API call failed: {e}") from e

    text = (resp.text or "").strip()
    if not text:
        raise GeminiAPIError("Empty response from Gemini")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise GeminiAPIError(f"Invalid JSON: {e}\nText: {text[:500]}") from e
    log.info("gemini.response_received", model=model_name, chars=len(text))
    return data
