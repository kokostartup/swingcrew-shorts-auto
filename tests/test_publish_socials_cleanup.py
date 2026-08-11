"""_cleanup_previous_slot_r2 회귀 테스트.

2026-08-07 사고: 노션 'scheduled' 조회가 일시적으로 빈 결과 반환 →
keep-list 방식 cleanup이 R2 버킷 전체(P032-S09 + P033 10개) 삭제.
2026-08-11: 미리 업로드된 미래 클립(P034, 아직 scheduled 아님)도 삭제.
→ delete-list 방식('게시' 확인된 것만 삭제) 검증.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "publish_socials_from_notion.py"
_spec = importlib.util.spec_from_file_location("publish_socials_from_notion", _SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _page(iid: str) -> dict[str, Any]:
    return {"id": f"page-{iid}", "internal_id": iid}


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


def test_deletes_only_published(
    deleted_keys: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """'게시' 확인된 mp4만 삭제 — scheduled와 미리 올려둔 미래 클립은 보존."""
    monkeypatch.setattr(
        mod, "list_pages_by_status", lambda status: [_page("26-P032-S09")]
    )
    mod._cleanup_previous_slot_r2([_page("26-P033-S01")])
    assert deleted_keys == ["26-P032-S09.mp4"]


def test_notion_query_failure_deletes_nothing(
    deleted_keys: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """published 조회 실패 시 삭제 없이 skip (다음 슬롯에서 재시도)."""

    def _boom(status: str) -> list[dict[str, Any]]:
        raise RuntimeError("notion down")

    monkeypatch.setattr(mod, "list_pages_by_status", _boom)
    mod._cleanup_previous_slot_r2([])
    assert deleted_keys == []
