"""Buffer 재시도 횟수 회귀 테스트.

`_graphql`과 `create_video_post` 양쪽에 tenacity 재시도가 걸려 있어 5xx 한 번에
3×3=9회 POST가 나갔다. Buffer 504는 gateway timeout이라 백엔드가 이미 post를
만들었을 수 있어서 시도 횟수가 곧 틱톡 중복 게시 위험이다 (2026-08-15
26-B016-S02 504). 바깥 데코레이터를 걷어내 3회로 고정.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations import buffer


class _Resp:
    """Buffer gateway timeout 응답."""

    status_code = 504
    text = "504 Gateway Time-out"


@pytest.fixture()
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """httpx POST를 항상 504로 응답 — 나간 요청 본문을 기록."""
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, json: dict[str, Any], headers: dict[str, str], timeout: int) -> _Resp:
        sent.append(json)
        return _Resp()

    monkeypatch.setattr(buffer.httpx, "post", fake_post)
    monkeypatch.setattr(buffer.settings, "buffer_access_token", "tok")
    monkeypatch.setattr(buffer._graphql.retry, "sleep", lambda _: None)
    return sent


def test_create_video_post_stops_at_three_attempts(posted: list[dict[str, Any]]) -> None:
    """504가 계속 나도 POST는 3회까지 — 중첩 재시도(9회)로 돌아가면 안 된다."""
    with pytest.raises(buffer.BufferError, match="504"):
        buffer.create_video_post(
            channel_id="ch-1",
            text="본문",
            video_url="https://r2.example/x.mp4",
            service="tiktok",
            share_now=True,
        )
    assert len(posted) == 3
