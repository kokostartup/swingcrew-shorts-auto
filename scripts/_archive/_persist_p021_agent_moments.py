"""1회용: P021 (mAFLAosow9M) agent path 결과를 SQLite + 노션에 저장.

agent 출력 (researcher + curator) → MagicMoment 변환 → SQLite shorts insert →
노션 'proposed' 페이지 create. 영빈이 노션에서 ✅/❌.

scene_kind (talking_head/swing_demo/comparison)는 현재 SQLite schema에 미반영 —
opening_line prefix `[kind=...]` 로 임시 저장. 추후 production이 scene_kind 받게 되면
스키마 컬럼 추가 + 마이그레이션.
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
from app.storage.models import MagicMoment

YOUTUBE_ID = "mAFLAosow9M"
TRANSCRIPT_PATH = Path("data/transcripts") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {
        "start_sec": 174.918, "end_sec": 240.156,
        "hook_text": "앞소리 통념 반박",
        "copy1": "앞에서 소리 내면", "copy2": "저는 반대입니다!",
        "scene_kind": "comparison", "score": 9.2,
        "reasoning": "프로가 통념을 정면 반박하는 의외성 패턴. 앞에서 소리→반대, 임팩트 직전 가속 원리를 실제 드릴로 증명.",
    },
    {
        "start_sec": 549.917, "end_sec": 615.016,
        "hook_text": "300야드 잠재능력",
        "copy1": "헤드스피드는 잠재능력", "copy2": "300야드 이미 됩니다!",
        "scene_kind": "talking_head", "score": 9.1,
        "reasoning": "300야드 선언이라는 구체 수치 + 잠재능력이라는 의외의 프레임. 76.9→77.7 수치 상승 현장 확인.",
    },
    {
        "start_sec": 609.674, "end_sec": 675.863,
        "hook_text": "위로 휘두르기 300",
        "copy1": "10개 중 1개 걸리면", "copy2": "300야드 나갑니다!",
        "scene_kind": "swing_demo", "score": 9.0,
        "reasoning": "위로 휘두르기 드릴 + 78.5 실측 달성 + 300야드 가능성 선언. 구체 드릴과 숫자 결과 동시.",
    },
    {
        "start_sec": 368.914, "end_sec": 435.558,
        "hook_text": "3가지 핵심 정리",
        "copy1": "백스윙 대각선 크게", "copy2": "다운스윙 엑셀 밟아요!",
        "scene_kind": "swing_demo", "score": 8.6,
        "reasoning": "백스윙-가속-지면력 3요소를 구조화해 정리하고 즉시 드릴로 이행. 실천 가능한 요약.",
    },
    {
        "start_sec": 61.572, "end_sec": 130.261,
        "hook_text": "오른쪽 어깨 대각선",
        "copy1": "오른쪽 어깨 대각선", "copy2": "이렇게 들면 됩니다!",
        "scene_kind": "swing_demo", "score": 8.2,
        "reasoning": "구체 신체부위(오른쪽 어깨) + 방향(대각선) 지시어가 즉시 행동으로 연결. 백스윙 교정 핵심 cue.",
    },
    {
        "start_sec": 308.782, "end_sec": 370.255,
        "hook_text": "대강 쳐도 70",
        "copy1": "대강 때려도 70", "copy2": "몸에 익으면 됩니다!",
        "scene_kind": "talking_head", "score": 8.0,
        "reasoning": "대강 때려도 70 나온다는 결과 약속이 강력한 행동 유인. 레슨 효과 보상 철학.",
    },
    {
        "start_sec": 707.473, "end_sec": 774.062,
        "hook_text": "왼쪽 3시간 훈련",
        "copy1": "왼쪽 공만 3시간씩", "copy2": "78.8 인생 볼스피드!",
        "scene_kind": "talking_head", "score": 7.9,
        "reasoning": "왼쪽으로 3시간씩 훈련한 프로의 비법 공개 + 78.8/293야드 인생 수치 마무리.",
    },
]


def _extract_opening_line(words: list[dict], start_sec: float) -> str:
    """모먼트 start_sec 시점부터 첫 ~20 단어."""
    matched = [w for w in words if float(w.get("start", 0)) >= start_sec]
    if not matched:
        return ""
    text = " ".join((w.get("text") or "").strip() for w in matched[:20]).strip()
    return text[:200]


def main() -> int:
    # transcript에서 모든 단어 flatten
    with TRANSCRIPT_PATH.open(encoding="utf-8") as f:
        tx = json.load(f)
    all_words: list[dict] = []
    for seg in tx.get("segments", []):
        for w in seg.get("words", []):
            all_words.append(w)
    all_words.sort(key=lambda w: float(w.get("start", 0)))

    conn = get_connection()
    try:
        video = get_video_by_youtube_id(conn, YOUTUBE_ID)
        if not video:
            print(f"video not found: {YOUTUBE_ID}")
            return 1
        print(f"video: id={video.id} channel={video.channel} internal_id={video.internal_id}")

        # 기존 P021 모먼트 (혹시 있다면) 확인 — 중복 방지
        existing = conn.execute(
            "SELECT internal_id FROM shorts WHERE source_video_id = ?",
            (video.id,),
        ).fetchall()
        if existing:
            print(f"이미 P021 모먼트 {len(existing)}개 존재 — 중단")
            for r in existing:
                print(f"  - {r['internal_id']}")
            return 0

        created = 0
        for i, m in enumerate(CURATOR_MOMENTS, 1):
            short_iid = f"26-P021-S{i:02d}"
            opening = _extract_opening_line(all_words, m["start_sec"])
            # scene_kind를 reasoning 끝에 prefix tag로 저장 (추후 스키마 컬럼 추가 시 마이그레이션 쉬움)
            reasoning_with_kind = f"[kind={m['scene_kind']}] {m['reasoning']}"

            # SQLite insert (status='proposed', scene_type=None → approve 시점 classify_scene 자동 호출)
            conn.execute(
                """
                INSERT INTO shorts
                    (source_video_id, start_time, end_time, score,
                     scene_type, face_center_x, face_segments,
                     opening_line, status, internal_id, copy1, copy2, channel)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (
                    video.id, m["start_sec"], m["end_sec"], m["score"],
                    None, None, None,
                    opening, short_iid, m["copy1"], m["copy2"], video.channel,
                ),
            )
            short_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # MagicMoment 객체 구성 (notion_create_page 호출용)
            moment_obj = MagicMoment(
                start_sec=m["start_sec"],
                end_sec=m["end_sec"],
                hook_text=m["hook_text"],
                copy1=m["copy1"],
                copy2=m["copy2"],
                score=m["score"],
                reasoning=reasoning_with_kind,
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
                print(f"  {short_iid} notion FAIL: {e}", flush=True)

        conn.commit()
        print(f"\nDONE: {created}/{len(CURATOR_MOMENTS)} 모먼트 노션 push 완료")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
