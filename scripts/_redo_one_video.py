"""특정 영상 모먼트 재처리 (template/edit fix 검증용, 1회용).

usage: .venv\\Scripts\\python.exe scripts/_redo_one_video.py --internal-prefix 26-P002

- prefix로 시작하는 internal_id 모먼트만 (status='scheduled') ffmpeg 재생성
- R2 overwrite + 기존 YouTube delete + 새 upload + 노션 preview URL update

scene 재분류는 X (기존 cx/scene_type 그대로). template + edit fix만 영향.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from app.config import settings
from app.integrations import r2
from app.integrations.notion import update_status as notion_update
from app.integrations.youtube import (
    delete_video,
    upload_short as youtube_upload,
    video_url as youtube_video_url,
)
from app.pipeline.edit import make_short
from app.pipeline.publish import _find_moment, _resolve_meta
from app.storage.db import get_connection
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)
YT_URL_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})")


def _scheduled_at_to_utc_iso(s: str) -> str:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redo_one(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    iid = row["internal_id"]
    yid = row["youtube_id"]
    page_id = row["notion_page_id"]
    generated_path = Path(row["generated_path"])
    mp4_src = settings.samples_dir / f"{yid}.mp4"

    # cache moment
    moment = _find_moment(yid, row["start_time"], row["end_time"])
    if moment is None:
        return f"{iid}: cache moment not found"

    # face_segments
    segs_json = row["face_segments"]
    face_segs: list[tuple[float, float, float]] | None = None
    if segs_json:
        try:
            raw = json.loads(segs_json)
            face_segs = [(float(s), float(e), float(c)) for s, e, c in raw]
        except Exception:
            face_segs = None

    # ffmpeg 재생성
    make_short(
        mp4_src,
        row["start_time"], row["end_time"],
        row["scene_type"],  # type: ignore[arg-type]
        moment.copy1, moment.copy2,
        generated_path,
        face_center_x=row["face_center_x"],
        face_segments=face_segs,
        internal_id=iid,
    )

    # 기존 YouTube delete
    pu = json.loads(row["published_urls"] or "{}")
    old_yt = pu.get("youtube") or ""
    m = YT_URL_RE.search(old_yt)
    if m:
        try:
            delete_video(m.group(1))
        except Exception as e:
            log.warning("redo.youtube_delete_failed", error=str(e))

    # R2 강제 overwrite
    r2.upload_video(generated_path, key=f"{iid}.mp4")

    # YouTube 재업로드
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
    pu["youtube"] = new_yt_url
    conn.execute(
        "UPDATE shorts SET published_urls = ? WHERE id = ?",
        (json.dumps(pu, ensure_ascii=False), row["id"]),
    )
    conn.commit()

    # 노션 preview update (status 그대로 — 영빈이 다시 검토 후 결정)
    try:
        notion_update(page_id, row["status"] or "scheduled", preview_url=new_yt_url)
    except Exception as e:
        log.warning("redo.notion_update_failed", error=str(e))

    return f"{iid}: OK new YT {new_video_id}"


@click.command()
@click.option("--internal-prefix", required=True, help="예: 26-P002")
def main(internal_prefix: str) -> None:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT s.*, v.youtube_id FROM shorts s "
            "JOIN videos v ON s.source_video_id = v.id "
            "WHERE s.internal_id LIKE ? "
            "  AND s.status = 'scheduled' "
            "  AND s.generated_path IS NOT NULL "
            "ORDER BY s.internal_id",
            (f"{internal_prefix}-S%",),
        ).fetchall()
        print(f"=== redo {len(rows)} moments matching {internal_prefix}-S* ===", flush=True)
        if not rows:
            return
        start_t = time.time()
        for idx, row in enumerate(rows, 1):
            print(f"\n[{idx}/{len(rows)}] elapsed {int(time.time()-start_t)}s", flush=True)
            try:
                result = _redo_one(conn, row)
                print(f"  {result}", flush=True)
            except Exception as e:
                log.warning("redo.failed", iid=row["internal_id"], error=str(e))
                print(f"  FAIL: {e}", flush=True)
        print(f"\n=== done in {int(time.time()-start_t)}s ===", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
