"""publish_socials_from_notion 회귀 테스트.

2026-08-07 사고: 노션 'scheduled' 조회가 일시적으로 빈 결과 반환 →
keep-list 방식 cleanup이 R2 버킷 전체(P032-S09 + P033 10개) 삭제.
2026-08-11: 미리 업로드된 미래 클립(P034, 아직 scheduled 아님)도 삭제.
→ delete-list 방식('게시' + 3일 경과만 삭제), R2 pre-flight,
  platform 실패 시 exit 1 검증.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "publish_socials_from_notion.py"
_spec = importlib.util.spec_from_file_location("publish_socials_from_notion", _SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _page(iid: str, sched: str | None = None, status: str = "scheduled") -> dict[str, Any]:
    return {"id": f"page-{iid}", "internal_id": iid, "scheduled_at": sched, "status": status}


@pytest.fixture()
def deleted_keys(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """R2에 mp4 3개 있는 상태 mock — 삭제된 키 목록 반환."""
    deleted: list[str] = []
    keys = ["26-P032-S09.mp4", "26-P033-S01.mp4", "26-P034-S01.mp4"]
    monkeypatch.setattr(mod.r2, "list_object_keys", lambda: list(keys))
    monkeypatch.setattr(mod.r2, "delete_object", lambda k: deleted.append(k) or True)
    monkeypatch.setattr(mod, "_internal_id_from_page", lambda pid: pid.removeprefix("page-"))
    monkeypatch.setattr(mod, "TARGET_INTERNAL_IDS", set())
    monkeypatch.setattr(mod, "DRY_RUN", False)
    return deleted


def test_transient_empty_notion_deletes_nothing(
    deleted_keys: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """8/7 사고 재현: 노션 조회가 빈 결과여도 R2 삭제는 0건이어야 한다."""
    monkeypatch.setattr(mod, "list_pages_by_status", lambda status: [])
    mod._cleanup_previous_slot_r2([])
    assert deleted_keys == []


def test_deletes_only_published(deleted_keys: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """'게시' + 3일 경과한 mp4만 삭제 — scheduled와 미리 올려둔 미래 클립은 보존."""
    monkeypatch.setattr(
        mod,
        "list_pages_by_status",
        lambda status: [_page("26-P032-S09", sched=_iso_days_ago(5), status="published")],
    )
    mod._cleanup_previous_slot_r2([_page("26-P033-S01")])
    assert deleted_keys == ["26-P032-S09.mp4"]


def test_recent_published_kept(deleted_keys: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """나이 가드: 게시됐어도 슬롯 시각 3일 이내면 보존 — 오삭제 물리 방어."""
    monkeypatch.setattr(
        mod,
        "list_pages_by_status",
        lambda status: [_page("26-P032-S09", sched=_iso_days_ago(1), status="published")],
    )
    mod._cleanup_previous_slot_r2([])
    assert deleted_keys == []


def test_notion_query_failure_deletes_nothing(
    deleted_keys: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """published 조회 실패 시 삭제 없이 skip (다음 슬롯에서 재시도)."""

    def _boom(status: str) -> list[dict[str, Any]]:
        raise RuntimeError("notion down")

    monkeypatch.setattr(mod, "list_pages_by_status", _boom)
    mod._cleanup_previous_slot_r2([])
    assert deleted_keys == []


# --- R2 pre-flight ---


def test_r2_file_missing_only_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """404만 missing 취급 — 그 외 응답/네트워크 오류는 게시 시도 진행."""
    monkeypatch.setattr(mod.httpx, "head", lambda url, timeout: SimpleNamespace(status_code=404))
    assert mod._r2_file_missing("https://r2/x.mp4") is True
    monkeypatch.setattr(mod.httpx, "head", lambda url, timeout: SimpleNamespace(status_code=200))
    assert mod._r2_file_missing("https://r2/x.mp4") is False

    def _net_down(url: str, timeout: int) -> SimpleNamespace:
        raise OSError("network down")

    monkeypatch.setattr(mod.httpx, "head", _net_down)
    assert mod._r2_file_missing("https://r2/x.mp4") is False


def test_preflight_missing_file_skips_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2에 파일 없으면 platform 호출 없이 skip + 노션 '오류' + 재업로드 안내."""
    platform_calls: list[str] = []
    notion_writes: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(mod, "_internal_id_from_page", lambda pid: "26-P033-S03")
    monkeypatch.setattr(mod.httpx, "head", lambda url, timeout: SimpleNamespace(status_code=404))
    for fn in ("post_facebook_video", "post_instagram_reel", "post_threads_video"):
        monkeypatch.setattr(mod, fn, lambda *a, _fn=fn, **k: platform_calls.append(_fn))
    monkeypatch.setattr(
        mod, "notion_update", lambda pid, status, **kw: notion_writes.append((pid, status, kw))
    )
    monkeypatch.setattr(mod, "TARGET_PLATFORMS", set())

    page = {"id": "page-1", "status": "scheduled", "title": "t", "description": "d"}
    ok, results = mod._publish_one_moment(page)

    assert ok is False
    assert platform_calls == []
    assert results["r2"].startswith("error:")
    assert notion_writes[0][1] == "error"
    assert "r2_reupload" in notion_writes[0][2]["preview_url"]


# --- platform 실패 시 exit 1 ---


def _run_main(monkeypatch: pytest.MonkeyPatch, publish_result: tuple[bool, dict]) -> None:
    monkeypatch.setattr(
        mod,
        "list_pages_by_status",
        lambda status: [_page("26-X", sched="2026-08-11T11:00:00+09:00")],
    )
    monkeypatch.setattr(mod, "_cleanup_previous_slot_r2", lambda pages: None)
    monkeypatch.setattr(mod, "_publish_one_moment", lambda page: publish_result)
    monkeypatch.setattr(mod, "notion_update", lambda *a, **k: None)
    monkeypatch.setattr(mod, "SKIP_TIME_FILTER", True)
    monkeypatch.setattr(mod, "TARGET_INTERNAL_IDS", set())
    monkeypatch.setattr(mod, "DRY_RUN", False)
    mod.main()


def test_main_exits_nonzero_on_platform_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """부분 성공이라도 platform 실패가 있으면 exit 1 — GitHub 실패 알림 트리거."""
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, (True, {"facebook": "https://fb", "instagram": "error:boom"}))
    assert exc.value.code == 1


def test_main_no_exit_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """전 platform 성공이면 SystemExit 없이 정상 종료."""
    _run_main(monkeypatch, (True, {"facebook": "https://fb"}))
