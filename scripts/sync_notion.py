"""분석 결과(Gemini 또는 에이전트 path) → 노션 후보 push 전용 CLI.

⚠️ Claude Code 세션 안전: 이 스크립트는 Gemini publish_meta / R2 / YouTube를
자동 호출하지 않습니다. 노션 polling + ffmpeg + 게시까지는 cron path
(`scripts/run_daily.py`)가 담당하며 모듈 함수를 직접 import해서 사용합니다.

Claude Code 세션에서 노션 ✅를 SQLite로 sync만 하려면
`scripts/poll_notion_status.py` 사용 (안전).

Examples:
    uv run python scripts/sync_notion.py --push 1wwEY0KEkoA
"""
import sys

import click

from app.pipeline.analyze import load_cached_analysis
from app.pipeline.approve import sync_to_notion
from app.storage.db import get_connection, get_video_by_youtube_id

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option(
    "--push", "youtube_id", required=True,
    help="분석 캐시 → 노션 후보 push (youtube_id)",
)
def main(youtube_id: str) -> None:
    """분석 결과를 노션에 후보로 push. Gemini 안 부름, 안전."""
    conn = get_connection()
    try:
        video = get_video_by_youtube_id(conn, youtube_id)
    finally:
        conn.close()
    if video is None:
        raise click.ClickException(
            f"{youtube_id} videos 행 없음 — ingest 먼저 실행하세요.",
        )
    result = load_cached_analysis(youtube_id)
    if result is None:
        raise click.ClickException(
            f"{youtube_id} 분석 캐시 없음 — analyze 또는 agent path 먼저 실행하세요.",
        )
    created = sync_to_notion(video, result)
    click.echo(f"OK: 노션에 새로 push 한 후보 {created}개")


if __name__ == "__main__":
    main()
