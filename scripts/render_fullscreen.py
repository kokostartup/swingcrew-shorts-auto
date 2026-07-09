"""P시리즈 풀스크린 9:16 렌더 CLI — framing spec 기반 (Claude Code 세션 전용).

전제: golf-framing-director + golf-subtitle-editor 에이전트 출력이
data/framing/<internal_id>.json 으로 저장돼 있어야 함.

렌더 후 process_approved와 동일하게 SQLite status='generated' + generated_path
업데이트, 노션 '생성' 전환. publish_meta는 건드리지 않음 (메인이
golf-publish-meta-writer 에이전트로 처리).

Example:
    uv run python scripts/render_fullscreen.py --internal-id 26-P027-S01
    uv run python scripts/render_fullscreen.py --internal-id 26-P027-S01 --skip-db
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from app.config import settings
from app.integrations.notion import update_status as notion_update
from app.pipeline.fullscreen import load_framing_spec, render_fullscreen
from app.storage.db import get_connection
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)


@click.command()
@click.option(
    "--internal-id",
    "-i",
    "internal_ids",
    multiple=True,
    required=True,
    help="shorts internal_id (예: 26-P027-S01). 여러 개 지정 가능.",
)
@click.option("--skip-db", is_flag=True, help="렌더만 하고 SQLite/노션 업데이트 안 함 (검증용).")
def main(internal_ids: tuple[str, ...], skip_db: bool) -> None:
    """framing spec으로 풀스크린 mp4 렌더 + (기본) status='generated' 전환."""
    conn = get_connection()
    failed = 0
    try:
        for iid in internal_ids:
            row = conn.execute(
                "SELECT s.*, v.local_path FROM shorts s "
                "JOIN videos v ON s.source_video_id = v.id "
                "WHERE s.internal_id = ?",
                (iid,),
            ).fetchone()
            if row is None:
                click.echo(f"SKIP {iid}: shorts row 없음")
                failed += 1
                continue
            spec_path = settings.framing_dir / f"{iid}.json"
            if not spec_path.exists():
                click.echo(f"SKIP {iid}: framing spec 없음 ({spec_path})")
                failed += 1
                continue
            video_path = Path(row["local_path"])
            if not video_path.exists():
                click.echo(f"SKIP {iid}: 소스 영상 없음 ({video_path})")
                failed += 1
                continue

            spec = load_framing_spec(iid)
            output = settings.shorts_output_dir / f"{iid}.mp4"
            render_fullscreen(
                video_path,
                spec,
                row["start_time"],
                row["end_time"],
                output,
            )
            click.echo(f"OK {iid}: {output} ({output.stat().st_size / 1e6:.1f} MB)")

            if skip_db:
                continue
            conn.execute(
                "UPDATE shorts SET status = 'generated', generated_path = ? WHERE id = ?",
                (str(output), row["id"]),
            )
            conn.commit()
            if row["notion_page_id"]:
                try:
                    notion_update(
                        row["notion_page_id"],
                        "generated",
                        internal_id=iid,
                    )
                except Exception as e:  # noqa: BLE001 — 노션 실패는 렌더 성공에 비치명
                    log.warning(
                        "render_fullscreen.notion_update_failed",
                        internal_id=iid,
                        error=str(e),
                    )
    finally:
        conn.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
