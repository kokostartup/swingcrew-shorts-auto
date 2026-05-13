"""Phase 5+6 CLI: 노션 sync + polling + ffmpeg 생성 + 멀티 플랫폼 게시.

Examples:
    # 1) 분석 결과 → 노션에 후보 push
    uv run python scripts/sync_notion.py --push 1wwEY0KEkoA

    # 2) 영빈 ✅ 토글 가져오고 → approved → ffmpeg → generated → 게시까지
    uv run python scripts/sync_notion.py --poll
"""
import sys

import click

from app.pipeline.analyze import load_cached_analysis
from app.pipeline.approve import (
    poll_status_from_notion,
    process_approved,
    sync_to_notion,
)
from app.pipeline.publish import publish_ready
from app.storage.db import get_connection, get_video_by_youtube_id

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option("--push", "youtube_id", default=None, help="분석 결과 → 노션 push")
@click.option("--poll", "do_poll", is_flag=True, help="노션 polling + ffmpeg 처리")
def main(youtube_id: str | None, do_poll: bool) -> None:
    """노션 승인 워크플로우 CLI."""
    if not youtube_id and not do_poll:
        raise click.UsageError("--push <youtube_id> 또는 --poll 둘 중 하나 필요")
    if youtube_id and do_poll:
        raise click.UsageError("--push와 --poll 동시 불가")

    if youtube_id:
        conn = get_connection()
        try:
            video = get_video_by_youtube_id(conn, youtube_id)
        finally:
            conn.close()
        if video is None:
            raise click.ClickException(
                f"{youtube_id} videos 행 없음 — analyze.py 먼저 실행하세요.",
            )
        result = load_cached_analysis(youtube_id)
        if result is None:
            raise click.ClickException(
                f"{youtube_id} 분석 캐시 없음 — analyze.py 먼저 실행하세요.",
            )
        created = sync_to_notion(video, result)
        click.echo(f"OK: 노션에 새로 push 한 후보 {created}개")
        return

    click.echo("Step 1/3: 노션 → SQLite 상태 sync (polling)...")
    counts = poll_status_from_notion()
    click.echo(
        f"  approved: {counts['approved']}, "
        f"rejected: {counts['rejected']}, "
        f"scheduled_synced: {counts['scheduled_synced']}"
    )
    click.echo("Step 2/3: approved → ffmpeg 시그니처 합성...")
    n_gen = process_approved()
    click.echo(f"  generated: {n_gen}개")
    click.echo("Step 3/3: generated + Scheduled At → 멀티 플랫폼 게시...")
    n_pub = publish_ready()
    click.echo(f"OK: scheduled {n_pub}개")


if __name__ == "__main__":
    main()
