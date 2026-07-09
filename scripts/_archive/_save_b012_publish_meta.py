"""1회용: B012 publish-meta-writer 결과 → SQLite + 노션."""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update

META = {
    "26-B012-S01": {
        "title": "헤드 스피드 같은데 비거리 20m 차이? 4가지 구질의 비밀",
        "description": "같은 헤드 스피드에도 비거리가 10~20m 차이나는 이유, 바로 구질에 있습니다. 텀블러, 라이저, 플로터, 너클볼 — 4가지 구질의 특성을 한 영상에 정리했어요. 내 볼이 어디에 속하는지 확인하고 비거리 +20m 만들어보세요!\n\n#골프 #골프레슨 #골프스윙 #비거리 #헤드스피드 #너클볼 #구질 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "헤드스피드", "비거리", "너클볼", "구질", "텀블러", "라이저", "플로터", "골프팁", "golf", "golfswing", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#헤드스피드", "#너클볼", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-B012-S02": {
        "title": "어택앵글 2도 차이로 비거리 10m가 바뀐다 — 같은 볼스피드인데 왜?",
        "description": "같은 볼스피드 80mps에서도 어택앵글이 -2도냐 +2도냐에 따라 비거리가 7~10m 차이 납니다. 숫자로 보면 왜 어택앵글 조정 한 번이 비거리에 직격인지 바로 이해돼요. 너클볼 셋업과 함께 보면 +20m까지 노려볼 수 있습니다.\n\n#골프 #골프레슨 #골프스윙 #어택앵글 #비거리 #드라이버비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "어택앵글", "비거리", "드라이버비거리", "볼스피드", "너클볼", "스윙크루", "골프팁", "golf", "golfswing", "golftips", "attackangle", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#어택앵글", "#비거리", "#드라이버비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-B012-S03": {
        "title": "비거리 20m 늘리는 세 가지 세팅 — 티 높이·볼 포지션·체중이동",
        "description": "드라이버 비거리 20m 늘리는 세 가지 세팅을 정리했습니다. 티 높이, 볼 포지션, 체중이동 — 이 셋만 점검해도 임팩트가 달라져요. 라운드 전에 꼭 체크해보세요!\n\n#골프 #골프레슨 #드라이버 #비거리 #골프스윙 #shorts #스윙크루 #골프팁 #드라이버비거리",
        "tags": ["골프", "골프레슨", "드라이버", "비거리", "드라이버비거리", "골프스윙", "티높이", "볼포지션", "체중이동", "스윙크루", "golf", "golfswing", "golftips", "driver", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#드라이버", "#비거리", "#골프스윙", "#shorts", "#스윙크루", "#골프팁", "#드라이버비거리"],
    },
    "26-B012-S04": {
        "title": "티 1cm만 높여도 스핀이 확 줄어든다 — 너클볼 비거리 +20m",
        "description": "드라이버 비거리를 늘리고 싶다면 티 높이부터 점검해보세요. 단 1cm만 높여도 백스핀이 줄어들면서 볼이 더 멀리 굴러갑니다. 너클볼 만드는 세팅 중 가장 쉽고 즉시 효과 보는 첫 번째 팁이에요.\n\n#골프 #골프레슨 #드라이버 #비거리 #너클볼 #티높이 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "드라이버", "비거리", "너클볼", "티높이", "백스핀", "드라이버팁", "골프스윙", "golf", "golfswing", "golftips", "driver", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#드라이버", "#비거리", "#너클볼", "#티높이", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-B012-S05": {
        "title": "아이언이 안 맞는 진짜 이유 — 손목 플리핑 한 번에 잡기",
        "description": "어퍼블로우 하려다 체중이동 없이 손목만 뒤집고 계신가요? 드라이버는 맞는데 아이언이 안 맞는다면 플리핑이 범인입니다. 손목을 잠그고 체중으로 치는 법, 한 영상에 정리했어요.\n\n#골프 #골프레슨 #골프스윙 #아이언샷 #플리핑 #비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "아이언", "아이언샷", "플리핑", "손목", "체중이동", "비거리", "어퍼블로우", "golf", "golfswing", "golftips", "irons", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#아이언샷", "#플리핑", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
}


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    saved = 0
    for iid, meta in META.items():
        r = conn.execute(
            "SELECT id, notion_page_id FROM shorts WHERE internal_id=?", (iid,),
        ).fetchone()
        if not r:
            print(f"  {iid}: no row")
            continue
        meta_json = json.dumps(meta, ensure_ascii=False)
        conn.execute(
            "UPDATE shorts SET publish_meta_json=? WHERE id=?",
            (meta_json, r["id"]),
        )
        if r["notion_page_id"]:
            try:
                notion_update(
                    r["notion_page_id"], "generated",
                    title=meta["title"], description=meta["description"],
                )
                saved += 1
                print(f"  {iid}: '{meta['title'][:50]}...'")
            except Exception as e:
                print(f"  {iid} notion FAIL: {e}")
    conn.commit()
    conn.close()
    print(f"\nsaved: {saved}/{len(META)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
