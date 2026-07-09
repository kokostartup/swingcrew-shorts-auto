"""1회용: P024 (5zyZWIMEssI) agent path 결과 → SQLite + 노션 + analyses cache."""

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

YOUTUBE_ID = "5zyZWIMEssI"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {"start_sec": 64.16, "end_sec": 128.31, "hook_text": "팔이 정답",
     "copy1": "팔이 정답 만들면", "copy2": "등각도 자동입니다!",
     "scene_kind": "talking_head", "score": 8.9,
     "reasoning": "[kind=talking_head] 원인-결과 명확 + 자동 결과 약속."},
    {"start_sec": 111.34, "end_sec": 177.12, "hook_text": "헤드 떨궈놓고",
     "copy1": "야구 배트처럼", "copy2": "헤드 떨궈놓고 엎으세요!",
     "scene_kind": "swing_demo", "score": 8.3,
     "reasoning": "[kind=swing_demo] 구체 비유(야구) + 명령형 행동 지시."},
    {"start_sec": 349.39, "end_sec": 407.73, "hook_text": "손 쓰지 마라",
     "copy1": "손 쓰지 말라는", "copy2": "진짜 이유 알려드려요!",
     "scene_kind": "talking_head", "score": 9.4,
     "reasoning": "[kind=talking_head] 영상 제목 직결 핵심 메시지."},
    {"start_sec": 442.31, "end_sec": 485.03, "hook_text": "머리 한 대 맞은",
     "copy1": "이거 들으시면", "copy2": "머리 한 대 맞습니다!",
     "scene_kind": "talking_head", "score": 8.1,
     "reasoning": "[kind=talking_head] 강한 감정 표현 hook + 호기심 유발."},
    {"start_sec": 486.45, "end_sec": 541.42, "hook_text": "슬라이스 공포",
     "copy1": "슬라이스 무서우면", "copy2": "슬라이스만 칩니다!",
     "scene_kind": "talking_head", "score": 8.6,
     "reasoning": "[kind=talking_head] 역설적 통찰(공포→자기충족)."},
    {"start_sec": 573.65, "end_sec": 647.56, "hook_text": "3일이면 고친다",
     "copy1": "이 드릴만 하면", "copy2": "3일이면 고쳐집니다!",
     "scene_kind": "swing_demo", "score": 9.1,
     "reasoning": "[kind=swing_demo] 구체 기간(3일) + 결과 약속."},
    {"start_sec": 654.58, "end_sec": 712.95, "hook_text": "20m 늘어요",
     "copy1": "이렇게만 치면", "copy2": "오비 없이 20m 늘어요!",
     "scene_kind": "talking_head", "score": 8.7,
     "reasoning": "[kind=talking_head] 구체 숫자(20m) + 결과 2가지."},
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
            short_iid = f"26-P024-S{i:02d}"
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
