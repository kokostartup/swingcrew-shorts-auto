"""기존 148개 모먼트의 start_sec snap + opening_line 재계산 + 노션 update (1회용).

영빈이 26-B002에서 발견한 mismatch (Gemini start_sec ≈ segment.end → silence + opening_line
fallback) 사후 패치. analyze.py에 _snap_start_sec 영구 추가됨.

처리 흐름:
1. data/analyses/*.json 모든 영상 iterate
2. transcript 로드 → 각 모먼트 _snap_start_sec
3. shift == 0이면 skip. shift > 0이면:
   - opening_line 재계산
   - analyses JSON 덮어쓰기
   - SQLite shorts UPDATE
   - 노션 page UPDATE (Start Sec, Time Range, Source Video, Opening Line)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.config import settings
from app.integrations.notion import (
    _get_client,
    _time_range,
    _youtube_timestamp_url,
)
from app.pipeline.analyze import _extract_opening_line, _snap_start_sec
from app.storage.db import get_connection
from app.storage.models import Transcript
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)


def main() -> None:
    client = _get_client()
    conn = get_connection()

    total = 0
    snapped = 0
    failed: list[str] = []

    try:
        for ap in sorted(Path("data/analyses").glob("*.json")):
            yid = ap.stem
            tp = Path(f"data/transcripts/{yid}.json")
            if not tp.exists():
                continue

            analysis = json.loads(ap.read_text(encoding="utf-8"))
            transcript = Transcript.model_validate_json(
                tp.read_text(encoding="utf-8"),
            )

            video_row = conn.execute(
                "SELECT id, internal_id FROM videos WHERE youtube_id = ?",
                (yid,),
            ).fetchone()
            if video_row is None:
                continue

            iid = video_row["internal_id"] or yid
            modified = False

            for m in analysis.get("moments", []):
                total += 1
                old_start = float(m["start_sec"])
                old_opening = m.get("opening_line")
                new_start = _snap_start_sec(transcript, old_start)
                new_opening = _extract_opening_line(transcript, new_start)
                start_changed = abs(new_start - old_start) >= 0.01
                opening_changed = (new_opening or "") != (old_opening or "")
                if not (start_changed or opening_changed):
                    continue

                shift = new_start - old_start
                m["start_sec"] = new_start
                m["opening_line"] = new_opening
                modified = True
                snapped += 1
                print(
                    f"{iid} start {old_start:.2f}→{new_start:.2f} (+{shift:.2f}s) "
                    f"| {(new_opening or '')[:50]}",
                    flush=True,
                )

                # SQLite UPDATE — match by source_video_id + 기존 start_time
                short_row = conn.execute(
                    "SELECT id, notion_page_id, end_time FROM shorts "
                    "WHERE source_video_id = ? "
                    "  AND ABS(start_time - ?) < 0.01",
                    (video_row["id"], old_start),
                ).fetchone()
                if short_row is None:
                    log.warning(
                        "snap.shorts_row_not_found",
                        yid=yid, old_start=old_start,
                    )
                    continue
                conn.execute(
                    "UPDATE shorts SET start_time = ?, opening_line = ? "
                    "WHERE id = ?",
                    (new_start, new_opening, short_row["id"]),
                )
                conn.commit()

                # 노션 page UPDATE
                page_id = short_row["notion_page_id"]
                if not page_id:
                    continue
                end_sec = float(short_row["end_time"])
                try:
                    client.pages.update(
                        page_id=page_id,
                        properties={
                            "Start Sec": {"number": round(new_start, 1)},
                            "Time Range": {
                                "rich_text": [{
                                    "text": {
                                        "content": _time_range(new_start, end_sec),
                                    },
                                }],
                            },
                            "Source Video": {
                                "url": _youtube_timestamp_url(yid, new_start),
                            },
                            "Opening Line": {
                                "rich_text": [{
                                    "text": {"content": (new_opening or "")[:1900]},
                                }],
                            },
                        },
                    )
                except Exception as e:
                    log.warning(
                        "snap.notion_update_failed",
                        page_id=page_id, error=str(e),
                    )
                    failed.append(f"{iid}@{old_start:.1f}")
                # 노션 API rate limit (~3 req/s)
                time.sleep(0.35)

            if modified:
                ap.write_text(
                    json.dumps(analysis, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    finally:
        conn.close()

    print(
        f"\n=== snap_existing done — total={total} snapped={snapped} "
        f"notion_failed={len(failed)} ===",
        flush=True,
    )
    if failed:
        print("failed pages:", failed, flush=True)


if __name__ == "__main__":
    main()
