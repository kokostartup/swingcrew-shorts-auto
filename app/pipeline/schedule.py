"""Phase 7 cron: Scheduled At 자동 채움.

채널별 슬롯 (channel-aware):
  - 한국 (ko): 매일 KST 11/20 (2슬롯) — 단 화/금은 20시 제외 (미드폼 업로드와
    경쟁 회피, 주 12슬롯). 영빈 결정 2026-07-13 (재고 수급 주 10~16개에 맞춤).
  - 영어 (en): 매일 America/Los_Angeles wall clock 07/20 (2슬롯, DST 자동)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.storage.db import get_connection
from app.utils.logger import get_logger

log = get_logger(__name__)

KST = timezone(timedelta(hours=9))
LA = ZoneInfo("America/Los_Angeles")  # DST 자동 (PST/PDT)

SLOT_HOURS_KST = [11, 20]
SLOT_HOURS_LA = [7, 20]  # 영어 채널: 미서부 morning + evening prime time

# 미드폼 업로드 요일 (화/금 20시 KST) — 같은 시각 숏츠는 알림/초기 노출 경쟁으로
# 조회수 중앙값 45% 하락 (2026-07-13 분석, 20시 화금 5,642 vs 나머지 10,384).
# 해당 요일은 20시 슬롯을 미드폼에 양보 → ko는 화/금 11시 1회만.
MIDFORM_WEEKDAYS_KO = (1, 4)  # 화=1, 금=4
MIDFORM_CLASH_HOUR_KO = 20

# channel별 timezone + 슬롯 시간.
_CHANNEL_TZ: dict[str, ZoneInfo | timezone] = {"ko": KST, "en": LA}
_CHANNEL_SLOTS: dict[str, list[int]] = {"ko": SLOT_HOURS_KST, "en": SLOT_HOURS_LA}

# 24h 검토 lead 폐기 (영빈 결정 2026-07-15) — 항상 가장 빠른 빈 슬롯부터 할당.
# 1h는 검토용이 아니라 YouTube publishAt 미래 보장 버퍼 (슬롯 직전 업로드 시
# publishAt이 과거가 되어 API 실패하는 것 방지).
MIN_LEAD_HOURS_KO = 1
MIN_LEAD_HOURS_EN = 0


def _tz_for(channel: str) -> ZoneInfo | timezone:
    return _CHANNEL_TZ.get(channel, KST)


def _slot_hours_for(channel: str) -> list[int]:
    return _CHANNEL_SLOTS.get(channel, SLOT_HOURS_KST)


def _min_lead_hours_for(channel: str) -> int:
    return MIN_LEAD_HOURS_EN if channel == "en" else MIN_LEAD_HOURS_KO


def _now_in(tz: ZoneInfo | timezone) -> datetime:
    return datetime.now(UTC).astimezone(tz)


def _candidate_slots(
    channel: str,
    max_lookahead_days: int = 30,
    min_lead_hours: int | None = None,
) -> list[datetime]:
    """channel별 다음 빈 슬롯 후보 (시간순). MIN_LEAD_HOURS 이후.

    min_lead_hours: None이면 channel default (ko=1, en=0). 명시하면 override.
    """
    tz = _tz_for(channel)
    slot_hours = _slot_hours_for(channel)
    now = _now_in(tz)
    lead = min_lead_hours if min_lead_hours is not None else _min_lead_hours_for(channel)
    earliest = now + timedelta(hours=lead)
    slots: list[datetime] = []
    day = earliest.date()
    for _ in range(max_lookahead_days):
        for h in slot_hours:
            # ko 화/금 20시는 미드폼 업로드와 겹침 → 슬롯에서 제외
            if (
                channel == "ko"
                and h == MIDFORM_CLASH_HOUR_KO
                and day.weekday() in MIDFORM_WEEKDAYS_KO
            ):
                continue
            slot = datetime(day.year, day.month, day.day, h, 0, tzinfo=tz)
            if slot > earliest:
                slots.append(slot)
        day = day + timedelta(days=1)
    return slots


def _existing_scheduled_in(channel: str) -> set[datetime]:
    """해당 channel에서 이미 예약된 슬롯 시각 (channel-tz 분 단위)."""
    tz = _tz_for(channel)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT scheduled_at FROM shorts "
            "WHERE scheduled_at IS NOT NULL "
            "  AND status NOT IN ('rejected', 'error') "
            "  AND channel = ?",
            (channel,),
        ).fetchall()
    finally:
        conn.close()
    used: set[datetime] = set()
    for r in rows:
        sched = r["scheduled_at"]
        if not sched:
            continue
        try:
            dt = datetime.fromisoformat(sched)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            local = dt.astimezone(tz)
            used.add(local.replace(second=0, microsecond=0))
        except ValueError:
            continue
    return used


def assign_scheduled_at_for_pending(
    pending_short_ids: list[int] | None = None,
    channel: str = "ko",
    min_lead_hours: int | None = None,
) -> int:
    """status='generated' + scheduled_at IS NULL인 모먼트에 다음 빈 슬롯 할당.

    channel: 'ko' 또는 'en'. ko는 KST 4슬롯, en은 LA 2슬롯.
    pending_short_ids: 특정 행만 처리. None이면 모든 적격 행.
    min_lead_hours: None이면 channel default (ko=1, en=0). 명시하면 override.
    반환: 할당된 모먼트 수.
    """
    candidates = _candidate_slots(channel, min_lead_hours=min_lead_hours)
    used = _existing_scheduled_in(channel)

    conn = get_connection()
    assigned = 0
    try:
        sql = (
            "SELECT id, internal_id, notion_page_id FROM shorts "
            "WHERE status = 'generated' AND scheduled_at IS NULL "
            "  AND channel = ? "
        )
        params: tuple = (channel,)
        if pending_short_ids:
            placeholders = ",".join("?" * len(pending_short_ids))
            sql += f" AND id IN ({placeholders})"
            params = (channel, *pending_short_ids)
        sql += " ORDER BY id"
        rows = conn.execute(sql, params).fetchall()

        for r in rows:
            next_slot: datetime | None = None
            for c in candidates:
                key = c.replace(second=0, microsecond=0)
                if key not in used:
                    next_slot = c
                    used.add(key)
                    break
            if next_slot is None:
                log.warning(
                    "schedule.no_slot_available",
                    short_id=r["id"],
                    channel=channel,
                    lookahead_days=30,
                )
                break
            iso_local = next_slot.isoformat()
            conn.execute(
                "UPDATE shorts SET scheduled_at = ? WHERE id = ?",
                (iso_local, r["id"]),
            )
            conn.commit()
            if r["notion_page_id"]:
                try:
                    _push_scheduled_to_notion(r["notion_page_id"], next_slot)
                except Exception as e:
                    log.warning(
                        "schedule.notion_push_failed",
                        short_id=r["id"],
                        error=str(e),
                    )
            log.info(
                "schedule.assigned",
                short_id=r["id"],
                internal_id=r["internal_id"],
                channel=channel,
                slot=iso_local,
            )
            assigned += 1
        return assigned
    finally:
        conn.close()


def _push_scheduled_to_notion(page_id: str, slot_local: datetime) -> None:
    """노션 Scheduled At date property 업데이트."""
    from app.integrations.notion import _get_client

    client = _get_client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Scheduled At": {"date": {"start": slot_local.isoformat()}},
        },
    )


__all__ = [
    "MIN_LEAD_HOURS_KO",
    "MIN_LEAD_HOURS_EN",
    "SLOT_HOURS_KST",
    "SLOT_HOURS_LA",
    "assign_scheduled_at_for_pending",
]
