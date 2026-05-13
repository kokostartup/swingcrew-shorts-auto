"""Buffer GraphQL 어댑터 — Threads/Instagram/TikTok 큐에 영상 post 추가.

영빈 Scheduled At은 큐 모드(`addToQueue`)에선 직접 사용되지 않음.
Buffer 대시보드에서 영빈이 미리 설정한 스케줄 슬롯대로 자동 게시.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

BUFFER_GRAPHQL_URL = "https://api.buffer.com/graphql"
# Buffer service 이름 → 영빈 채널 매핑.
SUPPORTED_SERVICES = {"threads", "instagram", "tiktok"}


class BufferError(RuntimeError):
    """Buffer 호출 실패."""


_organization_id: str | None = None
_channels_by_service: dict[str, str] | None = None


def _headers() -> dict[str, str]:
    if not settings.buffer_access_token:
        raise BufferError("BUFFER_ACCESS_TOKEN 미설정. .env 확인.")
    return {"Authorization": f"Bearer {settings.buffer_access_token}"}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(BufferError),
    reraise=True,
)
def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """GraphQL POST. errors 있으면 BufferError."""
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        resp = httpx.post(
            BUFFER_GRAPHQL_URL, json=payload, headers=_headers(), timeout=60,
        )
    except httpx.HTTPError as e:
        raise BufferError(f"network error: {e}") from e
    if resp.status_code >= 500:
        raise BufferError(f"Buffer 5xx: {resp.status_code} {resp.text[:300]}")
    try:
        body = resp.json()
    except Exception as e:
        raise BufferError(f"non-JSON response: {e} text={resp.text[:300]}") from e
    if "errors" in body and body["errors"]:
        raise BufferError(f"GraphQL errors: {body['errors']}")
    if "data" not in body:
        raise BufferError(f"missing data: {body}")
    return body["data"]


def _get_organization_id() -> str:
    """첫 organization id 캐시 + 반환. .env 명시 시 우선."""
    global _organization_id
    if _organization_id is not None:
        return _organization_id
    if settings.buffer_organization_id:
        _organization_id = settings.buffer_organization_id
        return _organization_id
    data = _graphql("{ account { organizations { id name } } }")
    orgs = data["account"]["organizations"]
    if not orgs:
        raise BufferError("Buffer organizations 없음 — 계정 셋업 확인.")
    _organization_id = str(orgs[0]["id"])
    log.info(
        "buffer.organization_resolved",
        organization_id=_organization_id, name=orgs[0]["name"],
    )
    return _organization_id


def get_channels_by_service() -> dict[str, str]:
    """서비스명 → channel id 매핑 (모듈 캐시).

    영빈 Buffer에 연결된 모든 채널 조회 → SUPPORTED_SERVICES만 채택.
    """
    global _channels_by_service
    if _channels_by_service is not None:
        return _channels_by_service
    org_id = _get_organization_id()
    query = (
        "query Channels($orgId: OrganizationId!) {"
        " channels(input: {organizationId: $orgId}) {"
        " id name displayName service }"
        "}"
    )
    data = _graphql(query, {"orgId": org_id})
    mapping: dict[str, str] = {}
    for ch in data["channels"]:
        service = str(ch["service"])
        if service in SUPPORTED_SERVICES:
            mapping[service] = str(ch["id"])
    _channels_by_service = mapping
    log.info(
        "buffer.channels_resolved",
        services=list(mapping.keys()),
        missing=list(SUPPORTED_SERVICES - set(mapping)),
    )
    return mapping


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(BufferError),
    reraise=True,
)
def create_video_post(
    *, channel_id: str, text: str, video_url: str, service: str | None = None,
) -> str:
    """Buffer 큐에 영상 post 추가 → post id 반환.

    mode=addToQueue: 영빈이 Buffer에 미리 설정한 schedule 슬롯에 따라 자동 게시.
    service='instagram' → metadata.instagram.type=reel (Reels로 게시).
    """
    mutation = (
        "mutation CreatePost($input: CreatePostInput!) {"
        " createPost(input: $input) {"
        " __typename"
        " ... on PostActionSuccess { post { id text } }"
        " ... on MutationError { message }"
        " }"
        "}"
    )
    input_data: dict[str, Any] = {
        "text": text,
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "addToQueue",
        "assets": {"videos": [{"url": video_url}]},
    }
    if service == "instagram":
        input_data["metadata"] = {
            "instagram": {"type": "reel", "shouldShareToFeed": True},
        }
    variables = {"input": input_data}
    data = _graphql(mutation, variables)
    result = data["createPost"]
    if result.get("__typename") == "MutationError":
        raise BufferError(f"createPost error: {result.get('message')}")
    post = result.get("post") or {}
    post_id = str(post.get("id", ""))
    log.info(
        "buffer.post_created",
        channel_id=channel_id, service=service,
        post_id=post_id, text_len=len(text),
    )
    return post_id


__all__ = [
    "SUPPORTED_SERVICES",
    "BufferError",
    "create_video_post",
    "get_channels_by_service",
]
