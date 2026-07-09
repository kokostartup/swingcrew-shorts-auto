"""1회용: 26-B002-S05 모먼트로 단일 정적 cx crop 샘플 생성.

목적:
  현재 face_centered_dynamic은 segment별 cx 변화 + fc 분기로 모드 플리커 발생.
  새 룰: scene_kind=talking_head → 모먼트 전체에 단일 cx 고정 crop, segment 폐기.

출력: outputs/shorts/26-B002-S05_singlecrop_sample.mp4
비교: 원본 YouTube 게시본 (또는 segment 기반 재생성본)
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.pipeline.edit import make_short

INTERNAL_ID = "26-B002-S05"
OUTPUT = Path("outputs/shorts/26-B002-S05_singlecrop_sample.mp4")


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT s.*, v.youtube_id FROM shorts s "
        "JOIN videos v ON v.id=s.source_video_id "
        "WHERE s.internal_id=?",
        (INTERNAL_ID,),
    ).fetchone()
    conn.close()
    if not r:
        print(f"no moment: {INTERNAL_ID}")
        return 1

    src = settings.samples_dir / f"{r['youtube_id']}.mp4"
    if not src.exists():
        print(f"source mp4 not found: {src}")
        return 1

    print(f"=== {INTERNAL_ID} single static cx crop sample ===")
    print(f"src: {src}")
    print(f"range: {r['start_time']:.2f} ~ {r['end_time']:.2f}")
    print(f"single cx: {r['face_center_x']:.3f} (모먼트 평균)")
    print(f"copy1: {r['copy1']}")
    print(f"copy2: {r['copy2']}")
    print(f"output: {OUTPUT}")
    print()

    make_short(
        src=src,
        start=float(r["start_time"]),
        end=float(r["end_time"]),
        strategy="face_centered_4_5",
        copy1=r["copy1"] or "",
        copy2=r["copy2"] or "",
        output=OUTPUT,
        face_center_x=float(r["face_center_x"]),
        internal_id=INTERNAL_ID,
    )

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\nDONE: {OUTPUT} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
