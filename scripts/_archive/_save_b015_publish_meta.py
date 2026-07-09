"""1회용: B015 publish-meta-writer 결과 → SQLite + 노션."""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update

META = {
    "26-B015-S01": {
        "title": "모던 스윙이 뭐죠? 미네소타 1위 코치가 알려주는 정답",
        "description": "요즘 가장 핫한 '모던 스윙'이 도대체 뭘까요? 미네소타 1위 골프 코치 저스트 크래프트가 직접 가르치는 모던 스윙의 정의부터 풀어드립니다. 아마추어 골퍼라면 꼭 알아야 할 5부작 시리즈의 첫 번째 영상이에요. 끝까지 보고 다음 편도 놓치지 마세요!\n\n#골프 #골프레슨 #모던스윙 #골프스윙 #골프팁 #shorts #스윙크루 #아마추어골프",
        "tags": ["골프", "골프레슨", "모던스윙", "골프스윙", "아마추어골프", "골프팁", "저스트크래프트", "미네소타골프코치", "스윙크루", "golf", "golfswing", "modernswing", "golftips", "golflesson", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#모던스윙", "#골프스윙", "#골프팁", "#shorts", "#스윙크루", "#아마추어골프"],
    },
    "26-B015-S02": {
        "title": "엉덩이 빼고 숙이면 그게 문제입니다, 모던 스윙 셋업의 정답",
        "description": "흔히 듣는 '엉덩이 빼고 숙여라'가 사실은 잘못된 셋업이라는 사실, 알고 계셨나요? 미네소타 1위 코치 저스트 크래프트가 가르치는 모던 스윙은 골반을 발 위에, 무릎은 신발끈 위에 두는 게 핵심입니다. 셋업 통념을 뒤집는 한 가지, 오늘부터 바로 적용해보세요!\n\n#골프 #골프레슨 #골프스윙 #셋업 #어드레스 #모던스윙 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "셋업", "어드레스", "모던스윙", "골반", "무릎", "스윙크루", "저스트크래프트", "golf", "golfswing", "golftips", "golfsetup", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#셋업", "#어드레스", "#모던스윙", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-B015-S03": {
        "title": "허리 아픈 50대 골퍼, 벤 호건처럼 스윙하세요",
        "description": "아침에 허리 뻐근하고 어깨 돌리기 힘든 50대 골퍼라면 꼭 보세요. 1949년 교통사고 후 단 16개월 만에 US오픈을 우승한 벤 호건이 답입니다. 평범한 골퍼를 위한 모던 스윙 3가지 비결 중 첫째, 바로 압력을 쓰는 법입니다.\n\n#골프 #골프레슨 #벤호건 #50대골프 #모던스윙 #골프스윙 #shorts #스윙크루 #골프팁 #시니어골프",
        "tags": ["골프", "골프레슨", "벤호건", "50대골프", "시니어골프", "모던스윙", "골프스윙", "압력", "스윙크루", "허리통증", "golf", "golfswing", "benhogan", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#벤호건", "#50대골프", "#모던스윙", "#골프스윙", "#shorts", "#스윙크루", "#골프팁", "#시니어골프"],
    },
    "26-B015-S04": {
        "title": "회전 부족한 50대도 비거리 나는 법, 깊이(Depth) 하나면 됩니다",
        "description": "몸이 안 돌아간다고 거리 포기하지 마세요. 미네소타 1위 코치 저스트 크래프트의 모던 스윙 두 번째 비결, 깊이(Depth)입니다. 클럽을 몸 뒤로 깊게 빼면 야구 와인드업처럼 거리가 납니다. 회전 부족을 깊이로 보완하는 한 가지만 기억하세요.\n\n#골프 #골프레슨 #골프스윙 #비거리 #50대골프 #모던스윙 #depth #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "50대골프", "시니어골프", "모던스윙", "깊이", "depth", "벤호건", "저스트크래프트", "스윙크루", "golf", "golfswing", "golftips"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#50대골프", "#모던스윙", "#depth", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-B015-S05": {
        "title": "벤 호건의 마지막 비결, 백스윙 탑에서 왼팔이 가슴을 가로지르게 하세요",
        "description": "모던 스윙 시리즈 마지막 편입니다. 백스윙 탑에서 왼팔이 가슴 위로 가로질러 올라오면 회전이 자동으로 잠기면서 일관된 임팩트가 만들어져요. 50대, 60대, 70대도 부담 없이 칠 수 있는 핵심 동작이니 오늘 연습장에서 바로 체크해보세요!\n\n#골프 #골프레슨 #골프스윙 #백스윙 #벤호건 #모던스윙 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "백스윙", "벤호건", "모던스윙", "왼팔", "가슴회전", "시니어골프", "스윙크루", "golf", "golfswing", "golftips", "backswing", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#백스윙", "#벤호건", "#모던스윙", "#shorts", "#스윙크루", "#골프팁"],
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
