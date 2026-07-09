"""1회용: B012 (r-_nfjQ5Jv0) agent path 결과를 SQLite + 노션 + analyses cache.

S06 (344.689 로리 왼발) NMS 충돌(S03와 18초 차이) + 같은 체중이동 theme → 제거. final 6개.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import create_page as notion_create_page
from app.storage.db import get_connection, get_video_by_youtube_id
from app.storage.models import AnalysisResult, MagicMoment

YOUTUBE_ID = "r-_nfjQ5Jv0"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {
        "start_sec": 38.995, "end_sec": 109.145,
        "hook_text": "헤드 스피드 같은데",
        "copy1": "헤드 스피드 같은데", "copy2": "왜 20m 차이날까?",
        "scene_kind": "talking_head", "score": 8.9,
        "reasoning": "[kind=talking_head] 오프닝 hook 강력. 의문 setup → payoff로 다음 줄 자연 연결.",
    },
    {
        "start_sec": 92.338, "end_sec": 151.889,
        "hook_text": "어택앵글 2도가",
        "copy1": "어택앵글 2도면", "copy2": "비거리 완전 바뀝니다!",
        "scene_kind": "talking_head", "score": 8.5,
        "reasoning": "[kind=talking_head] 구체 숫자(2도) + 변화 약속. 비교 데이터로 신뢰감.",
    },
    {
        "start_sec": 213.745, "end_sec": 274.857,
        "hook_text": "세 가지만",
        "copy1": "세 가지 세팅만", "copy2": "비거리 20m 늘어요!",
        "scene_kind": "talking_head", "score": 9.4,
        "reasoning": "[kind=talking_head] 구체 숫자(3가지) + 결과 약속(20m) + setup-payoff 완벽. 영상 핵심 thesis.",
    },
    {
        "start_sec": 255.661, "end_sec": 326.798,
        "hook_text": "티 1cm만 높이면",
        "copy1": "티 1cm만 높이면", "copy2": "스핀 확 줄어듭니다!",
        "scene_kind": "talking_head", "score": 8.2,
        "reasoning": "[kind=talking_head] 구체 액션(1cm) + 즉시 결과. 따라하기 쉬운 1번 세팅 — bookmarkable.",
    },
    {
        "start_sec": 326.798, "end_sec": 387.481,
        "hook_text": "손목만 뒤집으면",
        "copy1": "손목만 뒤집으면", "copy2": "비거리 다 날아갑니다!",
        "scene_kind": "talking_head", "score": 8.7,
        "reasoning": "[kind=talking_head] 구체 신체부위(손목) + 경고형 결과 약속. 플리핑 경고는 아마추어 페인 포인트.",
    },
    {
        "start_sec": 408.374, "end_sec": 451.664,
        "hook_text": "브라이슨 -600rpm",
        "copy1": "브라이슨도 너클볼로", "copy2": "스핀 600 줄였습니다!",
        "scene_kind": "talking_head", "score": 7.9,
        "reasoning": "[kind=talking_head] PGA 스타 사례 + 구체 수치(600rpm). 영상 마무리 authority proof.",
    },
]


def _load_opening_lines() -> dict[float, str]:
    tx_path = Path("data/transcripts") / f"{YOUTUBE_ID}.json"
    with tx_path.open(encoding="utf-8") as f:
        tx = json.load(f)
    all_words: list[dict] = []
    for seg in tx.get("segments", []):
        for w in seg.get("words", []):
            all_words.append(w)
    all_words.sort(key=lambda w: float(w.get("start", 0)))
    mapping: dict[float, str] = {}
    for m in CURATOR_MOMENTS:
        start = m["start_sec"]
        matched = [w for w in all_words if float(w.get("start", 0)) >= start]
        if matched:
            text = " ".join((w.get("text") or "").strip() for w in matched[:20]).strip()
            mapping[start] = text[:200]
    return mapping


def main() -> int:
    print("[1/2] analyses cache 작성")
    opening = _load_opening_lines()
    moments: list[MagicMoment] = []
    for m in CURATOR_MOMENTS:
        moments.append(MagicMoment(
            start_sec=m["start_sec"], end_sec=m["end_sec"],
            hook_text=m["hook_text"], copy1=m["copy1"], copy2=m["copy2"],
            score=m["score"], reasoning=m["reasoning"],
            opening_line=opening.get(m["start_sec"]),
        ))
    result = AnalysisResult(youtube_id=YOUTUBE_ID, model="claude-opus-agent", moments=moments)
    ANALYSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSES_PATH.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"  saved: {ANALYSES_PATH} ({len(moments)} moments)\n")

    print("[2/2] SQLite + 노션 push")
    conn = get_connection()
    try:
        video = get_video_by_youtube_id(conn, YOUTUBE_ID)
        if not video:
            print(f"video not found: {YOUTUBE_ID}")
            return 1
        existing = conn.execute(
            "SELECT internal_id FROM shorts WHERE source_video_id = ?",
            (video.id,),
        ).fetchall()
        if existing:
            print(f"이미 모먼트 {len(existing)}개 존재 — 중단")
            return 0

        created = 0
        for i, m in enumerate(CURATOR_MOMENTS, 1):
            short_iid = f"26-B012-S{i:02d}"
            opening_line = opening.get(m["start_sec"])
            conn.execute(
                "INSERT INTO shorts "
                "(source_video_id, start_time, end_time, score, scene_type, "
                "face_center_x, face_segments, opening_line, status, internal_id, "
                "copy1, copy2, channel) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)",
                (video.id, m["start_sec"], m["end_sec"], m["score"],
                 None, None, None, opening_line, short_iid,
                 m["copy1"], m["copy2"], video.channel),
            )
            short_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            moment_obj = MagicMoment(
                start_sec=m["start_sec"], end_sec=m["end_sec"],
                hook_text=m["hook_text"], copy1=m["copy1"], copy2=m["copy2"],
                score=m["score"], reasoning=m["reasoning"],
            )
            try:
                page_id = notion_create_page(
                    video, moment_obj, short_iid,
                    channel=video.channel, initial_status="proposed",
                )
                conn.execute(
                    "UPDATE shorts SET notion_page_id = ?, pushed_at = ? WHERE id = ?",
                    (page_id, datetime.now(UTC).isoformat(), short_id),
                )
                created += 1
                print(f"  {short_iid} pushed (page_id={page_id[:8]}..)")
            except Exception as e:
                print(f"  {short_iid} notion FAIL: {e}")
        conn.commit()
        print(f"\nDONE: {created}/{len(CURATOR_MOMENTS)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
