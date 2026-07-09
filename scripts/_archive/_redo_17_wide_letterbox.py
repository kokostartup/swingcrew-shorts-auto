"""1회용: 17 모먼트 (B008/P017/B009/P018) wide letterbox 재처리.

흐름:
  1. 17 internal_id의 YouTube video delete
  2. SQLite face_segments + face_center_x NULL + status='approved' + published_urls NULL
     (scheduled_at은 그대로 유지 — 영빈이 설정한 슬롯 순서 보존)
  3. 노션 status='승인'
  4. process_approved → 새 wide letterbox mp4 (수정된 approve.py가 항상 새 룰 적용)
  5. publish_ready → 새 R2 + YouTube 새 upload (publishAt = scheduled_at 그대로)
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys

from app.integrations.notion import update_status as notion_update
from app.integrations.youtube import get_credentials
from app.pipeline.approve import process_approved
from app.pipeline.publish import publish_ready

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

INTERNAL_IDS = [
    "26-B008-S01",
    "26-B008-S02",
    "26-B008-S03",
    "26-B008-S04",
    "26-B008-S05",
    "26-P017-S01",
    "26-P017-S02",
    "26-P017-S03",
    "26-P017-S04",
    "26-P017-S05",
    "26-B009-S01",
    "26-B009-S02",
    "26-B009-S03",
    "26-P018-S01",
    "26-P018-S02",
    "26-P018-S03",
    "26-P018-S04",
]


def main() -> None:
    print("=== _redo_17_wide_letterbox start ===", flush=True)

    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row

    # Step 1: YouTube delete
    print("\n[1/4] YouTube delete (ko channel)", flush=True)
    from googleapiclient.discovery import build

    yt_client = build(
        "youtube",
        "v3",
        credentials=get_credentials("ko"),
        cache_discovery=False,
    )
    deleted = 0
    items = []
    for iid in INTERNAL_IDS:
        r = conn.execute(
            "SELECT id, notion_page_id, published_urls FROM shorts WHERE internal_id = ?",
            (iid,),
        ).fetchone()
        if not r:
            print(f"  skip (no SQLite row): {iid}")
            continue
        pu = json.loads(r["published_urls"] or "{}")
        m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", pu.get("youtube", ""))
        yt_id = m.group(1) if m else None
        items.append(
            {"iid": iid, "shorts_id": r["id"], "page_id": r["notion_page_id"], "yt_id": yt_id}
        )
        if not yt_id:
            print(f"  skip (no YouTube id): {iid}")
            continue
        try:
            yt_client.videos().delete(id=yt_id).execute()
            deleted += 1
            print(f"  deleted: {iid} ({yt_id})", flush=True)
        except Exception as e:
            print(f"  FAIL: {iid} ({yt_id}): {e}", flush=True)
    print(f"  total deleted: {deleted}", flush=True)

    # Step 2: SQLite reset (scheduled_at 유지)
    print("\n[2/4] SQLite reset (face_segments/face_center_x NULL, status='approved')", flush=True)
    reset = 0
    for it in items:
        conn.execute(
            "UPDATE shorts SET face_segments = NULL, face_center_x = NULL, "
            "status = 'approved', published_urls = NULL WHERE id = ?",
            (it["shorts_id"],),
        )
        reset += 1
    conn.commit()
    print(f"  total reset: {reset}", flush=True)

    # Step 3: 노션 status='승인'
    print("\n[3/4] 노션 status='승인'", flush=True)
    notion_ok = 0
    for it in items:
        try:
            notion_update(it["page_id"], "approved")
            notion_ok += 1
        except Exception as e:
            print(f"  notion FAIL {it['iid']}: {e}", flush=True)
    print(f"  total notion updated: {notion_ok}", flush=True)

    conn.close()

    # Step 4: process_approved + publish_ready
    print("\n[4/4] process_approved (새 wide letterbox)", flush=True)
    n_proc = process_approved()
    print(f"  processed: {n_proc}", flush=True)

    # 검증
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    err = conn.execute(
        "SELECT internal_id FROM shorts WHERE status='error' AND internal_id IN ({})".format(
            ",".join("?" * len(INTERNAL_IDS))
        ),
        INTERNAL_IDS,
    ).fetchall()
    if err:
        print(
            f"  WARNING: process_approved error rows: {[r['internal_id'] for r in err]}", flush=True
        )
    conn.close()

    print("\n[5/4] publish_ready (새 R2 + YouTube)", flush=True)
    n_pub = publish_ready()
    print(f"  published: {n_pub}", flush=True)

    print("\n=== done ===", flush=True)


if __name__ == "__main__":
    main()
