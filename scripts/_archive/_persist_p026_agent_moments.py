"""1회용: P026 (DTdmO5ORDqo) agent path 결과 → SQLite + 노션 + analyses cache."""

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

YOUTUBE_ID = "DTdmO5ORDqo"
INTERNAL_ID_PREFIX = "26-P026"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

# start_sec 오름차순 → S01..S05.
CURATOR_MOMENTS = [
    {"start_sec": 81.4, "end_sec": 148.1, "hook_text": "왼손이 더 멀리",
     "copy1": "원반은 왼손이", "copy2": "더 멀리 던집니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.0,
     "reasoning": "[kind=face_centered_dynamic] 오프닝 의외성 hook (오른손잡이인데 왼손이 더 멀리). 통념 뒤집기+원반 비유로 직관 즉시. setup-tone."},
    {"start_sec": 321.0, "end_sec": 397.1, "hook_text": "타점 안 맞으면",
     "copy1": "토 힐 빗맞으면", "copy2": "오른팔 기능 확인하세요!",
     "scene_kind": "face_centered_dynamic", "score": 8.4,
     "reasoning": "[kind=face_centered_dynamic] 구체적 미스 진단 (토/힐) → 시청자 즉각 공감. 진단 카테고리 다양성."},
    {"start_sec": 371.5, "end_sec": 445.5, "hook_text": "손바닥 드릴",
     "copy1": "오른손가락 놓으면", "copy2": "오른팔 기능 살아납니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.6,
     "reasoning": "[kind=face_centered_dynamic] 구체 손바닥 드릴+실행 즉시 가능. researcher 최고점, viral 가능성 높음."},
    {"start_sec": 397.9, "end_sec": 476.3, "hook_text": "헤드 던지지 마라",
     "copy1": "헤드만 던지면", "copy2": "다트 던지는 겁니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.4,
     "reasoning": "[kind=face_centered_dynamic] 제목 직결 비유 (다트 vs 스윙). 통념 부정+강력한 시각 메타포."},
    {"start_sec": 603.9, "end_sec": 654.8, "hook_text": "비거리 손실 원인",
     "copy1": "오른팔 까먹으면", "copy2": "비거리 그냥 날아갑니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.2,
     "reasoning": "[kind=face_centered_dynamic] 마무리 결과 약속 (비거리+방향). 후반 핵심 정리 구간."},
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
            short_iid = f"{INTERNAL_ID_PREFIX}-S{i:02d}"
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
