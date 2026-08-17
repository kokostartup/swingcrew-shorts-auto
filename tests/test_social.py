"""IG/스레드 게시 재시도 회귀 테스트.

2026-08-17 26-B016-S05: FB/IG는 같은 R2 URL로 정상 게시됐는데 스레드만 컨테이너
처리에서 `ERROR: UNKNOWN` (컨테이너 생성 11초 뒤). 같은 인코딩의 26-B016-S02는
이틀 전 스레드 게시에 성공 → 파일 문제가 아니라 플랫폼 서버 측 일시 실패.
당시 코드는 재시도 없이 즉시 포기해서 그 슬롯 스레드 게시가 통째로 누락됐다.

재시도가 허용되는 구간은 게시 확정 전인 컨테이너 단계까지다. publish 호출은
플랫폼이 게시를 끝내고 응답만 유실돼도 재시도하면 중복 게시되므로 절대 재시도 X.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations import social


@pytest.fixture()
def threads_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """스레드 API mock — container 생성/publish 호출을 순서대로 기록."""
    calls: dict[str, list[Any]] = {"container": [], "publish": []}

    def fake_post(url: str, data: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
        if url.endswith("/me/threads_publish"):
            calls["publish"].append(data["creation_id"])
            return {"id": "media-1"}
        calls["container"].append(data["video_url"])
        return {"id": f"container-{len(calls['container'])}"}

    monkeypatch.setattr(social, "_post", fake_post)
    monkeypatch.setattr(social.time, "sleep", lambda _: None)
    monkeypatch.setattr(social.settings, "threads_access_token", "tok")
    return calls


@pytest.fixture()
def ig_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """IG API mock — container 생성/publish 호출을 순서대로 기록."""
    calls: dict[str, list[Any]] = {"container": [], "publish": []}

    def fake_post(url: str, data: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
        if url.endswith("/media_publish"):
            calls["publish"].append(data["creation_id"])
            return {"id": "media-1"}
        calls["container"].append(data["video_url"])
        return {"id": f"container-{len(calls['container'])}"}

    monkeypatch.setattr(social, "_post", fake_post)
    monkeypatch.setattr(social.time, "sleep", lambda _: None)
    monkeypatch.setattr(social.settings, "ig_user_id", "ig-1")
    monkeypatch.setattr(social.settings, "instagram_access_token", "tok")
    return calls


def _poll_returns(monkeypatch: pytest.MonkeyPatch, per_container: list[dict[str, Any]]) -> None:
    """N번째 컨테이너의 polling 응답 지정 (`container-N` → per_container[N-1])."""

    def fake_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
        nth = int(url.rsplit("container-", 1)[1])
        return per_container[nth - 1]

    monkeypatch.setattr(social, "_get", fake_get)


# --- 스레드 ---------------------------------------------------------------


def test_threads_transient_container_error_retries(
    threads_calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNKNOWN이면 컨테이너를 새로 만들어 재시도 — 2번째 시도에서 게시 성공."""
    _poll_returns(
        monkeypatch,
        [{"status": "ERROR", "error_message": "UNKNOWN"}, {"status": "FINISHED"}],
    )

    assert social.post_threads_video("https://r2.example/x.mp4", "본문") == "media-1"
    assert len(threads_calls["container"]) == 2
    # 실패한 1번 컨테이너가 아니라 성공한 2번 컨테이너로 게시.
    assert threads_calls["publish"] == ["container-2"]


