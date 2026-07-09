"""1회용: P025 (SJZqtfp4d_4) agent path 결과 → SQLite + 노션 + analyses cache."""

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

YOUTUBE_ID = "SJZqtfp4d_4"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

# curator 결과 (start_sec 오름차순 정렬 → S01..S08).
CURATOR_MOMENTS = [
    {"start_sec": 95.716, "end_sec": 164.967, "hook_text": "두 가지 꼬임",
     "copy1": "꼬임이 두 가지면", "copy2": "비거리 30야드 차이!",
     "scene_kind": "face_centered_dynamic", "score": 9.0,
     "reasoning": "[kind=face_centered_dynamic] 분류형 hook + PD 실험 참여로 흥미. 영상 도입부 핵심 개념."},
    {"start_sec": 197.911, "end_sec": 266.307, "hook_text": "아마추어 99%",
     "copy1": "꼬임을 푼다고 하면", "copy2": "비거리 그대로 멈춥니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.4,
     "reasoning": "[kind=face_centered_dynamic] 영상 제목 '아마추어 99% 실수' 직결 + 통념부수기. 빨래 비유 시각화."},
    {"start_sec": 284.645, "end_sec": 349.776, "hook_text": "헤드를 데리고 와",
     "copy1": "헤드를 데리고 오면", "copy2": "캐스팅 바로 사라집니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.9,
     "reasoning": "[kind=face_centered_dynamic] '이게 핵심' 강조 + 캐스팅 진단 → 레깅 해법."},
    {"start_sec": 418.632, "end_sec": 496.724, "hook_text": "왼발-오른손 거리",
     "copy1": "왼발부터 오른손까지", "copy2": "쭉 늘려서 꼬세요!",
     "scene_kind": "face_centered_dynamic", "score": 8.8,
     "reasoning": "[kind=face_centered_dynamic] 구체 신체부위 2개 + 대각선 이미지."},
    {"start_sec": 468.734, "end_sec": 540.509, "hook_text": "헤드 뒤에 두기",
     "copy1": "헤드를 빨리 펼치면", "copy2": "속도 절대 안 늘어요!",
     "scene_kind": "face_centered_dynamic", "score": 8.4,
     "reasoning": "[kind=face_centered_dynamic] 금지형 setup + 헤드 속도 약속. 통념부수기."},
    {"start_sec": 717.083, "end_sec": 786.591, "hook_text": "오늘의 핵심",
     "copy1": "헤드가 뒤에 있으면", "copy2": "회전이 자동으로 됩니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.7,
     "reasoning": "[kind=face_centered_dynamic] 요약형 + 첫번째 숫자 + 구체 신체부위."},
    {"start_sec": 794.54, "end_sec": 871.507, "hook_text": "점프 비유",
     "copy1": "선 채로 점프하면", "copy2": "스윙 절대 안 터집니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.5,
     "reasoning": "[kind=face_centered_dynamic] 점프 비유로 광배/근막 원리 쉽게. 다른 모먼트와 결 다름."},
    {"start_sec": 920.641, "end_sec": 999.892, "hook_text": "테이크어웨이 빨리",
     "copy1": "테이크어웨이가 느리면", "copy2": "비거리 그대로 막힙니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.3,
     "reasoning": "[kind=face_centered_dynamic] 아마추어 99% reframe + 비거리 직결. 영상 마무리."},
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
            short_iid = f"26-P025-S{i:02d}"
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
