"""Phase 7 cron: Scheduled At 자동 채움.

매일 4 슬롯 (KST 7/11/17/20시) 중 다음 빈 슬롯에 영빈 ✅한 모먼트 자동 예약.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.storage.db import get_connection
from app.utils.logger import get_logger

log = get_logger(__name__)

KST = timezone(timedelta(hours=9))
SLOT_HOURS_KST = [7, 11, 17, 20]
# 영빈 검토 시간 보장: ffmpeg 완료 후 최소 N시간 미래 슬롯에만 예약.
MIN_LEAD_HOURS = 24


def _kst_now() -> datetime:
    return datetime.now(UTC).astimezone(KST)


def _candidate_slots(
    now_kst: datetime, max_lookahead_days: int = 30,
) -> list[datetime]:
    """now + MIN_LEAD_HOURS 이후의 슬롯 후보 (시간순)."""
    earliest = now_kst + timedelta(hours=MIN_LEAD_HOURS)
    slots: list[datetime] = []
    day = earliest.date()
    for _ in range(max_lookahead_days):
        for h in SLOT_HOURS_KST:
            slot = datetime(day.year, day.month, day.day, h, 0, tzinfo=KST)
            if slot > earliest:
                slots.append(slot)
        day = day + timedelta(days=1)
    return slots


def _existing_scheduled_kst() -> set[datetime]:
    """SQLite shorts에 이미 예약된 Scheduled At 시간 (KST 분 단위 정확히 매치용)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT scheduled_at FROM shorts "
            "WHERE scheduled_at IS NOT NULL "
            "  AND status NOT IN ('rejected', 'error')"
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
                dt = dt.replace(tzinfo=KST)
            kst = dt.astimezone(KST)
            used.add(kst.replace(second=0, microsecond=0))
        except ValueError:
            continue
    return used


def assign_scheduled_at_for_pending(
    pending_short_ids: list[int] | None = None,
) -> int:
    """status='generated' + scheduled_at IS NULL인 모먼트에 다음 빈 슬롯 할당.

    pending_short_ids: 특정 행만 처리. None이면 모든 적격 행.
    반환: 할당된 모먼트 수.
    """
    now_kst = _kst_now()
    candidates = _candidate_slots(now_kst)
    used = _existing_scheduled_kst()

    conn = get_connection()
    assigned = 0
    try:
        sql = (
            "SELECT id, internal_id, notion_page_id FROM shorts "
            "WHERE status = 'generated' AND scheduled_at IS NULL "
        )
        params: tuple[int, ...] = ()
        if pending_short_ids:
            placeholders = ",".join("?" * len(pending_short_ids))
            sql += f" AND id IN ({placeholders})"
            params = tuple(pending_short_ids)
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
                    short_id=r["id"], lookahead_days=30,
                )
                break
            iso_kst = next_slot.isoformat()
            conn.execute(
                "UPDATE shorts SET scheduled_at = ? WHERE id = ?",
                (iso_kst, r["id"]),
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
                slot=iso_kst,
            )
            assigned += 1
        return assigned
    finally:
        conn.close()


def _push_scheduled_to_notion(page_id: str, slot_kst: datetime) -> None:
    """노션 Scheduled At date property 업데이트."""
    from app.integrations.notion import _get_client

    client = _get_client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Scheduled At": {"date": {"start": slot_kst.isoformat()}},
        },
    )


__all__ = [
    "MIN_LEAD_HOURS",
    "SLOT_HOURS_KST",
    "assign_scheduled_at_for_pending",
]
