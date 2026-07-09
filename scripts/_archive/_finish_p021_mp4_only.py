"""1회용: P021 S05/S06 mp4 추가 생성 + S04 SQLite 보정 + Gemini publish_meta 삭제.

publish_meta는 publish-meta-writer 에이전트로 별도 처리 예정.

흐름:
  1. S04 generated_path가 disk에는 있는데 SQLite 미반영 → 보정
  2. S05, S06 process_approved (generate_publish_meta monkey-patch로 skip)
  3. S01-S03의 Gemini publish_meta_json 삭제 (다음 에이전트가 새로 쓰도록)
  4. 노션 Title/Description 클리어 (에이전트 결과로 덮어쓰기 위함)
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import _get_client


def _skip_publish_meta(*args, **kwargs):
    raise RuntimeError("agent path — Gemini publish_meta skipped intentionally")


def main() -> int:
    # Step 1: S04 SQLite 보정 (mp4 이미 disk에 있음)
    print("[1/4] S04 SQLite 보정...")
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    s04 = conn.execute(
        "SELECT id, generated_path, status FROM shorts WHERE internal_id='26-P021-S04'"
    ).fetchone()
    s04_path = "outputs/shorts/26-P021-S04.mp4"
    if Path(s04_path).exists() and not s04["generated_path"]:
        conn.execute(
            "UPDATE shorts SET generated_path=?, status='generated', publish_meta_json=NULL "
            "WHERE id=?",
            (s04_path, s04["id"]),
        )
        print(f"  S04 보정 (generated_path={s04_path})")
    else:
        print(f"  S04 skip (path={s04['generated_path']} status={s04['status']})")
    conn.commit()
    conn.close()

    # Step 2: S05/S06 mp4 생성 — Gemini publish_meta monkey-patch
    print("\n[2/4] S05/S06 mp4 생성 (publish_meta skip)...")
    from app.pipeline import approve as approve_mod
    from app.pipeline import publish_meta as publish_meta_mod

    approve_mod.generate_publish_meta = _skip_publish_meta
    publish_meta_mod.generate_publish_meta = _skip_publish_meta
    from app.pipeline.approve import process_approved

    n = process_approved()
    print(f"  processed (mp4 only): {n}")

    # Step 3: S01-S03 publish_meta_json 삭제
    print("\n[3/4] S01-S03 Gemini publish_meta_json 삭제...")
    conn = sqlite3.connect("data/state.db")
    cleared = conn.execute(
        "UPDATE shorts SET publish_meta_json=NULL "
        "WHERE internal_id IN ('26-P021-S01','26-P021-S02','26-P021-S03')"
    ).rowcount
    conn.commit()
    print(f"  cleared SQLite rows: {cleared}")

    # Step 4: S01-S03 노션 Title/Description 클리어
    print("\n[4/4] S01-S03 노션 Title/Description 클리어...")
    rows = conn.execute(
        "SELECT internal_id, notion_page_id FROM shorts "
        "WHERE internal_id IN ('26-P021-S01','26-P021-S02','26-P021-S03')"
    ).fetchall()
    client = _get_client()
    cleared_n = 0
    for r in rows:
        nid, pid = r
        if not pid:
            continue
        try:
            client.pages.update(
                page_id=pid,
                properties={
                    "Title": {"rich_text": []},
                    "Description": {"rich_text": []},
                },
            )
            print(f"  {nid} cleared")
            cleared_n += 1
        except Exception as e:
            print(f"  {nid} FAIL: {e}")
    conn.close()
    print(f"  cleared notion: {cleared_n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
