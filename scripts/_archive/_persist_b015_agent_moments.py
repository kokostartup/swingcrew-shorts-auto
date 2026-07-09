"""1회용: B015 (ELQZMObN3fc) agent path 결과 → SQLite + 노션 + analyses cache."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import create_page as notion_create_page
from app.storage.db import get_connection, get_video_by_youtube_id
from app.storage.models import AnalysisResult, MagicMoment

YOUTUBE_ID = "ELQZMObN3fc"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

# start_sec 오름차순 → S01..S05.
CURATOR_MOMENTS = [
    {"start_sec": 36.397, "end_sec": 114.207, "hook_text": "모던 스윙이 뭐죠?",
     "copy1": "모던 스윙은", "copy2": "미국 1위가 가르칩니다!",
     "scene_kind": "face_centered_dynamic", "score": 7.4,
     "reasoning": "[kind=face_centered_dynamic] 도입부 정의+미네소타 1위 코치 권위. 시리즈 토대."},
    {"start_sec": 165.743, "end_sec": 234.362, "hook_text": "엉덩이 빼지 마라",
     "copy1": "엉덩이 빼고 숙이면", "copy2": "그게 바로 문제입니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.8,
     "reasoning": "[kind=face_centered_dynamic] 평소 들었던 레슨 통념 정면 부정+'신세계' 약속. setup-tone."},
    {"start_sec": 280.477, "end_sec": 347.583, "hook_text": "허리 아프시죠?",
     "copy1": "50대 골퍼는", "copy2": "벤 호건을 따라하세요!",
     "scene_kind": "face_centered_dynamic", "score": 9.2,
     "reasoning": "[kind=face_centered_dynamic] 공감 hook+1949 교통사고 16개월 후 US오픈+3가지 비결+첫째 압력. B narration 정점."},
    {"start_sec": 347.583, "end_sec": 411.197, "hook_text": "회전 말고 깊이",
     "copy1": "회전 덜 해도", "copy2": "거리는 깊이로 납니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.6,
     "reasoning": "[kind=face_centered_dynamic] 50대 회전 부족 해결+야구 와인드업 비유+거리 약속."},
    {"start_sec": 388.535, "end_sec": 458.491, "hook_text": "왼팔이 가슴 가로질러",
     "copy1": "왼팔이 가슴을", "copy2": "가로지르게 하세요!",
     "scene_kind": "face_centered_dynamic", "score": 8.3,
     "reasoning": "[kind=face_centered_dynamic] 신체부위(왼팔/가슴)+의문형+자동 락+50/60/70대 칠 수 있음."},
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
            short_iid = f"26-B015-S{i:02d}"
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
