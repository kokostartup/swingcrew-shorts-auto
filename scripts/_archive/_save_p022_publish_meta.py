"""1회용: P022 publish-meta-writer 결과 → SQLite + 노션.

S06 title에 em-dash 들어가서 수동으로 콤마로 치환 (에이전트 룰 위반 회피).
"""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update

META = {
    "26-P022-S01": {
        "title": "다운블로우는 손으로 찍는 게 아니다, 오른발 하나로 끝낸다",
        "description": "다운블로우를 손으로 찍어치려다 뒷땅 나는 분들 꼭 보세요. 손목을 쓰는 게 아니라 체중 이동과 최하점 컨트롤이 핵심입니다. 오른발 움직임 하나만 이해해도 뒷땅이 사라져요. 아이언 컨택이 단단해지는 한 가지를 정리했습니다.\n\n#골프 #골프레슨 #골프스윙 #다운블로우 #아이언샷 #뒷땅 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "다운블로우", "아이언샷", "뒷땅", "체중이동", "골프스윙", "아이언", "오른발", "골프팁", "golf", "golfswing", "golftips", "irons", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#다운블로우", "#아이언샷", "#뒷땅", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P022-S03": {
        "title": "5번 아이언은 드라이버처럼 치세요 거리 안 나오는 분 필독",
        "description": "5번 아이언인데 7번이랑 거리가 비슷하다면 다운블로우가 답이 아닐 수도 있어요. 5번은 오히려 드라이버처럼 살짝 올려치는 느낌으로 가져가야 거리가 살아납니다. 통념을 깨는 롱아이언 교정 팁, 이 한 가지만 바꿔보세요.\n\n#골프 #골프레슨 #골프스윙 #5번아이언 #롱아이언 #비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "5번아이언", "롱아이언", "아이언샷", "비거리", "드라이버스윙", "올려치기", "다운블로우", "골프스윙", "golf", "golfswing", "golftips", "irons", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#5번아이언", "#롱아이언", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P022-S05": {
        "title": "다리로 점프하면 100% 뒷땅! 아이언 다운블로우는 골반 회전이 답",
        "description": "아이언에서 자꾸 뒷땅이 나는 이유, 다리로 점프하기 때문입니다. 다운스윙에서 오른발을 어떻게 써야 하는지, 그리고 골반 회전 한 가지만 바꿔도 다운블로우가 살아납니다. 오늘 라운드 가기 전에 꼭 확인하세요.\n\n#골프 #골프레슨 #아이언샷 #다운블로우 #뒷땅방지 #골반회전 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "아이언샷", "다운블로우", "뒷땅", "골반회전", "오른발", "골프스윙", "골프팁", "golf", "golfswing", "golftips", "iron", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#아이언샷", "#다운블로우", "#뒷땅방지", "#골반회전", "#shorts", "#스윙크루", "#골프팁"],
    },
    # ★ em-dash 수동 치환: "왼쪽이 저절로 들려요 — 아이언" → "왼쪽이 저절로 들려요, 아이언"
    "26-P022-S06": {
        "title": "오른발만 차면 왼쪽이 저절로 들려요, 아이언 다운블로우 핵심",
        "description": "아이언 뒷땅 고민이라면 오른발 하나만 신경 써보세요. 오른발을 차듯 밀어주면 왼쪽 골반이 저절로 들리면서 다리가 굴러갑니다. 다운블로우의 진짜 메커니즘, 영상에서 한 번에 정리했어요.\n\n#골프 #골프레슨 #골프스윙 #아이언샷 #다운블로우 #뒷땅방지 #shorts #스윙크루 #골프팁 #아이언레슨",
        "tags": ["골프", "골프레슨", "아이언샷", "다운블로우", "뒷땅방지", "오른발", "골프스윙", "체중이동", "아이언레슨", "골프팁", "golf", "golfswing", "irons", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#아이언샷", "#다운블로우", "#뒷땅방지", "#shorts", "#스윙크루", "#골프팁", "#아이언레슨"],
    },
    "26-P022-S07": {
        "title": "아이언 찍어치기 10년이면 평생 뒷땅 못 고쳐요",
        "description": "다운블로우는 손으로 찍는 게 아닙니다. 10년 동안 찍어치기 습관 들이면 드라이버 슬라이스까지 따라오는 악순환이 시작돼요. 아이언 컨택을 살리는 진짜 다운블로우 감각을 한 번에 정리했습니다.\n\n#골프 #골프레슨 #아이언 #다운블로우 #뒷땅 #찍어치기 #골프스윙 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "아이언", "다운블로우", "뒷땅", "찍어치기", "아이언샷", "골프스윙", "슬라이스", "golf", "golfswing", "irons", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#아이언", "#다운블로우", "#뒷땅", "#찍어치기", "#골프스윙", "#shorts", "#스윙크루", "#골프팁"],
    },
}


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    saved = 0
    for iid, meta in META.items():
        # 안전망: em-dash/en-dash/콜론 자동 치환 (혹시 에이전트가 또 어겼을 때)
        for key in ("title", "description"):
            meta[key] = meta[key].replace("—", ",").replace("–", ",").replace(": ", " ")

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
