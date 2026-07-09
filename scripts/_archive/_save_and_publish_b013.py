"""1회용: B013 publish-meta-writer 결과 저장 + schedule + publish_ready 한 번에."""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update
from app.pipeline.publish import publish_ready
from app.pipeline.schedule import assign_scheduled_at_for_pending

META = {
    "26-B013-S01": {
        "title": "어깨 정렬만 바꿔도 슬라이스 사라진다",
        "description": "슬라이스의 진짜 원인은 스윙이 아니라 어깨 정렬입니다. 어깨만 제대로 맞춰도 공이 똑바로 가고 비거리는 자연스럽게 따라와요. 세게 휘두르지 않아도 250m, 어깨 하나만 바꿔보세요.\n\n#골프 #골프레슨 #골프스윙 #슬라이스 #슬라이스교정 #드라이버 #비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "슬라이스", "슬라이스교정", "어깨정렬", "드라이버", "비거리", "골프스윙", "골프드라이버", "골프자세", "golf", "golfswing", "golftips", "slice", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#슬라이스", "#슬라이스교정", "#드라이버", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-B013-S02": {
        "title": "팔 길게 뻗으면 슬라이스 나는 이유, 어깨 1가지로 해결",
        "description": "비거리 늘리려고 팔을 쭉 뻗으면 어깨가 자동으로 열려서 아웃인 스윙, 결국 슬라이스로 이어집니다. 어깨 동작 한 가지만 바꾸면 슬라이스와 비거리 부족이 동시에 풀려요. 짧게 정리했으니 끝까지 보고 따라해보세요.\n\n#골프 #골프레슨 #골프스윙 #슬라이스 #비거리 #아웃인 #shorts #스윙크루 #골프팁 #어깨회전",
        "tags": ["골프", "골프레슨", "골프스윙", "슬라이스", "슬라이스교정", "비거리", "아웃인스윙", "어깨회전", "스윙크루", "golf", "golfswing", "golftips", "slicefix", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#슬라이스", "#비거리", "#아웃인", "#shorts", "#스윙크루", "#골프팁", "#어깨회전"],
    },
    "26-B013-S03": {
        "title": "PGA vs LPGA 어택앵글 비교, 아마추어는 LPGA 따라야 비거리 30m 늘어요",
        "description": "PGA는 -1.3도로 내려치고 LPGA는 +3도로 위로 칩니다. 헤드스피드 38~44mps 아마추어라면 LPGA 스타일이 정답이에요. 어택앵글 하나만 바꿔도 비거리 30m 차이가 납니다.\n\n#골프 #골프레슨 #골프스윙 #비거리 #어택앵글 #드라이버 #shorts #스윙크루 #골프팁 #LPGA",
        "tags": ["골프", "골프레슨", "골프스윙", "어택앵글", "비거리", "드라이버", "LPGA", "PGA", "헤드스피드", "스윙크루", "golf", "golfswing", "golftips", "attackangle", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#어택앵글", "#드라이버", "#shorts", "#스윙크루", "#골프팁", "#LPGA"],
    },
    "26-B013-S04": {
        "title": "티 높이 하나만 바꿔도 비거리 늘어나는 이유",
        "description": "티를 높게 꽂고 어택앵글을 양수로 만들면 스핀이 약 300rpm 줄고 캐리가 늘어납니다. 셋업에서 단 하나만 바꿔도 비거리가 달라져요. 다음 라운드에서 바로 적용해보세요.\n\n#골프 #골프레슨 #비거리 #드라이버 #티높이 #골프팁 #shorts #스윙크루",
        "tags": ["골프", "골프레슨", "비거리", "드라이버", "티높이", "어택앵글", "스핀", "골프스윙", "셋업", "골프팁", "golf", "golfswing", "driverdistance", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#비거리", "#드라이버", "#티높이", "#골프팁", "#shorts", "#스윙크루"],
    },
    "26-B013-S05": {
        "title": "비거리 20m 늘리는 0원 3가지, 새 드라이버 안 사도 됩니다",
        "description": "새 드라이버 사기 전에 이것부터. 돈 한 푼 안 들이고 비거리 20m 늘리는 3가지 방법을 정리했어요. 9도와 10.5도 로프트 차이 데이터 (179m vs 198m)까지 같이 보세요.\n\n#골프 #골프레슨 #비거리 #드라이버 #골프팁 #shorts #스윙크루 #골프스윙 #비거리늘리기 #골프꿀팁",
        "tags": ["골프", "골프레슨", "비거리", "비거리늘리기", "드라이버", "드라이버로프트", "골프스윙", "골프팁", "골프꿀팁", "스윙크루", "golf", "golfswing", "golftips", "driverdistance", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#비거리", "#드라이버", "#골프팁", "#shorts", "#스윙크루", "#골프스윙", "#비거리늘리기", "#골프꿀팁"],
    },
    "26-B013-S06": {
        "title": "드라이버 로프트 1.5도 차이가 비거리 20m를 바꿉니다",
        "description": "헤드 스피드 95마일 기준, 드라이버 로프트 9도와 10.5도의 비거리 차이는 약 19m. 게다가 티, 호젤, 그립을 1만원대로 세팅만 바꿔도 추가로 15~20m를 더 보낼 수 있습니다. 장비 튜닝 한 번으로 최대 40m 가까이 벌어지는 셋업 포인트를 정리했어요.\n\n#골프 #골프레슨 #드라이버 #드라이버로프트 #비거리 #골프장비 #shorts #스윙크루 #골프팁 #골프튜닝",
        "tags": ["골프", "골프레슨", "드라이버", "드라이버로프트", "비거리", "비거리늘리기", "골프장비", "골프튜닝", "골프팁", "스윙크루", "golf", "golfdriver", "golftips", "shorts", "golfswing"],
        "hashtags": ["#골프", "#골프레슨", "#드라이버", "#드라이버로프트", "#비거리", "#골프장비", "#shorts", "#스윙크루", "#골프팁", "#골프튜닝"],
    },
}


def main() -> int:
    # [1/3] publish_meta SQLite + 노션 저장
    print("[1/3] publish_meta 저장")
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    for iid, meta in META.items():
        # 안전망: em-dash / en-dash / 콜론 자동 치환
        for key in ("title", "description"):
            meta[key] = meta[key].replace("—", ",").replace("–", ",").replace(": ", " ")
        r = conn.execute(
            "SELECT id, notion_page_id FROM shorts WHERE internal_id=?", (iid,),
        ).fetchone()
        if not r:
            continue
        conn.execute(
            "UPDATE shorts SET publish_meta_json=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), r["id"]),
        )
        if r["notion_page_id"]:
            notion_update(
                r["notion_page_id"], "generated",
                title=meta["title"], description=meta["description"],
            )
        print(f"  {iid}: '{meta['title'][:50]}...'")
    conn.commit()
    conn.close()
    print()

    # [2/3] schedule
    print("[2/3] schedule.assign (lead=1h)")
    n_sched = assign_scheduled_at_for_pending(channel="ko", min_lead_hours=1)
    print(f"  assigned: {n_sched}\n")

    # [3/3] publish_ready (R2 + YouTube)
    print("[3/3] publish_ready(skip_gemini_fallback=True)")
    n_pub = publish_ready(skip_gemini_fallback=True)
    print(f"  published: {n_pub}\n")

    # 최종 상태
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT internal_id, status, scheduled_at, published_urls "
        "FROM shorts WHERE internal_id LIKE '26-B013-%' ORDER BY internal_id"
    ).fetchall()
    for r in rows:
        sched = (r["scheduled_at"] or "")[:19]
        urls = (r["published_urls"] or "")[:60]
        print(f"  {r['internal_id']} [{r['status']:10}] sched={sched} urls={urls}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
