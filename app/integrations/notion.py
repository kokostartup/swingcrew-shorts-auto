"""Notion DB 어댑터 (승인 워크플로우) — Phase 5.

영빈이 노션 모바일에서 Status 토글 + Scheduled At 입력 → polling이 SQLite로 sync.
영문 status (SQLite) ↔ 한글 status (Notion) 매핑은 이 모듈이 단일 책임.
"""
from __future__ import annotations

from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.storage.models import MagicMoment, Video
from app.utils.logger import get_logger

log = get_logger(__name__)


STATUS_EN_TO_KO = {
    "proposed": "제안",
    "approved": "승인",
    "generated": "생성",
    "scheduled": "예약",
    "published": "게시",
    "rejected": "거절",
    "error": "오류",
}
STATUS_KO_TO_EN = {v: k for k, v in STATUS_EN_TO_KO.items()}


class NotionAPIError(RuntimeError):
    """Notion 호출 실패."""


_client: Any = None
_data_source_id: str | None = None


def _get_client() -> Any:
    """notion-client lazy init."""
    global _client
    if _client is not None:
        return _client
    if not settings.notion_token:
        raise NotionAPIError("NOTION_TOKEN 미설정. .env 확인.")
    if not settings.notion_shorts_db_id:
        raise NotionAPIError("NOTION_SHORTS_DB_ID 미설정. .env 확인.")
    from notion_client import Client

    _client = Client(auth=settings.notion_token)
    return _client


def _get_data_source_id() -> str:
    """첫 data source ID lazy resolve + 모듈 캐시.

    Notion 2025-09 API부터 query는 data_source_id 기반. 영빈 DB는 single-source.
    """
    global _data_source_id
    if _data_source_id is not None:
        return _data_source_id
    client = _get_client()
    try:
        resp = client.databases.retrieve(database_id=settings.notion_shorts_db_id)
    except Exception as e:
        raise NotionAPIError(f"databases.retrieve failed: {e}") from e
    sources = resp.get("data_sources") or []
    if not sources:
        raise NotionAPIError(
            f"DB {settings.notion_shorts_db_id}에 data_sources 없음 — schema 확인.",
        )
    _data_source_id = str(sources[0]["id"])
    log.info("notion.data_source_resolved", data_source_id=_data_source_id)
    return _data_source_id


