"""1개 모먼트 scene 재분류 (face_count 포함) + remake_mp4 (1회용, dynamic mix 검증).

usage: scripts/_rescan_and_remake.py --internal-id 26-P002-S04

흐름:
1. classify_scene_with_metrics 재호출 → 새 segments (4-tuple, face_count 포함)
2. SQLite shorts UPDATE (face_segments, scene_type, face_center_x)
3. remake_mp4 로 ffmpeg 재생성 (다음 번호 -N)
"""
from __future__ import annotations

import json
import sys

import click

from app.config import settings
from app.pipeline.scene import classify_scene_with_metrics
from app.storage.db import get_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option("--internal-id", required=True)
def main(internal_id: str) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT s.*, v.youtube_id FROM shorts s "
            "JOIN videos v ON s.source_video_id = v.id "
            "WHERE s.internal_id = ?",
            (internal_id,),
        ).fetchone()
        if row is None:
            raise click.ClickException(f"{internal_id}: SQLite에 없음")
        yid = row["youtube_id"]
        mp4 = settings.samples_dir / f"{yid}.mp4"
        print(f"=== scene 재분류 {internal_id} ===", flush=True)
        scene, cx, segments = classify_scene_with_metrics(
            mp4, row["start_time"], row["end_time"],
        )
        print(f"  scene: {scene}  cx: {cx}", flush=True)
        if segments:
            print(f"  segments ({len(segments)}):", flush=True)
            for s in segments:
                print(f"    {s[0]:5.1f}-{s[1]:5.1f}s cx={s[2]:.3f} face_count={s[3]}", flush=True)
        segs_json = json.dumps([list(s) for s in segments]) if segments else None
        conn.execute(
            "UPDATE shorts SET scene_type=?, face_center_x=?, face_segments=? WHERE id=?",
            (scene, cx, segs_json, row["id"]),
        )
        conn.commit()
        print("  DB updated.", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
