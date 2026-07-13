"""슬롯 자동 할당 테스트 — 2026-07-13 슬롯 개편 (11/20시, 화·금 20시 제외)."""

from __future__ import annotations

from app.pipeline.schedule import _candidate_slots


def test_ko_slots_only_11_and_20():
    slots = _candidate_slots("ko", max_lookahead_days=14, min_lead_hours=0)
    assert slots, "슬롯이 비어있으면 안 됨"
    assert {s.hour for s in slots} == {11, 20}


def test_ko_no_20h_slot_on_tue_fri():
    """화(1)/금(4) 20시는 미드폼 업로드와 겹치므로 제외."""
    slots = _candidate_slots("ko", max_lookahead_days=21, min_lead_hours=0)
    clash = [s for s in slots if s.hour == 20 and s.weekday() in (1, 4)]
    assert clash == []
    # 화/금 11시는 존재해야 함
    assert any(s.hour == 11 and s.weekday() in (1, 4) for s in slots)


def test_ko_weekly_slot_count_is_12():
    """주 12슬롯 (5일×2 + 화금×1) — 재고 수급 주 10~16개에 맞춘 케이던스."""
    slots = _candidate_slots("ko", max_lookahead_days=14, min_lead_hours=0)
    # 온전한 7일 창을 세기 위해 첫 슬롯 날짜부터 7일간 카운트
    start = slots[0].date()
    week = [s for s in slots if (s.date() - start).days < 7]
    assert len(week) in (11, 12)  # lead 경계로 첫날 슬롯 1개가 잘릴 수 있음


def test_en_slots_unchanged():
    slots = _candidate_slots("en", max_lookahead_days=7, min_lead_hours=0)
    assert {s.hour for s in slots} == {7, 20}