def _smpte(seconds: float, fps: int = 30) -> str:
    """초 → SMPTE timecode MM:SS:FF (30fps 고정 — 영빈 영상 표준)."""
    total = round(seconds * fps)
    mm = total // (fps * 60)
    ss = (total // fps) % 60
    ff = total % fps
    return f"{mm:02d}:{ss:02d}:{ff:02d}"


def _time_range(start: float, end: float) -> str:
    """예: 00:25:18 - 00:51:00 (30fps SMPTE)."""
    return f"{_smpte(start)} - {_smpte(end)}"


def _youtube_timestamp_url(youtube_id: str, start_sec: float) -> str:
    """YouTube 미리보기 timestamp deep link (모바일 앱에서 해당 구간 재생)."""
    return f"https://youtu.be/{youtube_id}?t={int(start_sec)}s"


def _hook_title(moment: MagicMoment) -> str:
    """노션 페이지 제목 = copy1 / copy2 합쳐서 노출."""
    return f"{moment.copy1} / {moment.copy2}"


def _moment_properties(video: Video, moment: MagicMoment) -> dict[str, Any]:
    """MagicMoment → Notion page properties (create 시점 기준 모든 필드)."""
    score = moment.final_score if moment.final_score is not None else moment.score
    props: dict[str, Any] = {
        "Hook": {"title": [{"text": {"content": _hook_title(moment)}}]},
        "Status": {"select": {"name": STATUS_EN_TO_KO["proposed"]}},
        "Source Video": {
            "url": _youtube_timestamp_url(video.youtube_id, moment.start_sec),
        },
        "Time Range": {
            "rich_text": [
                {"text": {"content": _time_range(moment.start_sec, moment.end_sec)}},
            ],
        },
        "Start Sec": {"number": round(float(moment.start_sec), 1)},
        "End Sec": {"number": round(float(moment.end_sec), 1)},
        "Score": {"number": round(float(score), 2)},
        "Reasoning": {
            "rich_text": [{"text": {"content": (moment.reasoning or "")[:1900]}}],
        },
    }
    if moment.scene_type:
        props["Scene Type"] = {"select": {"name": moment.scene_type}}
    if video.internal_id:
        props["Internal ID"] = {
            "rich_text": [{"text": {"content": video.internal_id}}],
        }
    if moment.opening_line:
        props["Opening Line"] = {
            "rich_text": [{"text": {"content": moment.opening_line[:1900]}}],
        }
    return props


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(NotionAPIError),
    reraise=True,
)
def create_page(video: Video, moment: MagicMoment) -> str:
    """Notion DB에 새 후보 페이지 생성 → page_id 반환."""
    client = _get_client()
    try:
        resp = client.pages.create(
            parent={"database_id": settings.notion_shorts_db_id},
            properties=_moment_properties(video, moment),
        )
    except Exception as e:
        raise NotionAPIError(f"create_page failed: {e}") from e
    page_id = str(resp["id"])
    log.info(
        "notion.page_created",
        page_id=page_id,
        youtube_id=video.youtube_id,
        start_sec=moment.start_sec,
    )
    return page_id


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(NotionAPIError),
    reraise=True,
)
def list_pages_by_status(status_en: str) -> list[dict[str, Any]]:
    """특정 상태의 페이지 리스트 (paginated 자동 처리).

    반환 항목:
        {"id": page_id, "status": status_en, "scheduled_at": ISO|None}
    """
    if status_en not in STATUS_EN_TO_KO:
        raise ValueError(f"Unknown status: {status_en}")
    status_ko = STATUS_EN_TO_KO[status_en]
    client = _get_client()
    data_source_id = _get_data_source_id()
    out: list[dict[str, Any]] = []
    next_cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "data_source_id": data_source_id,
            "filter": {"property": "Status", "select": {"equals": status_ko}},
        }
        if next_cursor:
            params["start_cursor"] = next_cursor
        try:
            resp = client.data_sources.query(**params)
        except Exception as e:
            raise NotionAPIError(f"data_sources.query failed: {e}") from e
        for p in resp.get("results", []):
            props = p.get("properties", {})
            sched = props.get("Scheduled At", {}).get("date") or None
            scene_sel = props.get("Scene Type", {}).get("select") or None
            start_sec = props.get("Start Sec", {}).get("number")
            end_sec = props.get("End Sec", {}).get("number")
            title_rt = props.get("Title", {}).get("rich_text") or []
            desc_rt = props.get("Description", {}).get("rich_text") or []
            title = "".join(t.get("plain_text") or "" for t in title_rt).strip()
            description = "".join(t.get("plain_text") or "" for t in desc_rt).strip()
            out.append(
                {
                    "id": str(p["id"]),
                    "status": status_en,
                    "scheduled_at": sched.get("start") if sched else None,
                    "scene_type": scene_sel.get("name") if scene_sel else None,
                    "start_sec": float(start_sec) if start_sec is not None else None,
                    "end_sec": float(end_sec) if end_sec is not None else None,
                    "title": title or None,
                    "description": description or None,
                },
            )
        if not resp.get("has_more"):
            break
        next_cursor = resp.get("next_cursor")
        if not next_cursor:
            break
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(NotionAPIError),
    reraise=True,
)
def fetch_page_meta(page_id: str) -> dict[str, str | None]:
    """페이지 retrieve → Title/Description plain text 반환 (영빈 override 검수용)."""
    client = _get_client()
    try:
        resp = client.pages.retrieve(page_id=page_id)
    except Exception as e:
        raise NotionAPIError(f"pages.retrieve failed: {e}") from e
    props = resp.get("properties", {})
    title_rt = props.get("Title", {}).get("rich_text") or []
    desc_rt = props.get("Description", {}).get("rich_text") or []
    title = "".join(t.get("plain_text") or "" for t in title_rt).strip()
    description = "".join(t.get("plain_text") or "" for t in desc_rt).strip()
    return {
        "title": title or None,
        "description": description or None,
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(NotionAPIError),
    reraise=True,
)
def update_status(page_id: str, status_en: str, **extra: Any) -> None:
    """Status 전환 + 부가 properties 업데이트.

    extra 지원 키:
        preview_url (str): Preview URL 컬럼
        published_urls (list[str]): Published URLs (줄바꿈으로 합침)
    """
    if status_en not in STATUS_EN_TO_KO:
        raise ValueError(f"Unknown status: {status_en}")
    status_ko = STATUS_EN_TO_KO[status_en]
    props: dict[str, Any] = {"Status": {"select": {"name": status_ko}}}
    preview = extra.get("preview_url")
    if preview:
        props["Preview"] = {"url": preview}
    pub_urls = extra.get("published_urls")
    if pub_urls:
        urls_text = "\n".join(pub_urls)
        props["Published URLs"] = {
            "rich_text": [{"text": {"content": urls_text[:1900]}}],
        }
    internal_id = extra.get("internal_id")
    if internal_id:
        props["Internal ID"] = {
            "rich_text": [{"text": {"content": str(internal_id)}}],
        }
    title = extra.get("title")
    if title:
        props["Title"] = {
            "rich_text": [{"text": {"content": str(title)[:1900]}}],
        }
    description = extra.get("description")
    if description:
        props["Description"] = {
            "rich_text": [{"text": {"content": str(description)[:1900]}}],
        }
    client = _get_client()
    try:
        client.pages.update(page_id=page_id, properties=props)
    except Exception as e:
        raise NotionAPIError(f"pages.update failed: {e}") from e
    log.info("notion.status_updated", page_id=page_id, status=status_en)


__all__ = [
    "STATUS_EN_TO_KO",
    "STATUS_KO_TO_EN",
    "NotionAPIError",
    "create_page",
    "fetch_page_meta",
    "list_pages_by_status",
    "update_status",
]
