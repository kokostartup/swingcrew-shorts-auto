"""아직 게시 안 된 scheduled 모먼트의 mp4를 R2에 재업로드 (유실 복구용).

2026-07-15 사고: publish_socials의 R2 cleanup이 미래 슬롯용 mp4를 삭제
(P030 11개 유실). SQLite generated_path의 로컬 mp4를 {internal_id}.mp4 키로
다시 올린다. 이미 R2에 있는 키는 skip.

렌더 파일이 있는 PC(운영 PC)에서 실행:
    uv run python scripts/r2_reupload.py --prefix 26-P030
    uv run python scripts/r2_reupload.py --internal-id 26-P030-S02 --internal-id 26-P030-S03
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from app.integrations import r2
from app.storage.db import get_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option("--prefix", default=None, help="internal_id prefix (예: 26-P030).")
@click.option(
    "--internal-id",
    "internal_ids",
    multiple=True,
    help="특정 internal_id만. --prefix와 병행 가능.",
)
@click.option("--dry-run", is_flag=True, help="업로드 없이 대상만 출력.")
def main(prefix: str | None, internal_ids: tuple[str, ...], dry_run: bool) -> None:
    if not prefix and not internal_ids:
        raise click.UsageError("--prefix 또는 --internal-id 필요")

    conn = get_connection()
    try:
        sql = (
            "SELECT internal_id, generated_path FROM shorts "
            "WHERE status = 'scheduled' AND generated_path IS NOT NULL"
        )
        rows = [
            r
            for r in conn.execute(sql).fetchall()
            if (prefix and r["internal_id"].startswith(prefix)) or r["internal_id"] in internal_ids
        ]
    finally:
        conn.close()

    if not rows:
        click.echo("대상 없음")
        sys.exit(1)

    failed = 0
    for r in rows:
        iid = r["internal_id"]
        key = f"{iid}.mp4"
        path = Path(r["generated_path"])
        if not path.exists():
            click.echo(f"SKIP {iid}: 로컬 mp4 없음 ({path})")
            failed += 1
            continue
        if r2.object_exists(key):
            click.echo(f"SKIP {iid}: R2에 이미 존재")
            continue
        if dry_run:
            click.echo(f"DRY {iid}: {path} → {key}")
            continue
        url = r2.upload_video(path, key=key)
        click.echo(f"OK {iid}: {url}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
