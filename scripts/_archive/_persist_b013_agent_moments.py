"""1회용: B013 (k33YsH1Yr30) agent path 결과 → SQLite + 노션 + analyses cache."""

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

YOUTUBE_ID = "k33YsH1Yr30"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {"start_sec": 44.78, "end_sec": 119.0, "hook_text": "어깨 정렬만",
     "copy1": "어깨만 맞추면", "copy2": "슬라이스 사라집니다!",
     "scene_kind": "talking_head", "score": 9.2,
     "reasoning": "[kind=talking_head] 슬라이스 진짜 원인이 어깨 정렬. 구체 신체부위 + 결과 약속."},
    {"start_sec": 113.50, "end_sec": 186.3, "hook_text": "리치 함정",
     "copy1": "팔을 길게 뻗으면", "copy2": "아웃인 스윙 됩니다!",
     "scene_kind": "talking_head", "score": 8.6,
     "reasoning": "[kind=talking_head] 흔한 오해(리치=좋다) 정면 반박."},
    {"start_sec": 188.83, "end_sec": 252.1, "hook_text": "PGA vs LPGA",
     "copy1": "어택앵글만 바꾸면", "copy2": "비거리 30m 늘어요!",
     "scene_kind": "comparison", "score": 8.5,
     "reasoning": "[kind=comparison] PGA/LPGA 비교 데이터 → 어택앵글 중요성."},
    {"start_sec": 261.09, "end_sec": 317.2, "hook_text": "티 높이만",
     "copy1": "티만 높게 꽂으면", "copy2": "스핀 줄고 멀리갑니다!",
     "scene_kind": "talking_head", "score": 8.4,
     "reasoning": "[kind=talking_head] 티 높이 → 스핀 감소 인과. 가장 쉬운 즉시 적용 팁."},
    {"start_sec": 398.50, "end_sec": 449.9, "hook_text": "돈 0원으로",
     "copy1": "0원 세 가지만", "copy2": "비거리 20m 늘어요!",
     "scene_kind": "talking_head", "score": 9.1,
     "reasoning": "[kind=talking_head] 0원 + 세 가지 + 20m 숫자 트리플. ROI 강력 후킹."},
    {"start_sec": 441.72, "end_sec": 516.5, "hook_text": "9도 vs 10.5도",
     "copy1": "로프트 1.5도만 바꾸면", "copy2": "20m 더 날아갑니다!",
     "scene_kind": "talking_head", "score": 9.0,
     "reasoning": "[kind=talking_head] 179m vs 198m 구체 측정 데이터. 즉시 행동 가능."},
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
            short_iid = f"26-B013-S{i:02d}"
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
