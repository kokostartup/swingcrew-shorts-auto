"""1회용: P022 (eLMM-66_t0o) agent path 결과 → SQLite + 노션 + analyses cache."""

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

YOUTUBE_ID = "eLMM-66_t0o"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {"start_sec": 93.75, "end_sec": 158.0, "hook_text": "다운블로우 진실",
     "copy1": "다운블로우는", "copy2": "손으로 찍지 마세요!",
     "scene_kind": "talking_head", "score": 9.3,
     "reasoning": "[kind=talking_head] 영상 메인 주제 직격 + 부정 명령형 강한 훅."},
    {"start_sec": 302.43, "end_sec": 362.6, "hook_text": "공위치 반 개씩",
     "copy1": "클럽마다 반 개씩", "copy2": "공위치 옮기세요!",
     "scene_kind": "talking_head", "score": 7.9,
     "reasoning": "[kind=talking_head] 구체 수치(반 개씩) + 행동 지시. 셋업 주제."},
    {"start_sec": 339.48, "end_sec": 408.0, "hook_text": "5번 아이언",
     "copy1": "5번 아이언은", "copy2": "드라이버처럼 치세요!",
     "scene_kind": "talking_head", "score": 8.6,
     "reasoning": "[kind=talking_head] 역발상 카피 + 구체 클럽 번호. 통념 깨기."},
    {"start_sec": 445.9, "end_sec": 519.4, "hook_text": "왼어깨 올라가면",
     "copy1": "왼쪽 어깨 올라가면", "copy2": "오른쪽이 떨어집니다!",
     "scene_kind": "swing_demo", "score": 8.2,
     "reasoning": "[kind=swing_demo] 구체 신체부위 2개 + 인과 구조 명확."},
    {"start_sec": 519.46, "end_sec": 593.8, "hook_text": "다리 점프 금지",
     "copy1": "다리로 점프하면", "copy2": "100% 뒷땅 납니다!",
     "scene_kind": "swing_demo", "score": 9.2,
     "reasoning": "[kind=swing_demo] 구체 수치(100%) + 결과 약속(뒷땅). 자기 진단 욕구 강함."},
    {"start_sec": 671.87, "end_sec": 745.0, "hook_text": "오른발이 답",
     "copy1": "오른발만 차면", "copy2": "왼쪽이 저절로 들려요!",
     "scene_kind": "swing_demo", "score": 8.8,
     "reasoning": "[kind=swing_demo] 구체 신체부위(오른발) + 인과 약속. 제목 '오른발만 이해하면' 직접 콜백."},
    {"start_sec": 897.17, "end_sec": 957.1, "hook_text": "10년 찍어치기",
     "copy1": "찍어치기 10년이면", "copy2": "평생 뒷땅 못 고쳐요!",
     "scene_kind": "talking_head", "score": 9.1,
     "reasoning": "[kind=talking_head] 시간 단위(10년) + 공포 소구. 통념 부숨 + 마무리."},
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
            short_iid = f"26-P022-S{i:02d}"
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
