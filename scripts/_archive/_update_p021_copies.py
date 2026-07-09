"""1회용: P021 7개 모먼트의 copy1/copy2를 강화된 curator 결과로 교체.

흐름:
  1. SQLite shorts.copy1/copy2 UPDATE
  2. 노션 페이지 Hook (title) UPDATE → "copy1 / copy2"
"""

from __future__ import annotations

import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import _get_client

UPDATES = [
    ("26-P021-S01", "앞에서 소리는", "저는 반대입니다!"),
    ("26-P021-S02", "헤드스피드는", "이미 300 됩니다!"),
    ("26-P021-S03", "위로 휘두르면", "78.5 바로 나와요!"),
    ("26-P021-S04", "3가지만 하면", "비거리 그냥 늘어요!"),
    ("26-P021-S05", "오른쪽 어깨를", "대각선 높게 드세요!"),
    ("26-P021-S06", "대강 때려도", "볼스피드 70 나와요!"),
    ("26-P021-S07", "왼쪽으로 3시간 치면", "인생 볼스피드 옵니다!"),
]


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    client = _get_client()

    updated_db = 0
    updated_notion = 0
    for iid, c1, c2 in UPDATES:
        r = conn.execute(
            "SELECT id, notion_page_id FROM shorts WHERE internal_id=?",
            (iid,),
        ).fetchone()
        if not r:
            print(f"  {iid}: no row")
            continue

        # SQLite update
        conn.execute(
            "UPDATE shorts SET copy1=?, copy2=? WHERE id=?",
            (c1, c2, r["id"]),
        )
        updated_db += 1

        # Notion Hook (title) update — "copy1 / copy2"
        if r["notion_page_id"]:
            try:
                client.pages.update(
                    page_id=r["notion_page_id"],
                    properties={
                        "Hook": {"title": [{"text": {"content": f"{c1} / {c2}"}}]},
                    },
                )
                updated_notion += 1
                print(f"  {iid}: '{c1}' / '{c2}'")
            except Exception as e:
                print(f"  {iid}: notion FAIL: {e}")

    conn.commit()
    conn.close()
    print(f"\nDB updated: {updated_db}, Notion updated: {updated_notion}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
