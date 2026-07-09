"""1회용: P026 publish-meta-writer 결과 → SQLite + 노션."""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update

META = {
    "26-P026-S01": {
        "title": "오른손잡이도 왼손이 더 멀리 던집니다, 골프 스윙의 진짜 비밀",
        "description": "원반 던지기를 해보면 오른손잡이도 왼손이 더 멀리 날아갑니다. 왜냐하면 왼손이 몸의 회전과 시퀀스를 그대로 따라가기 때문이에요. 골프 스윙도 똑같습니다. 오른팔은 힘이 아니라 안정성에 쓰여야 비거리와 방향성이 같이 잡혀요.\n\n#골프 #골프레슨 #골프스윙 #비거리 #방향성 #오른팔사용법 #shorts #스윙크루 #골프팁 #golf",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "방향성", "오른팔", "스윙시퀀스", "원반던지기", "스윙크루", "골프팁", "golf", "golfswing", "golftips", "shorts", "golflesson"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#방향성", "#오른팔사용법", "#shorts", "#스윙크루", "#골프팁", "#golf"],
    },
    "26-P026-S02": {
        "title": "드라이버 타점 안 맞을 땐 오른팔만 확인하세요",
        "description": "토에 맞거나 힐에 맞아서 비거리가 들쭉날쭉하다면 십중팔구 오른팔 문제입니다. 오른팔이 공간을 못 잡고 무너지면 클럽 페이스가 흔들려서 타점이 그대로 망가져요. 이 영상에서 토·힐 미스의 진짜 원인을 진단해 드립니다. 한 번만 따라해보면 바로 차이를 느낄 거예요!\n\n#골프 #골프레슨 #골프스윙 #드라이버 #타점 #비거리 #shorts #스윙크루 #골프팁 #골프오른팔",
        "tags": ["골프", "골프레슨", "골프스윙", "드라이버", "드라이버타점", "오른팔사용법", "비거리", "방향성", "토미스", "힐미스", "golf", "golfswing", "golftips", "driver", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#드라이버", "#타점", "#비거리", "#shorts", "#스윙크루", "#골프팁", "#골프오른팔"],
    },
    "26-P026-S03": {
        "title": "오른팔 제대로 쓰는 손바닥 드릴, 손가락만 놓으면 됩니다",
        "description": "헤드를 무작정 던지는 습관을 끊는 손바닥 드릴입니다. 그립을 잡은 다음 오른손 손가락을 모두 놓고 손바닥만 클럽에 붙이고 스윙해보세요. 오른팔이 힘을 제대로 쓰는 감각이 살아나면서 비거리와 방향성을 동시에 잡을 수 있습니다. 연습장에서 바로 따라해보세요!\n\n#골프 #골프레슨 #골프스윙 #손바닥드릴 #오른팔사용법 #비거리 #방향성 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "손바닥드릴", "오른팔", "비거리", "방향성", "그립", "헤드던지기", "골프드릴", "golf", "golfswing", "golftips", "golfdrill", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#손바닥드릴", "#오른팔사용법", "#비거리", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P026-S04": {
        "title": "헤드 던지면 다트 됩니다, 비거리 잡는 오른팔 사용법",
        "description": "헤드만 던지는 스윙은 다트로 공 맞추는 것과 같아서 매번 결과가 달라집니다. 일관된 임팩트를 만들고 싶다면 오른팔로 헤드의 공간을 유지하면서 스윙해야 해요. 비거리와 방향성을 동시에 잡는 핵심, 이 영상에서 확인해보세요!\n\n#골프 #골프레슨 #골프스윙 #비거리 #방향성 #오른팔사용법 #shorts #스윙크루 #골프팁 #임팩트",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "방향성", "오른팔", "헤드던지기", "임팩트", "스윙크루", "프로레슨", "golf", "golfswing", "golftips", "shorts", "golflesson"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#방향성", "#오른팔사용법", "#shorts", "#스윙크루", "#골프팁"],
    },
    "26-P026-S05": {
        "title": "오른팔 까먹으면 비거리 그냥 날아갑니다, 슬라이스까지 잡는 임팩트 비밀",
        "description": "헤드에 오른팔이 따라오지 않으면 타이밍이 무너지고 힘 전달이 끊겨서 비거리가 손실됩니다. 페이스도 같이 흔들려서 슬라이스가 잘 나죠. 오른팔 하나만 제대로 써도 비거리와 방향성 둘 다 잡힙니다. 끝까지 보고 다음 라운드에 바로 적용해보세요!\n\n#골프 #골프레슨 #골프스윙 #비거리 #슬라이스교정 #오른팔사용법 #shorts #스윙크루 #골프팁",
        "tags": ["골프", "골프레슨", "골프스윙", "비거리", "슬라이스교정", "오른팔사용법", "임팩트", "방향성", "스윙크루", "golf", "golfswing", "golftips", "golfdistance", "shorts"],
        "hashtags": ["#골프", "#골프레슨", "#골프스윙", "#비거리", "#슬라이스교정", "#오른팔사용법", "#shorts", "#스윙크루", "#골프팁"],
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
