"""노션 'published' 페이지의 R2 mp4를 일괄 삭제 (catch-up).

평상시엔 publish_socials_from_notion이 게시 직후 자동 삭제하지만,
- 그 hook 도입 전 게시된 것
- 자동 삭제가 swallow된 실패 케이스
이런 누락된 R2 파일을 정리하는 수동 도구.

사용:
    .venv/Scripts/python.exe scripts/r2_cleanup_published.py            # 실행
    .venv/Scripts/python.exe scripts/r2_cleanup_published.py --dry-run  # 후보만 출력
    .venv/Scripts/python.exe scripts/r2_cleanup_published.py --keys 26-E001-S01,26-E001-S02  # 명시 키
"""
from __future__ import annotations

import sqlite3
import sys

import click

from app.integrations import r2
from app.integrations.notion import list_pages_by_status
from app.storage.db import get_connection
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)


def _published_internal_ids(channel: str) -> list[str]:
    """삭제 후보 internal_id 리스트.

    - ko: 노션 status='게시' 페이지의 internal_id (SQLite cross-ref).
    - en: SQLite shorts.channel='en' 인 모든 internal_id (EN은 YouTube only → R2 fetch
      수요 0이므로 게시 여부 무관하게 R2에서 안전 삭제).
    """
    conn: sqlite3.Connection = get_connection()
    try:
        if channel == "en":
            rows = conn.execute(
                "SELECT internal_id FROM shorts "
                "WHERE channel = 'en' AND internal_id IS NOT NULL",
            ).fetchall()
            return [r["internal_id"] for r in rows]
        try:
            pages = list_pages_by_status("published", channel=channel)
        except Exception as e:
            click.echo(f"  channel={channel}: notion fetch failed: {e}", err=True)
            return []
        if not pages:
            return []
        iids: list[str] = []
        for p in pages:
            row = conn.execute(
                "SELECT internal_id FROM shorts WHERE notion_page_id = ?",
                (p["id"],),
            ).fetchone()
            if row and row["internal_id"]:
                iids.append(row["internal_id"])
        return iids
    finally:
        conn.close()


@click.command()
@click.option(
    "--dry-run", is_flag=True, help="삭제 안 하고 후보만 출력",
)
@click.option(
    "--channel", default="all",
    type=click.Choice(["ko", "en", "all"]),
    help="대상 채널 (--keys 지정 시 무시)",
)
@click.option(
    "--keys", default="",
    help="콤마 구분 internal_id 명시 (예: 26-E001-S01,26-E001-S02). "
         "지정 시 채널 자동 추출 우회.",
)
def main(dry_run: bool, channel: str, keys: str) -> None:
    if keys.strip():
        all_iids = [k.strip() for k in keys.split(",") if k.strip()]
        click.echo(f"explicit keys: {len(all_iids)}")
    else:
        channels = ["ko", "en"] if channel == "all" else [channel]
        all_iids = []
        for ch in channels:
            iids = _published_internal_ids(ch)
            click.echo(f"channel={ch}: {len(iids)} published")
            all_iids.extend(iids)

    if not all_iids:
        click.echo("nothing to delete.")
        return

    deleted = 0
    skipped = 0
    failed = 0
    for iid in sorted(set(all_iids)):
        key = f"{iid}.mp4"
        if dry_run:
            click.echo(f"  [dry-run] would delete: {key}")
            continue
        try:
            ok = r2.delete_object(key)
            if ok:
                deleted += 1
                click.echo(f"  deleted: {key}")
            else:
                skipped += 1
                click.echo(f"  skip (not in R2): {key}")
        except Exception as e:
            failed += 1
            click.echo(f"  FAILED: {key}  {e}", err=True)

    if dry_run:
        click.echo(f"=== dry-run total = {len(set(all_iids))} ===")
    else:
        click.echo(
            f"=== deleted={deleted} skipped={skipped} failed={failed} ===",
        )


if __name__ == "__main__":
    main()
