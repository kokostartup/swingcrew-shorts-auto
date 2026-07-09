"""1회용: P021 6개 모먼트 빈 슬롯으로 reschedule.

문제: assign_scheduled_at_for_pending 기본 lead=24h라서 manual 처리 시 영빈이 노션 검토
이미 완료했는데도 24시간 후 슬롯부터 잡힘. P021을 가까운 빈 슬롯으로 당김.

흐름:
  1. P021 6개 현재 slot 정보 (youtube video_id, notion page_id)
  2. P021 제외 모든 scheduled 모먼트 slot 모음 → "사용 중" set
  3. candidate slots (lead=1h) 중 사용 중이 아닌 것 6개 추출
  4. 각 P021 모먼트에 새 slot 할당:
     - YouTube videos.update publishAt 변경
     - SQLite scheduled_at 변경
     - 노션 Scheduled At 변경
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.youtube import update_publish_at
from app.pipeline.schedule import _candidate_slots, _push_scheduled_to_notion

KST = ZoneInfo("Asia/Seoul")
INTERNAL_IDS = [f"26-P021-S{i:02d}" for i in range(1, 7)]


def _extract_video_id(published_urls_json: str | None) -> str | None:
    if not published_urls_json:
        return None
    try:
        pu = json.loads(published_urls_json)
    except json.JSONDecodeError:
        return None
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", pu.get("youtube") or "")
    return m.group(1) if m else None


def _scheduled_at_to_utc_iso(scheduled_at_local_iso: str) -> str:
    dt = datetime.fromisoformat(scheduled_at_local_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row

    # P021 6개
    p021_rows = conn.execute(
        "SELECT id, internal_id, notion_page_id, scheduled_at, published_urls "
        "FROM shorts WHERE internal_id IN ({}) ORDER BY internal_id".format(
            ",".join("?" * len(INTERNAL_IDS))
        ),
        INTERNAL_IDS,
    ).fetchall()
    if len(p021_rows) != 6:
        print(f"WARN: P021 모먼트 수가 6이 아님 ({len(p021_rows)})")

    # 다른 채널 모든 사용 중 slot
    other_rows = conn.execute(
        "SELECT scheduled_at FROM shorts WHERE channel='ko' "
        "AND scheduled_at IS NOT NULL AND status NOT IN ('rejected','error') "
        "AND id NOT IN ({})".format(",".join("?" * len(p021_rows))),
        [r["id"] for r in p021_rows],
    ).fetchall()
    used: set[datetime] = set()
    for r in other_rows:
        try:
            dt = datetime.fromisoformat(r["scheduled_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            used.add(dt.astimezone(KST).replace(second=0, microsecond=0))
        except ValueError:
            continue

    # 빈 슬롯 후보 (lead=1h)
    candidates = _candidate_slots("ko", min_lead_hours=1)
    free_slots = [s for s in candidates if s.replace(second=0, microsecond=0) not in used]
    if len(free_slots) < len(p021_rows):
        print(f"ERROR: 빈 슬롯 부족 ({len(free_slots)}<{len(p021_rows)})")
        return 1

    now_kst = datetime.now(KST)
    print(f"now KST: {now_kst.isoformat()}")
    print(f"P021 6개 + 빈 슬롯 매칭:\n")
    new_slots = free_slots[:6]
    for r, slot in zip(p021_rows, new_slots, strict=True):
        old = r["scheduled_at"]
        print(f"  {r['internal_id']}: {old[:19] if old else '(none)'} → {slot.isoformat()}")
    print()

    updated = 0
    for r, slot in zip(p021_rows, new_slots, strict=True):
        iid = r["internal_id"]
        page_id = r["notion_page_id"]
        video_id = _extract_video_id(r["published_urls"])
        new_local = slot.isoformat()
        new_utc = _scheduled_at_to_utc_iso(new_local)

        # YouTube publishAt 변경
        if video_id:
            try:
                update_publish_at(video_id, new_utc, channel="ko")
                print(f"  {iid}: YouTube publishAt updated → {new_utc}")
            except Exception as e:
                print(f"  {iid}: YouTube update FAIL: {e}")
                continue
        else:
            print(f"  {iid}: no YouTube video_id, skip YouTube")

        # SQLite scheduled_at 변경
        conn.execute(
            "UPDATE shorts SET scheduled_at=? WHERE id=?",
            (new_local, r["id"]),
        )

        # 노션 Scheduled At 변경
        if page_id:
            try:
                _push_scheduled_to_notion(page_id, slot)
                print(f"  {iid}: notion Scheduled At updated")
            except Exception as e:
                print(f"  {iid}: notion update FAIL: {e}")

        updated += 1

    conn.commit()
    conn.close()
    print(f"\nupdated: {updated}/{len(p021_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
