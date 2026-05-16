"""Phase 7 cron: Scheduled At 자동 채움.

채널별 슬롯 (channel-aware):
  - 한국 (ko): 매일 KST 07/11/17/20 (4슬롯)
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

SLOT_HOURS_KST = [7, 11, 17, 20]
SLOT_HOURS_LA = [7, 20]  # 영어 채널: 미서부 morning + evening prime time

# channel별 timezone + 슬롯 시간.
_CHANNEL_TZ: dict[str, ZoneInfo | timezone] = {"ko": KST, "en": LA}
_CHANNEL_SLOTS: dict[str, list[int]] = {"ko": SLOT_HOURS_KST, "en": SLOT_HOURS_LA}

# 영빈 검토 시간 보장: ffmpeg 완료 후 최소 N시간 미래 슬롯에만 예약.
# 영어 채널은 자동 흐름이라 검토 시간 불필요 → 0시간으로.
MIN_LEAD_HOURS_KO = 24
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
    channel: str, max_lookahead_days: int = 30,
) -> list[datetime]:
    """channel별 다음 빈 슬롯 후보 (시간순). MIN_LEAD_HOURS 이후."""
    tz = _tz_for(channel)
    slot_hours = _slot_hours_for(channel)
    now = _now_in(tz)
    earliest = now + timedelta(hours=_min_lead_hours_for(channel))
    slots: list[datetime] = []
    day = earliest.date()
    for _ in range(max_lookahead_days):
        for h in slot_hours:
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
) -> int:
    """status='generated' + scheduled_at IS NULL인 모먼트에 다음 빈 슬롯 할당.

    channel: 'ko' 또는 'en'. ko는 KST 4슬롯, en은 LA 2슬롯.
    pending_short_ids: 특정 행만 처리. None이면 모든 적격 행.
    반환: 할당된 모먼트 수.
    """
    candidates = _candidate_slots(channel)
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
                    short_id=r["id"], channel=channel, lookahead_days=30,
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
                        short_id=r["id"], error=str(e),
                    )
            log.info(
                "schedule.assigned",
                short_id=r["id"], internal_id=r["internal_id"],
                channel=channel, slot=iso_local,
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
