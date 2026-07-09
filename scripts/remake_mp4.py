"""ffmpeg만 재생성 — R2/YouTube/노션 X. 번호 자동 부여 (-1, -2, -3...).

usage:
    .venv\\Scripts\\python.exe scripts/remake_mp4.py --internal-id 26-P002-S01

기존 {internal_id}-N.mp4 파일들 검사해서 다음 N으로 저장.
검증 끝나면 영빈이 직접 mp4 재생해서 확인.
"""
from __future__ import annotations

import json
import re
import sys

import click

from app.config import settings
from app.pipeline.analyze import load_cached_analysis
from app.pipeline.edit import make_short
from app.storage.db import get_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def _next_remake_number(internal_id: str) -> int:
    """outputs/shorts/{internal_id}-N.mp4 중 가장 큰 N + 1."""
    pattern = re.compile(rf"^{re.escape(internal_id)}-(\d+)\.mp4$")
    nums: list[int] = []
    for fp in settings.shorts_output_dir.glob(f"{internal_id}-*.mp4"):
        m = pattern.match(fp.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) if nums else 0) + 1


def _process_one(internal_id: str) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT s.*, v.youtube_id FROM shorts s "
            "JOIN videos v ON s.source_video_id = v.id "
            "WHERE s.internal_id = ?",
            (internal_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        print(f"{internal_id}: SQLite에 없음 — skip", flush=True)
        return

    youtube_id = row["youtube_id"]
    cached = load_cached_analysis(youtube_id)
    if cached is None:
        raise click.ClickException(f"{youtube_id}: analysis cache 없음")
    moment = next(
        (m for m in cached.moments if abs(m.start_sec - row["start_time"]) < 0.5),
        None,
    )
    if moment is None and cached.moments:
        nearest = min(
            cached.moments,
            key=lambda m: abs(m.start_sec - row["start_time"]),
        )
        moment = nearest.model_copy(update={
            "start_sec": row["start_time"],
            "end_sec": row["end_time"],
        })
    if moment is None:
        raise click.ClickException(f"{internal_id}: cache moment 없음")

    # 영빈 노션 Hook override (SQLite copy1/copy2) 반영 — 없으면 cache copy 그대로.
    copy_overrides: dict[str, str] = {}
    if row["copy1"]:
        copy_overrides["copy1"] = row["copy1"]
    if row["copy2"]:
        copy_overrides["copy2"] = row["copy2"]
    if copy_overrides:
        moment = moment.model_copy(update=copy_overrides)

    # face_segments parse — 3-tuple (legacy) / 4-tuple 호환.
    segs_json = row["face_segments"]
    face_segs: list[tuple[float, float, float, int]] | None = None
    if segs_json:
        try:
            raw = json.loads(segs_json)
            face_segs = [
                (
                    float(s[0]), float(s[1]), float(s[2]),
                    int(s[3]) if len(s) >= 4 else 1,
                )
                for s in raw
            ]
        except Exception:
            face_segs = None

    n = _next_remake_number(internal_id)
    out_path = settings.shorts_output_dir / f"{internal_id}-{n}.mp4"
    src = settings.samples_dir / f"{youtube_id}.mp4"

    print(f"=== {internal_id} remake #{n} ===")
    print(f"  src: {src}")
    print(f"  out: {out_path}")
    print(f"  scene: {row['scene_type']}  cx: {row['face_center_x']}")
    print(f"  copy1: {moment.copy1}  copy2: {moment.copy2}")

    make_short(
        src,
        row["start_time"], row["end_time"],
        row["scene_type"],  # type: ignore[arg-type]
        moment.copy1, moment.copy2,
        out_path,
        face_center_x=row["face_center_x"],
        face_segments=face_segs,
        internal_id=internal_id,
    )

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"DONE: {out_path} ({size_mb:.1f} MB)\n", flush=True)


@click.command()
@click.option("--internal-id", default=None, help="예: 26-P002-S01 (단일)")
@click.option("--prefix", default=None,
              help="예: 26-P002 (그 영상의 모먼트 다 처리). 쉼표로 다중 OK: 26-B002,26-P002")
def main(internal_id: str | None, prefix: str | None) -> None:
    if not internal_id and not prefix:
        raise click.ClickException("--internal-id 또는 --prefix 필요")

    targets: list[str] = []
    if internal_id:
        targets.append(internal_id)
    if prefix:
        prefixes = [p.strip() for p in prefix.split(",") if p.strip()]
        conn = get_connection()
        try:
            for p in prefixes:
                rows = conn.execute(
                    "SELECT internal_id FROM shorts WHERE internal_id LIKE ? "
                    "ORDER BY internal_id",
                    (f"{p}-S%",),
                ).fetchall()
                targets.extend(r["internal_id"] for r in rows)
        finally:
            conn.close()
    if not targets:
        raise click.ClickException("매칭되는 모먼트 없음")

    print(f"=== remake_mp4: {len(targets)} 모먼트 ===\n", flush=True)
    for iid in targets:
        try:
            _process_one(iid)
        except Exception as e:
            print(f"{iid}: FAIL — {e}\n", flush=True)
    print("=== done ===", flush=True)


if __name__ == "__main__":
    main()
