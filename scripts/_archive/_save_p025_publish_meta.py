"""1회용: P025 publish-meta-writer 8개 결과 → SQLite + 노션 저장. publish_ready X."""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update

META: dict[str, dict] = {
    "26-P025-S01": {
        "title": "비거리 30야드 차이 만드는 두 가지 꼬임, 좋은 꼬임 vs 나쁜 꼬임",
        "description": "프로처럼 가볍게 툭 치는데 비거리는 더 나오는 이유, 바로 꼬임의 차이입니다. 힘이 꽉 들어간 꼬임과 힘이 빠진 꼬임을 구분하면 비거리가 30야드까지 달라져요. 아마추어 99%가 놓치는 다운스윙의 핵심 원리, 이 영상 하나로 정리했습니다. 끝까지 보고 내 스윙에 적용해보세요!\n\n#골프 #골프레슨 #골프스윙 #비거리 #다운스윙 #꼬임 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "다운스윙", "꼬임", "백스윙", "스윙공식", "아마추어골프", "golf", "golfswing", "golftips", "shorts", "스윙크루"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#다운스윙", "#꼬임", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P025-S02": {
        "title": "아마추어 99%가 모르는 다운스윙 비밀, 꼬임은 푸는 게 아니라 데려오는 겁니다",
        "description": "다운스윙에서 꼬임을 빨리 풀려는 순간 비거리는 사라집니다. 빨래를 짤 때처럼 꼬임을 유지한 채 임팩트까지 데려오는 게 핵심이에요. 프로처럼 가볍게 툭 치는 스윙의 진짜 공식, 이 한 가지만 바꿔도 거리가 달라집니다.\n\n#골프 #골프레슨 #골프스윙 #다운스윙 #비거리 #꼬임유지 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "다운스윙", "비거리", "꼬임유지", "아마추어골프", "스윙공식", "임팩트", "골프팁", "golf", "golfswing", "golftips", "downswing", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#다운스윙", "#비거리", "#꼬임유지", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P025-S03": {
        "title": "헤드를 끌고 오면 캐스팅이 사라집니다, 다운스윙 레깅 만드는 법",
        "description": "아마추어 99%가 놓치는 다운스윙 원리, 헤드를 끌고 오는 감각 하나로 캐스팅이 사라집니다. 꼬임을 끝까지 데리고 와야 헤드의 힘이 공까지 전달돼요. 비거리 늘리고 싶다면 이 한 가지만 바꿔보세요!\n\n#골프 #골프레슨 #골프스윙 #다운스윙 #캐스팅 #레깅 #비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "다운스윙", "캐스팅", "레깅", "비거리", "헤드스피드", "스윙공식", "아마추어골프", "golf", "golfswing", "golftips", "downswing", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#다운스윙", "#캐스팅", "#레깅", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P025-S04": {
        "title": "백스윙 탑에서 왼발부터 오른손까지 쭉 늘려서 꼬세요",
        "description": "백스윙 탑에서 꼬임을 제대로 만드는 핵심은 왼발과 오른손의 대각선 거리입니다. 두 지점이 쭉 늘어나 있다고 이미지를 그려보세요. 이 감각만 잡아도 다운스윙의 파워가 달라집니다. 짧게 따라 해보세요.\n\n#골프 #골프레슨 #골프스윙 #백스윙 #다운스윙 #비거리 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "백스윙", "다운스윙", "비거리", "꼬임", "스윙공식", "골프팁", "스윙크루", "golf", "golfswing", "golftips", "backswing", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#백스윙", "#다운스윙", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P025-S05": {
        "title": "헤드 던지면 속도 절대 안 늘어요, 헤드 뒤에 두는 다운스윙 원리",
        "description": "비거리 늘리려고 헤드를 던졌다가 오히려 속도가 줄어든 경험 있으신가요. 핵심은 헤드를 뒤에 두고 왼쪽 어깨와 왼쪽 힙이 먼저 회전하는 것입니다. 이 한 가지 순서만 바꿔도 헤드 스피드가 살아납니다. 끝까지 보고 다음 라운드에서 바로 적용해보세요.\n\n#골프 #골프레슨 #골프스윙 #다운스윙 #헤드스피드 #비거리 #shorts #스윙크루 #골프팁 #골프꿀팁",
        "tags": ["골프", "골프레슨", "골프스윙", "다운스윙", "헤드스피드", "비거리", "스윙공식", "헤드뒤에두기", "아마추어골프", "스윙크루", "golf", "golfswing", "golftips", "downswing", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#다운스윙", "#헤드스피드", "#비거리", "#shorts", "#스윙크루", "#골프팁", "#골프꿀팁"],
    },
    "26-P025-S06": {
        "title": "헤드를 뒤에 두면 회전이 자동으로 됩니다, 비거리 늘리는 스윙 공식",
        "description": "프로처럼 가볍게 툭 쳐서 비거리 늘리는 핵심을 정리했습니다. 헤드를 뒤에 두는 감각만 잡으면 손으로 억지로 돌릴 필요 없이 회전이 자동으로 따라옵니다. 다운스윙에서 아마추어 99%가 놓치는 원리, 이 한 가지만 기억하세요.\n\n#골프 #골프레슨 #골프스윙 #다운스윙 #비거리 #회전 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "다운스윙", "비거리", "헤드", "회전", "스윙공식", "골프팁", "golf", "golfswing", "golftips", "downswing", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#다운스윙", "#비거리", "#회전", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P025-S07": {
        "title": "선 채로 점프하면 스윙 절대 안 터집니다, 비거리 늘리는 다운스윙 원리",
        "description": "프로처럼 가볍게 툭 쳐도 비거리가 나는 비밀은 점프 비유에 있습니다. 선 채로는 점프가 안 되듯, 몸을 늘렸다 폭발시켜야 스윙이 터져요. 광배와 근막을 활용한 다운스윙 원리를 쉽게 풀어드립니다. 아마추어 99%가 놓치는 포인트, 끝까지 보고 따라해보세요!\n\n#골프 #골프레슨 #골프스윙 #비거리 #다운스윙 #스윙크루 #shorts #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "다운스윙", "스윙원리", "광배근", "골프비거리늘리기", "아마추어골프", "golf", "golfswing", "golftips", "downswing", "shorts", "스윙크루"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#다운스윙", "#스윙크루", "#shorts", "#골프팁"],
    },
    "26-P025-S08": {
        "title": "테이크어웨이 느리면 비거리 막힙니다, 아마추어 99%가 헷갈리는 순서",
        "description": "테이크어웨이는 빠르게, 백스윙 탑에선 힘이 빠져야 합니다. 아마추어 대부분이 이 순서를 거꾸로 해서 비거리를 잃어요. 오늘 영상에서 올바른 스윙 템포 공식 확인해보세요!\n\n#골프 #골프레슨 #골프스윙 #테이크어웨이 #백스윙 #비거리 #스윙템포 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "테이크어웨이", "백스윙", "비거리", "스윙템포", "다운스윙", "아마추어골프", "golf", "golfswing", "golftips", "takeaway", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#테이크어웨이", "#백스윙", "#비거리", "#스윙템포", "#shorts", "#스윙크루", "#골프팁"],
    },
}


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    saved = 0
    for iid, meta in META.items():
        # 안전망: em-dash / en-dash / 콜론 자동 치환
        for key in ("title", "description"):
            meta[key] = meta[key].replace("—", ",").replace("–", ",").replace(": ", " ")
        r = conn.execute(
            "SELECT id, notion_page_id FROM shorts WHERE internal_id=?", (iid,),
        ).fetchone()
        if not r:
            print(f"  {iid} not found — skip")
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
        saved += 1
        print(f"  {iid}: '{meta['title'][:60]}...'")
    conn.commit()
    conn.close()
    print(f"\nDONE: {saved}/{len(META)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