def test_threads_permanent_container_error_does_not_retry(
    threads_calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """스펙 위반은 재시도해도 결과가 같다 — 5분씩 낭비하지 않고 즉시 포기."""
    _poll_returns(monkeypatch, [{"status": "ERROR", "error_message": "INVALID_ASPECT_RATIO"}])

    with pytest.raises(social.SocialPostError, match="INVALID_ASPECT_RATIO"):
        social.post_threads_video("https://r2.example/x.mp4", "본문")
    assert len(threads_calls["container"]) == 1
    assert threads_calls["publish"] == []


def test_threads_gives_up_after_max_tries(
    threads_calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """계속 UNKNOWN이면 상한까지만 재시도하고 SocialPostError."""
    _poll_returns(
        monkeypatch,
        [{"status": "ERROR", "error_message": "UNKNOWN"}] * social.CONTAINER_MAX_TRIES,
    )

    with pytest.raises(social.SocialPostError, match="UNKNOWN"):
        social.post_threads_video("https://r2.example/x.mp4", "본문")
    assert len(threads_calls["container"]) == social.CONTAINER_MAX_TRIES
    assert threads_calls["publish"] == []


def test_threads_publish_stage_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish 응답 실패는 재시도 금지 — 서버가 이미 게시했으면 중복 게시된다."""
    containers: list[str] = []
    publishes: list[str] = []

    def fake_post(url: str, data: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
        if url.endswith("/me/threads_publish"):
            publishes.append(data["creation_id"])
            raise social.SocialPostError("http_error: ReadTimeout")
        containers.append(data["video_url"])
        return {"id": f"container-{len(containers)}"}

    monkeypatch.setattr(social, "_post", fake_post)
    monkeypatch.setattr(social, "_get", lambda url, params: {"status": "FINISHED"})
    monkeypatch.setattr(social.time, "sleep", lambda _: None)
    monkeypatch.setattr(social.settings, "threads_access_token", "tok")

    with pytest.raises(social.SocialPostError):
        social.post_threads_video("https://r2.example/x.mp4", "본문")
    assert len(containers) == 1
    assert len(publishes) == 1


# --- 인스타그램 -----------------------------------------------------------


def test_ig_container_error_retries(
    ig_calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """IG 컨테이너 ERROR도 재생성으로 재시도 — 2번째 시도에서 게시 성공."""
    _poll_returns(
        monkeypatch,
        [
            {"status_code": "ERROR", "status": "The media failed to process"},
            {"status_code": "FINISHED"},
        ],
    )

    assert social.post_instagram_reel("https://r2.example/x.mp4", "캡션") == "media-1"
    assert len(ig_calls["container"]) == 2
    assert ig_calls["publish"] == ["container-2"]


def test_ig_gives_up_after_max_tries(
    ig_calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """계속 ERROR면 상한까지만 재시도 — publish는 시도조차 하지 않는다."""
    _poll_returns(
        monkeypatch,
        [{"status_code": "ERROR", "status": "unsupported format"}] * social.CONTAINER_MAX_TRIES,
    )

    with pytest.raises(social.SocialPostError, match="unsupported format"):
        social.post_instagram_reel("https://r2.example/x.mp4", "캡션")
    assert len(ig_calls["container"]) == social.CONTAINER_MAX_TRIES
    assert ig_calls["publish"] == []


def test_ig_publish_stage_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """IG publish 응답 실패도 재시도 금지 — 릴스가 두 개 올라간다."""
    containers: list[str] = []
    publishes: list[str] = []

    def fake_post(url: str, data: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
        if url.endswith("/media_publish"):
            publishes.append(data["creation_id"])
            raise social.SocialPostError("http_error: ReadTimeout")
        containers.append(data["video_url"])
        return {"id": f"container-{len(containers)}"}

    monkeypatch.setattr(social, "_post", fake_post)
    monkeypatch.setattr(social, "_get", lambda url, params: {"status_code": "FINISHED"})
    monkeypatch.setattr(social.time, "sleep", lambda _: None)
    monkeypatch.setattr(social.settings, "ig_user_id", "ig-1")
    monkeypatch.setattr(social.settings, "instagram_access_token", "tok")

    with pytest.raises(social.SocialPostError):
        social.post_instagram_reel("https://r2.example/x.mp4", "캡션")
    assert len(containers) == 1
    assert len(publishes) == 1
