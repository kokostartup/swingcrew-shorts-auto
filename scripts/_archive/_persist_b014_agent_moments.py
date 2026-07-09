"""1회용: B014 (_OnMvcHegro) agent path 결과 → SQLite + 노션 + analyses cache."""

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

YOUTUBE_ID = "_OnMvcHegro"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {"start_sec": 22.711, "end_sec": 90.444, "hook_text": "힙턴 통념 거짓말",
     "copy1": "힙 빨리 돌리라는 말", "copy2": "그거 다 거짓말입니다!",
     "scene_kind": "face_centered_dynamic", "score": 8.9,
     "reasoning": "[kind=face_centered_dynamic] 통념 부수기 setup-tone. 도입부 hook + 90도 숄더턴 정의 자연 연결."},
    {"start_sec": 169.169, "end_sec": 225.466, "hook_text": "프로 53도 vs 아마 23도",
     "copy1": "프로 어깨는 53도", "copy2": "아마는 90%가 못 돕니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.1,
     "reasoning": "[kind=face_centered_dynamic] 53도/23-43도/90% 숫자 폭격 + 프로 vs 아마 비교 프레임."},
    {"start_sec": 319.12, "end_sec": 368.378, "hook_text": "0.04초의 비밀",
     "copy1": "임팩트 0.04초가", "copy2": "비거리 다 결정합니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.4,
     "reasoning": "[kind=face_centered_dynamic] 0.04초 + 의식 컨트롤 불가 의외성. hook 밀도 최상."},
    {"start_sec": 385.493, "end_sec": 458.052, "hook_text": "채찍의 비밀",
     "copy1": "채찍 끝이 빨라지려면", "copy2": "골반을 멈춰야 합니다!",
     "scene_kind": "face_centered_dynamic", "score": 9.0,
     "reasoning": "[kind=face_centered_dynamic] 채찍 비유 직관 + '손잡이 멈춰야 끝이 빨라진다' 의외성."},
    {"start_sec": 537.297, "end_sec": 602.543, "hook_text": "스텝 드릴 박자",
     "copy1": "양발 좁게 서서", "copy2": "하나둘셋 박자만 타세요!",
     "scene_kind": "face_centered_dynamic", "score": 8.0,
     "reasoning": "[kind=face_centered_dynamic] 드릴 카테고리. 양발 붙이기 + 하나둘셋 박자 즉시 따라할 수 있음."},
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
            short_iid = f"26-B014-S{i:02d}"
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
