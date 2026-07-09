"""1회용: P021 6개 모먼트 강화 blur로 mp4 재생성 + scheduled_at 할당 + R2/YouTube 예약.

전제:
  - production edit.py 강화 blur 적용 완료
  - production approve.py skip_publish_meta=True 시 publish_meta_json 보존 fix
  - P021 publish_meta_json 6개 이미 publish-meta-writer 에이전트 결과로 저장됨

흐름:
  1. P021 S01-S06 status reset 'generated' → 'approved' (SQLite + 노션)
  2. 기존 mp4 삭제 (덮어쓰기 보장)
  3. process_approved(skip_publish_meta=True) — 새 blur mp4 재생성, publish_meta 보존
  4. schedule.assign_scheduled_at_for_pending — 슬롯 할당
  5. publish_ready(skip_gemini_fallback=True) — R2 + YouTube 예약
  6. 결과 보고
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update
from app.pipeline.approve import process_approved
from app.pipeline.publish import publish_ready
from app.pipeline.schedule import assign_scheduled_at_for_pending

INTERNAL_IDS = [f"26-P021-S{i:02d}" for i in range(1, 7)]


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row

    print("[1/5] status reset 'generated' → 'approved'")
    reset = 0
    for iid in INTERNAL_IDS:
        r = conn.execute(
            "SELECT id, notion_page_id, generated_path FROM shorts WHERE internal_id=?",
            (iid,),
        ).fetchone()
        if not r:
            print(f"  {iid}: no row")
            continue
        conn.execute(
            "UPDATE shorts SET status='approved' WHERE id=?",
            (r["id"],),
        )
        if r["notion_page_id"]:
            try:
                notion_update(r["notion_page_id"], "approved")
            except Exception as e:
                print(f"  {iid} notion fail: {e}")
        # 기존 mp4 삭제 (덮어쓰기 보장)
        if r["generated_path"]:
            mp4 = Path(r["generated_path"])
            if mp4.exists():
                try:
                    mp4.unlink()
                    print(f"  {iid}: status reset + mp4 deleted")
                except OSError as e:
                    print(f"  {iid}: mp4 delete fail: {e}")
            else:
                print(f"  {iid}: status reset (mp4 already gone)")
        else:
            print(f"  {iid}: status reset (no mp4 path)")
        reset += 1
    conn.commit()
    conn.close()
    print(f"  reset count: {reset}\n")

    print("[2/5] process_approved(skip_publish_meta=True) — 새 blur mp4 생성")
    n_proc = process_approved(skip_publish_meta=True)
    print(f"  processed: {n_proc}\n")

    print("[3/5] schedule.assign_scheduled_at_for_pending")
    n_sched = assign_scheduled_at_for_pending(channel="ko")
    print(f"  assigned: {n_sched}\n")

    print("[4/5] publish_ready(skip_gemini_fallback=True) — R2 + YouTube 예약")
    n_pub = publish_ready(skip_gemini_fallback=True)
    print(f"  published: {n_pub}\n")

    print("[5/5] 최종 상태 확인")
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT internal_id, status, scheduled_at, published_urls "
        "FROM shorts WHERE internal_id LIKE '26-P021-S%' ORDER BY internal_id"
    ).fetchall()
    for r in rows:
        sched = r["scheduled_at"] or "(none)"
        urls = r["published_urls"] or "(none)"
        print(f"  {r['internal_id']} [{r['status']:10}] sched={sched[:19]} urls={urls[:60]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
