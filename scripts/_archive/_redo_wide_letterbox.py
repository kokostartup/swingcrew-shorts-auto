"""1회용: 노션 'scheduled' 페이지 (≥ 6/1 23:00 KST) 전부 wide letterbox로 재처리.

흐름:
  1. 노션 ko + en scheduled fetch (cutoff filter)
  2. YouTube delete (channel별 OAuth)
  3. SQLite reset (face_segments/face_center_x NULL, status='approved')
  4. 노션 status='승인'
  5. process_approved → ffmpeg 재생성
  6. publish_ready → R2 + YouTube 새 upload
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from app.integrations.notion import (
    _get_client as notion_client,
)
from app.integrations.notion import (
    list_pages_by_status,
)
from app.integrations.notion import (
    update_status as notion_update,
)
from app.integrations.youtube import get_credentials
from app.pipeline.approve import process_approved
from app.pipeline.publish import publish_ready
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

log = get_logger(__name__)
KST = timezone(timedelta(hours=9))
CUTOFF = datetime(2026, 6, 1, 23, 0, tzinfo=KST)


def _fetch_scheduled() -> list[dict]:
    client = notion_client()
    out: list[dict] = []
    for ch in ("ko", "en"):
        pages = list_pages_by_status("scheduled", channel=ch)
        for p in pages:
            sched_str = p.get("scheduled_at")
            if not sched_str:
                continue
            try:
                sched = datetime.fromisoformat(sched_str)
            except ValueError:
                continue
            if sched < CUTOFF:
                continue
            page = client.pages.retrieve(page_id=p["id"])
            props = page.get("properties", {})
            iid_rt = props.get("Internal ID", {}).get("rich_text", [])
            iid = "".join(t.get("plain_text") or "" for t in iid_rt).strip()
            if iid:
                out.append(
                    {
                        "iid": iid,
                        "page_id": p["id"],
                        "channel": ch,
                        "sched": sched_str,
                        "sched_dt": sched,
                    }
                )
    out.sort(key=lambda x: x["sched_dt"])
    return out


def _cross_ref_sqlite(cands: list[dict]) -> list[dict]:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    matched: list[dict] = []
    for c in cands:
        r = conn.execute(
            "SELECT id, published_urls FROM shorts WHERE notion_page_id = ?",
            (c["page_id"],),
        ).fetchone()
        if not r:
            continue
        pu = json.loads(r["published_urls"] or "{}")
        m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", pu.get("youtube", ""))
        matched.append(
            {
                **c,
                "shorts_id": r["id"],
                "youtube_id": m.group(1) if m else None,
            }
        )
    conn.close()
    return matched


def _delete_youtube_videos(items: list[dict]) -> dict:
    from googleapiclient.discovery import build

    clients_per_ch: dict = {}
    deleted, failed = 0, 0
    for c in items:
        if not c["youtube_id"]:
            continue
        ch = c["channel"]
        if ch not in clients_per_ch:
            clients_per_ch[ch] = build(
                "youtube",
                "v3",
                credentials=get_credentials(ch),
                cache_discovery=False,
            )
        try:
            clients_per_ch[ch].videos().delete(id=c["youtube_id"]).execute()
            deleted += 1
            print(f"  deleted: {c['iid']} ({c['youtube_id']})", flush=True)
        except Exception as e:
            failed += 1
            print(f"  FAIL delete {c['iid']} ({c['youtube_id']}): {e}", flush=True)
    return {"deleted": deleted, "failed": failed}


def _reset_sqlite_and_notion(items: list[dict]) -> int:
    conn = sqlite3.connect("data/state.db")
    reset_count = 0
    for c in items:
        conn.execute(
            "UPDATE shorts SET face_segments = NULL, face_center_x = NULL, "
            "status = 'approved', published_urls = NULL WHERE id = ?",
            (c["shorts_id"],),
        )
        try:
            notion_update(c["page_id"], "approved")
        except Exception as e:
            print(f"  notion update fail {c['iid']}: {e}", flush=True)
        reset_count += 1
    conn.commit()
    conn.close()
    return reset_count


def main() -> None:
    print("=== _redo_wide_letterbox start ===", flush=True)
    cands = _fetch_scheduled()
    print(f"노션 scheduled (≥ {CUTOFF.isoformat()}): {len(cands)}", flush=True)
    items = _cross_ref_sqlite(cands)
    print(f"SQLite 매칭: {len(items)}", flush=True)

    print("\n[1/4] YouTube delete", flush=True)
    r = _delete_youtube_videos(items)
    print(f"  deleted={r['deleted']}, failed={r['failed']}", flush=True)

    print("\n[2/4] SQLite + 노션 reset → approved", flush=True)
    n = _reset_sqlite_and_notion(items)
    print(f"  reset={n}", flush=True)

    print("\n[3/4] process_approved (ffmpeg 재생성)", flush=True)
    n_proc = process_approved()
    print(f"  processed={n_proc}", flush=True)

    print("\n[4/4] publish_ready (R2 + YouTube 새 upload)", flush=True)
    n_pub = publish_ready()
    print(f"  published={n_pub}", flush=True)

    print("\n=== done ===", flush=True)


if __name__ == "__main__":
    main()
