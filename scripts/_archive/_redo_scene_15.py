"""YOLOv8-face 도입 후 15개 모먼트 scene 재분류 + 재처리 (1회용).

흐름 (per moment):
1. classify_scene_with_metrics (새 YOLO detector)
2. SQLite UPDATE (scene_type, face_center_x, face_segments)
3. ffmpeg 재생성 (덮어쓰기)
4. 기존 YouTube video delete (private 상태라 안전)
5. R2 강제 overwrite upload
6. YouTube 재업로드 (publishAt 그대로 KST→UTC)
7. 노션 published_urls + preview_url update
8. SQLite published_urls 갱신

Buffer는 skip (24h rate limit, 5/14 02:00+ 별도 재시도).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.integrations import r2
from app.integrations.notion import update_status as notion_update
from app.integrations.youtube import (
    delete_video,
    upload_short as youtube_upload,
    video_url as youtube_video_url,
)
from app.pipeline.analyze import load_cached_analysis
from app.pipeline.edit import make_short
from app.pipeline.publish import _find_moment, _resolve_meta
from app.pipeline.scene import classify_scene_with_metrics
from app.storage.db import get_connection
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)

YT_URL_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})")


def _scheduled_at_to_utc_iso(scheduled_at: str) -> str:
    """노션 KST ISO → YouTube publishAt UTC ISO."""
    dt = datetime.fromisoformat(scheduled_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redo_one(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """단일 모먼트 재처리. 결과 상태 문자열 반환."""
    short_id = row["id"]
    iid = row["internal_id"]
    youtube_id = row["youtube_id"]
    page_id = row["notion_page_id"]
    generated_path = Path(row["generated_path"])
    if not generated_path.exists():
        return f"{iid}: generated_path missing"

    # 1. scene 재분류 (YOLO)
    mp4_src = settings.samples_dir / f"{youtube_id}.mp4"
    new_scene, new_cx, new_segments = classify_scene_with_metrics(
        mp4_src, row["start_time"], row["end_time"],
    )
    old_scene = row["scene_type"]
    print(
        f"{iid}: scene {old_scene} → {new_scene} | "
        f"cx {row['face_center_x']} → {new_cx}",
        flush=True,
    )

    # 2. SQLite UPDATE
    segments_json = (
        json.dumps([list(s) for s in new_segments]) if new_segments else None
    )
    conn.execute(
        "UPDATE shorts SET scene_type = ?, face_center_x = ?, "
        "face_segments = ? WHERE id = ?",
        (new_scene, new_cx, segments_json, short_id),
    )
    conn.commit()

    # 3. ffmpeg 재생성
    moment = _find_moment(youtube_id, row["start_time"], row["end_time"])
    if moment is None:
        return f"{iid}: cache moment not found"

    # face_segments tuple list 변환 (4-tuple).
    face_segs_tuple: list[tuple[float, float, float, int]] | None = (
        [
            (float(s[0]), float(s[1]), float(s[2]), int(s[3]) if len(s) >= 4 else 1)
            for s in new_segments
        ]
        if new_segments else None
    )

    make_short(
        mp4_src,
        row["start_time"], row["end_time"],
        new_scene,  # type: ignore[arg-type]
        moment.copy1, moment.copy2,
        generated_path,
        face_center_x=new_cx,
        face_segments=face_segs_tuple,
        internal_id=iid,
    )

    # 4. 기존 YouTube delete
    pu = json.loads(row["published_urls"] or "{}")
    old_yt = pu.get("youtube") or ""
    m = YT_URL_RE.search(old_yt)
    if m:
        old_video_id = m.group(1)
        try:
            delete_video(old_video_id)
        except Exception as e:
            log.warning("redo.youtube_delete_failed", video_id=old_video_id, error=str(e))

    # 5. R2 강제 overwrite (boto3 put_object는 자동 overwrite)
    key = f"{iid}.mp4"
    r2.upload_video(generated_path, key=key)

    # 6. YouTube 재업로드
    meta = _resolve_meta(row, page_id, moment)
    if meta is None:
        return f"{iid}: meta unresolved"
    publish_at_utc = _scheduled_at_to_utc_iso(row["scheduled_at"])
    new_video_id = youtube_upload(
        video_path=generated_path,
        title=meta.title,
        description=meta.description,
        tags=meta.tags,
        publish_at_utc=publish_at_utc,
    )
    new_yt_url = youtube_video_url(new_video_id)

    # 7. SQLite published_urls 갱신
    pu["youtube"] = new_yt_url
    conn.execute(
        "UPDATE shorts SET published_urls = ? WHERE id = ?",
        (json.dumps(pu, ensure_ascii=False), short_id),
    )
    conn.commit()

    # 8. 노션 preview_url update (Scene Type도 동기화)
    try:
        notion_update(page_id, "scheduled", preview_url=new_yt_url)
    except Exception as e:
        log.warning("redo.notion_update_failed", page_id=page_id, error=str(e))

    return f"{iid}: OK ({old_scene}→{new_scene}, new YT {new_video_id})"


def main() -> None:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT s.*, v.youtube_id FROM shorts s
            JOIN videos v ON s.source_video_id = v.id
            WHERE s.status = 'scheduled'
              AND (s.internal_id LIKE '26-P002-S%'
                   OR s.internal_id LIKE '26-B002-S%'
                   OR s.internal_id LIKE '26-P003-S%')
            ORDER BY s.internal_id
        """).fetchall()
        print(f"=== redo_scene_15 start: {len(rows)} moments ===", flush=True)
        start_t = time.time()
        ok = 0
        failed: list[str] = []
        for idx, row in enumerate(rows, 1):
            print(
                f"\n[{idx}/{len(rows)}] elapsed {int(time.time()-start_t)}s",
                flush=True,
            )
            try:
                result = _redo_one(conn, row)
                print(f"  {result}", flush=True)
                if "OK" in result:
                    ok += 1
                else:
                    failed.append(row["internal_id"])
            except Exception as e:
                log.warning("redo.failed", iid=row["internal_id"], error=str(e))
                print(f"  FAIL: {e}", flush=True)
                failed.append(row["internal_id"])
        elapsed = int(time.time() - start_t)
        print(
            f"\n=== redo_scene_15 done in {elapsed}s — "
            f"ok={ok}/{len(rows)} failed={failed} ===",
            flush=True,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
