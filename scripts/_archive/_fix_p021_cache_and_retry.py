"""1회용: P021 analyses cache 생성 + error→approved 복원 + process_approved 재실행.

원인: agent path가 data/analyses/<youtube_id>.json 캐시를 안 만들어서
process_approved의 load_cached_analysis가 None 반환 → 'analysis_cache_missing' error.

흐름:
  1. 우리 7개 curator 모먼트로 AnalysisResult 구성 → data/analyses/mAFLAosow9M.json 작성
  2. SQLite + 노션 'error' 상태 6개 (S01-S06) → 'approved' 복원
     (S07은 rejected 그대로)
  3. process_approved 호출 (Gemini publish_meta + blur padding ffmpeg)
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.notion import update_status as notion_update
from app.pipeline.approve import process_approved
from app.storage.models import AnalysisResult, MagicMoment

YOUTUBE_ID = "mAFLAosow9M"
ANALYSES_PATH = Path("data/analyses") / f"{YOUTUBE_ID}.json"

CURATOR_MOMENTS = [
    {
        "internal_id": "26-P021-S01",
        "start_sec": 174.918, "end_sec": 240.156,
        "hook_text": "앞에서 소리는",
        "copy1": "앞에서 소리는", "copy2": "저는 반대입니다!",
        "scene_kind": "comparison", "score": 9.2,
        "reasoning": "[kind=comparison] 통념 정면 반박 — 앞에서 소리 통설을 뒤집는 setup이 copy1, 반박 선언이 copy2.",
    },
    {
        "internal_id": "26-P021-S02",
        "start_sec": 549.917, "end_sec": 615.016,
        "hook_text": "헤드스피드는",
        "copy1": "헤드스피드는", "copy2": "이미 300 됩니다!",
        "scene_kind": "talking_head", "score": 9.1,
        "reasoning": "[kind=talking_head] 헤드스피드 = 잠재능력 선언 + 76.9→77.7 수치로 결과 약속.",
    },
    {
        "internal_id": "26-P021-S03",
        "start_sec": 609.674, "end_sec": 675.863,
        "hook_text": "위로 휘두르면",
        "copy1": "위로 휘두르면", "copy2": "78.5 바로 나와요!",
        "scene_kind": "swing_demo", "score": 9.0,
        "reasoning": "[kind=swing_demo] 위로 휘두르기 드릴 + 78.5 실측 수치 달성.",
    },
    {
        "internal_id": "26-P021-S04",
        "start_sec": 368.914, "end_sec": 435.558,
        "hook_text": "3가지만 하면",
        "copy1": "3가지만 하면", "copy2": "비거리 그냥 늘어요!",
        "scene_kind": "swing_demo", "score": 8.6,
        "reasoning": "[kind=swing_demo] 백스윙-가속-지면력 3요소를 구조화해 정리하고 즉시 드릴로 이행.",
    },
    {
        "internal_id": "26-P021-S05",
        "start_sec": 61.572, "end_sec": 130.261,
        "hook_text": "오른쪽 어깨를",
        "copy1": "오른쪽 어깨를", "copy2": "대각선 높게 드세요!",
        "scene_kind": "swing_demo", "score": 8.2,
        "reasoning": "[kind=swing_demo] 구체 신체부위(오른쪽 어깨) + 방향(대각선) 지시어가 즉시 행동으로 연결.",
    },
    {
        "internal_id": "26-P021-S06",
        "start_sec": 308.782, "end_sec": 370.255,
        "hook_text": "대강 때려도",
        "copy1": "대강 때려도", "copy2": "볼스피드 70 나와요!",
        "scene_kind": "talking_head", "score": 8.0,
        "reasoning": "[kind=talking_head] 레슨 효과 약속 — 대강 때려도 70 선언이 강력한 역설 hook.",
    },
    {
        "internal_id": "26-P021-S07",
        "start_sec": 707.473, "end_sec": 774.062,
        "hook_text": "왼쪽으로 3시간 치면",
        "copy1": "왼쪽으로 3시간 치면", "copy2": "인생 볼스피드 옵니다!",
        "scene_kind": "talking_head", "score": 7.9,
        "reasoning": "[kind=talking_head] 왼쪽 공 3시간 훈련 비법 + 78.8 인생 볼스피드 293야드 달성.",
    },
]


def _load_opening_lines() -> dict[float, str]:
    """transcript에서 각 모먼트 start_sec → opening_line 매핑."""
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
    # Step 1: analyses cache 작성
    print("[1/3] analyses cache 작성...")
    opening_lines = _load_opening_lines()
    moments: list[MagicMoment] = []
    for m in CURATOR_MOMENTS:
        mm = MagicMoment(
            start_sec=m["start_sec"],
            end_sec=m["end_sec"],
            hook_text=m["hook_text"],
            copy1=m["copy1"],
            copy2=m["copy2"],
            score=m["score"],
            reasoning=m["reasoning"],
            opening_line=opening_lines.get(m["start_sec"]),
        )
        moments.append(mm)

    result = AnalysisResult(
        youtube_id=YOUTUBE_ID,
        model="claude-opus-agent",
        moments=moments,
    )
    ANALYSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSES_PATH.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"  saved: {ANALYSES_PATH} ({len(moments)} moments)")

    # Step 2: error → approved 복원 (S01~S06만, S07은 rejected 그대로)
    print("\n[2/3] error → approved 복원...")
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, internal_id, notion_page_id FROM shorts "
        "WHERE internal_id LIKE '26-P021-S%' AND status='error'"
    ).fetchall()
    restored = 0
    for r in rows:
        conn.execute(
            "UPDATE shorts SET status='approved' WHERE id=?",
            (r["id"],),
        )
        if r["notion_page_id"]:
            try:
                notion_update(r["notion_page_id"], "approved")
                print(f"  {r['internal_id']} restored")
                restored += 1
            except Exception as e:
                print(f"  {r['internal_id']} notion FAIL: {e}")
    conn.commit()
    conn.close()
    print(f"  restored: {restored}")

    # Step 3: process_approved 재실행
    print("\n[3/3] process_approved 재실행...")
    n = process_approved()
    print(f"  processed: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
