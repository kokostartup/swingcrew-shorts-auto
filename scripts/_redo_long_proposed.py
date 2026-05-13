"""긴 P 영상 10개의 'proposed' 모먼트 재분석 (동적 max_moments 적용, 1회용).

영빈 결정: B 시리즈는 5 고정, P 시리즈는 max(3, duration//120).
대상 영상은 'proposed' 모먼트가 있는 긴 P 영상만 (generated/scheduled는 보존).

흐름:
1. 영상별 'proposed' shorts row → 노션 page archive + SQLite delete
2. analyses JSON 백업 후 삭제 (cache miss 강제)
3. analyze() 호출 — 새 dynamic max_moments로 Gemini 재호출
4. sync_to_notion → 새 모먼트 노션 push
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from app.config import settings
from app.integrations.notion import _get_client
from app.pipeline.analyze import _dynamic_max_moments, analyze
from app.pipeline.approve import sync_to_notion
from app.pipeline.ingest import ingest
from app.pipeline.transcribe import transcribe
from app.storage.db import get_connection
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)

TARGETS: list[tuple[str, str]] = [
    ("26-P004", "JoKK-yA195I"),
    ("26-P005", "bI1X-3CzqrU"),
    ("26-P006", "Lv-dE1jPVHQ"),
    ("26-P007", "q7mhUH4OunI"),
    ("26-P010", "O75_HX8CYlc"),
    ("26-P012", "BprLGji8cL4"),
    ("26-P013", "tM4KJj2wlw8"),
    ("26-P015", "9yoZayGczOA"),
    ("26-P016", "elVfUEZoldw"),
    ("26-P019", "tWaB_vKqQlo"),
]


def _archive_proposed_and_purge(yid: str) -> tuple[int, int]:
    """영상의 'proposed' shorts: 노션 page archive + SQLite DELETE.

    Returns: (archived_count, sqlite_deleted_count).
    """
    client = _get_client()
    conn = get_connection()
    try:
        video_row = conn.execute(
            "SELECT id FROM videos WHERE youtube_id = ?", (yid,),
        ).fetchone()
        if video_row is None:
            return (0, 0)
        rows = conn.execute(
            "SELECT id, notion_page_id FROM shorts "
            "WHERE source_video_id = ? AND status = 'proposed'",
            (video_row["id"],),
        ).fetchall()
        archived = 0
        deleted = 0
        for r in rows:
            page_id = r["notion_page_id"]
            if page_id:
                try:
                    client.pages.update(page_id=page_id, archived=True)
                    archived += 1
                    time.sleep(0.35)  # 노션 rate limit
                except Exception as e:
                    log.warning(
                        "redo.notion_archive_failed",
                        page_id=page_id, error=str(e),
                    )
            conn.execute("DELETE FROM shorts WHERE id = ?", (r["id"],))
            deleted += 1
        conn.commit()
        return (archived, deleted)
    finally:
        conn.close()


def main() -> None:
    start_t = time.time()
    print(f"=== redo_long_proposed start: {len(TARGETS)} videos ===", flush=True)

    ok = 0
    failed: list[str] = []
    for idx, (iid, yid) in enumerate(TARGETS, 1):
        print(f"\n[{idx}/{len(TARGETS)}] {iid} ({yid})", flush=True)

        # 1. 기존 'proposed' archive + purge
        try:
            archived, deleted = _archive_proposed_and_purge(yid)
            print(f"  archived={archived} sqlite_deleted={deleted}", flush=True)
        except Exception as e:
            print(f"  archive failed: {e}", flush=True)
            failed.append(iid)
            continue

        # 2. analysis JSON 백업 + 삭제
        ap = settings.analyses_dir / f"{yid}.json"
        if ap.exists():
            shutil.copy(ap, ap.with_suffix(".json.bak"))
            ap.unlink()

        # 3. 재분석
        try:
            video = ingest(yid)
            new_max = _dynamic_max_moments(video)
            print(f"  duration={video.duration}s → max_moments={new_max}", flush=True)
            transcript = transcribe(video)
            result = analyze(video, transcript)
            print(f"  analyzed: {len(result.moments)} moments", flush=True)
        except Exception as e:
            log.warning("redo.analyze_failed", yid=yid, error=str(e))
            print(f"  analyze failed: {e}", flush=True)
            failed.append(iid)
            continue

        # 4. 노션 push (sync_to_notion은 notion_page_id NULL인 새 행만)
        try:
            created = sync_to_notion(video, result)
            print(f"  notion pushed: {created}", flush=True)
            ok += 1
        except Exception as e:
            log.warning("redo.sync_failed", yid=yid, error=str(e))
            print(f"  sync failed: {e}", flush=True)
            failed.append(iid)

    elapsed = int(time.time() - start_t)
    print(
        f"\n=== redo_long_proposed done in {elapsed}s — "
        f"ok={ok}/{len(TARGETS)} failed={failed} ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
