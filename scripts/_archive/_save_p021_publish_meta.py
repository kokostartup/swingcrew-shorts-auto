"""1회용: P021 publish-meta-writer 에이전트 결과를 SQLite + 노션에 저장."""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update

META = {
    "26-P021-S01": {
        "title": "드라이버 앞에서 소리? 저는 반대입니다 — 임팩트 직전 가속이 진짜입니다",
        "description": "'앞에서 소리를 내라'는 말, 사실 맞지 않습니다. 볼스피드를 높이는 진짜 원리는 임팩트 직전 최대 가속 — 자동차가 0에서 100km/h로 치고 나가는 것과 같습니다. 드릴로 직접 확인해보세요.\n\n#골프 #드라이버 #골프레슨 #비거리 #볼스피드 #골프스윙 #골프팁 #스윙크루 #shorts",
        "tags": ["골프", "드라이버", "골프레슨", "비거리", "볼스피드", "골프스윙", "임팩트", "골프팁", "드라이버스윙", "golf", "driver", "golfswing", "ballspeed", "golftips", "shorts"],
        "hashtags": ["#골프", "#드라이버", "#골프레슨", "#비거리", "#볼스피드", "#골프스윙", "#스윙크루", "#shorts"],
    },
    "26-P021-S02": {
        "title": "헤드스피드 52면 300야드 이미 됩니다 — 볼스피드 76.9→77.7 실측 확인",
        "description": "헤드스피드 52가 나온다면 300야드 비거리는 이미 잠재능력 안에 있습니다. 스윙을 바꾸기 전에 볼스피드를 먼저 확인해보세요 — 76.9에서 77.7로 오른 실제 수치가 그 증거입니다. 지금 스윙에서 뭘 바꿔야 비거리가 나오는지, 이 영상에서 바로 확인하세요.\n\n#골프 #골프레슨 #드라이버비거리 #헤드스피드 #볼스피드 #골프스윙 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "헤드스피드", "드라이버비거리", "볼스피드", "비거리늘리기", "드라이버레슨", "골프스윙", "golf", "golflesson", "driverswing", "ballspeed", "clubheadspeed", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#헤드스피드", "#드라이버비거리", "#볼스피드", "#골프스윙", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P021-S03": {
        "title": "위로 휘두르면 볼스피드 78.5 바로 나와요 — 드라이버 비거리 드릴",
        "description": "드라이버 비거리 고민된다면 이 드릴 한 번 해보세요. 위로 휘두르는 스윙만 익혀도 볼스피드 78.5, 300야드 비거리가 실측으로 나옵니다. 10개 중 1개라도 맞히면 바로 느낌 옵니다. 따라해보세요!\n\n#골프 #드라이버 #비거리 #골프레슨 #골프스윙 #볼스피드 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "드라이버", "비거리", "볼스피드", "골프레슨", "골프스윙", "드라이버드릴", "300야드", "golf", "driver", "golfswing", "golftips", "ballspeed", "distancegolf", "shorts"],
        "hashtags": ["#골프", "#드라이버", "#비거리", "#볼스피드", "#골프레슨", "#골프스윙", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P021-S04": {
        "title": "비거리 느는 3가지 — 백스윙·가속·지면력만 바꿔보세요",
        "description": "백스윙을 대각선으로 크게, 다운스윙은 엑셀 밟듯 가속, 그리고 지면력까지 — 이 3가지가 비거리의 핵심입니다. 복잡하게 생각할 필요 없이 이 순서대로 드릴해보세요. 스윙크루가 실전 적용법을 스윙 시연으로 정리했습니다.\n\n#골프 #골프레슨 #비거리 #골프스윙 #지면력 #백스윙 #shorts #스윙크루",
        "tags": ["골프", "골프레슨", "비거리", "골프스윙", "백스윙", "지면력", "가속", "골프드릴", "스윙크루", "golf", "golfswing", "golftips", "drivedistance", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#비거리", "#골프스윙", "#지면력", "#백스윙", "#shorts", "#스윙크루"],
    },
    "26-P021-S05": {
        "title": "백스윙 교정 — 오른쪽 어깨를 대각선으로 높게 드세요",
        "description": "백스윙이 짧거나 어깨 회전이 부족하다면 이 한 가지만 바꿔보세요. 오른쪽 어깨를 대각선으로 높게 드는 것만으로 백스윙 궤도가 달라집니다. 미스샷 두려움 없이 100% 이상 힘으로 휘두를 수 있는 백스윙 교정 cue, 지금 바로 확인해보세요!\n\n#골프 #골프레슨 #백스윙 #골프스윙 #골프교정 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "백스윙", "골프스윙", "어깨회전", "골프교정", "스윙교정", "golf", "golfswing", "backswing", "golftips", "golflesson", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#백스윙", "#골프스윙", "#골프교정", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P021-S06": {
        "title": "대강 때려도 드라이버 볼스피드 70 나오는 스윙 레슨",
        "description": "제대로 된 스윙이 몸에 배면 대강 때려도 볼스피드 70이 나옵니다. 힘을 쓰는 게 아니라 구조가 답이에요. 스윙크루 드라이버 레슨으로 직접 확인해보세요.\n\n#골프 #골프레슨 #드라이버 #볼스피드 #골프스윙 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "드라이버", "볼스피드", "골프스윙", "드라이버레슨", "스윙교정", "비거리", "golf", "golflesson", "driverswing", "ballspeed", "golftips", "shorts", "스윙크루"],
        "hashtags": ["#골프", "#골프레슨", "#드라이버", "#볼스피드", "#골프스윙", "#shorts", "#스윙크루", "#골프팁"],
    },
}


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row

    saved = 0
    for iid, meta in META.items():
        r = conn.execute(
            "SELECT id, notion_page_id FROM shorts WHERE internal_id=?",
            (iid,),
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
                print(f"  {iid}: '{meta['title'][:40]}...'")
            except Exception as e:
                print(f"  {iid} notion FAIL: {e}")

    conn.commit()
    conn.close()
    print(f"\nsaved: {saved}/{len(META)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
