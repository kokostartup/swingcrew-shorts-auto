"""1회용: P024 publish-meta-writer 결과 저장 + schedule + publish_ready 통합."""

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
    "26-P024-S01": {
        "title": "팔만 정답 만들면 등각도 자동! 슬라이스 훅 뒷땅 한 번에 잡는 법",
        "description": "보통은 몸부터 움직이려 하지만 사실은 반대예요. 팔이 정답 위치를 만들면 등 각도는 알아서 따라옵니다. 배치기, 슬라이스, 훅, 뒷땅까지 한 번에 잡히는 핵심 원리예요.\n\n#골프 #골프레슨 #골프스윙 #슬라이스교정 #훅교정 #뒷땅 #등각도 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "슬라이스", "훅", "뒷땅", "배치기", "등각도", "팔동작", "스윙자세", "golf", "golfswing", "golftips", "golflesson", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#슬라이스교정", "#훅교정", "#뒷땅", "#등각도", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P024-S02": {
        "title": "손목 쓰지 마라? 야구 배트처럼 헤드 떨궈놓고 엎으세요",
        "description": "손목을 쓰지 말라는 진짜 이유, 사실은 야구 배트 스윙에 답이 있습니다. 헤드를 먼저 떨궈놓고 엎는 감각으로 휘둘러보세요. 골퍼 직관과 정반대지만 한 번 느끼면 임팩트가 달라집니다.\n\n#골프 #골프레슨 #골프스윙 #손목스윙 #임팩트 #비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "손목사용", "임팩트", "헤드스피드", "비거리", "스윙감각", "다운스윙", "golf", "golfswing", "golftips", "wrist", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#손목스윙", "#임팩트", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P024-S03": {
        "title": "프로가 손목 쓰지 말라는 진짜 이유, 손과 몸 자리만 바꾸세요",
        "description": "프로 코치가 손목 쓰지 말라고 하는 진짜 이유를 한 영상에 정리했습니다. 손과 몸의 자리를 바꿔주는 시범 한 번이면 스윙 감각이 달라져요. 따라 해보면서 임팩트의 안정감을 직접 느껴보세요.\n\n#골프 #골프레슨 #골프스윙 #손목 #골프손목 #임팩트 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "손목", "골프손목", "임팩트", "스윙감각", "손과몸", "골프자세", "golf", "golfswing", "golftips", "wrist", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#손목", "#골프손목", "#임팩트", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P024-S04": {
        "title": "이거 들으면 머리 한 대 맞는 기분, 손목 사용 한 방에 정리",
        "description": "손목 사용이 헷갈렸다면 이 영상 하나로 정리됩니다. 잘못된 손목 동작을 한 번에 교정하는 핵심 포인트를 짚어드려요. 끝까지 보고 다음 라운드에서 바로 적용해보세요.\n\n#골프 #골프레슨 #골프스윙 #손목사용 #손목코킹 #스윙교정 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "손목사용", "손목코킹", "스윙교정", "임팩트", "비거리", "골프팁", "golf", "golfswing", "golftips", "wristaction", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#손목사용", "#손목코킹", "#스윙교정", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P024-S05": {
        "title": "슬라이스가 무서워서 막으려고 하면 더 심해지는 이유",
        "description": "슬라이스를 막으려고 애쓸수록 더 심해지는 자기충족 굴레. 두려움을 인정하고 오히려 슬라이스를 의도해서 쳐보면 몸이 풀리고 스윙이 돌아옵니다. 한 번만 시도해보세요.\n\n#골프 #골프레슨 #슬라이스교정 #슬라이스 #골프스윙 #드라이버 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "슬라이스", "슬라이스교정", "골프스윙", "드라이버", "드라이버슬라이스", "골프팁", "스윙크루", "golf", "golfswing", "slice", "golftips", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#슬라이스교정", "#슬라이스", "#골프스윙", "#드라이버", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P024-S06": {
        "title": "손목 풀림 3일 만에 고치는 드릴 한 가지",
        "description": "스윙 중 손목이 풀려서 일관성이 무너지는 분들을 위한 3일 교정 드릴입니다. 손목을 잠근 상태로 반복하면 임팩트 모양이 잡혀요. 오늘부터 따라해 보세요.\n\n#골프 #골프레슨 #골프스윙 #손목드릴 #손목풀림 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "손목드릴", "손목풀림", "임팩트", "스윙교정", "골프드릴", "비거리", "golf", "golfswing", "golftips", "wristdrill", "shorts", "스윙크루"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#손목드릴", "#손목풀림", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P024-S07": {
        "title": "오비 없이 비거리 20m 늘리는 손목 교정 한 가지",
        "description": "손목 교정 한 가지만 바꿔도 오비가 줄고 비거리가 20m까지 늘어납니다. 5일에서 일주일이면 변화를 체감할 수 있어요. 영상 보고 그대로 따라해보세요!\n\n#골프 #골프레슨 #골프스윙 #비거리 #오비방지 #손목교정 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "비거리늘리기", "오비방지", "손목교정", "드라이버", "골프팁", "스윙크루", "golf", "golfswing", "golftips", "shorts", "driver"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#오비방지", "#손목교정", "#shorts", "#스윙크루", "#골프팁"],
    },
}


def main() -> int:
    # [1/3] meta 저장 (em-dash/colon 자동 sanitize)
    print("[1/3] publish_meta 저장")
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    for iid, meta in META.items():
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

    # [3/3] publish_ready
    print("[3/3] publish_ready(skip_gemini_fallback=True)")
    n_pub = publish_ready(skip_gemini_fallback=True)
    print(f"  published: {n_pub}\n")

    # 최종 상태
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT internal_id, status, scheduled_at, published_urls "
        "FROM shorts WHERE internal_id LIKE '26-P024-%' ORDER BY internal_id"
    ).fetchall()
    for r in rows:
        sched = (r["scheduled_at"] or "")[:19]
        urls = (r["published_urls"] or "")[:60]
        print(f"  {r['internal_id']} [{r['status']:10}] sched={sched} urls={urls}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
