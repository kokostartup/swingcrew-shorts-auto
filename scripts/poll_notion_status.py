"""노션 ✅/❌/Scheduled At/override → SQLite sync 전용. Gemini/ffmpeg/게시 안 함.

⚠️ Claude Code 세션 안전:
    - poll_status_from_notion만 호출. process_approved / publish_ready import 안 함.
    - approved 모먼트 처리(ffmpeg + 메타)는 Claude Code 메인이
      `process_approved(skip_publish_meta=True)` + `golf-publish-meta-writer` 에이전트로
      이어가야 함 (CLAUDE.md 가이드).

Examples:
    uv run python scripts/poll_notion_status.py --channel ko
    uv run python scripts/poll_notion_status.py --channel en
"""

from __future__ import annotations

import sys

import click

from app.pipeline.approve import poll_status_from_notion

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option(
    "--channel",
    type=click.Choice(["ko", "en"]),
    default="ko",
    show_default=True,
    help="대상 채널",
)
def main(channel: str) -> None:
    """노션 → SQLite 단방향 sync. 다른 단계 자동 호출 없음."""
    counts = poll_status_from_notion(channel=channel)
    click.echo(
        f"OK channel={channel} "
        f"approved={counts['approved']} "
        f"rejected={counts['rejected']} "
        f"scheduled_synced={counts['scheduled_synced']} "
        f"scene_overridden={counts['scene_overridden']} "
        f"time_overridden={counts['time_overridden']} "
        f"copy_overridden={counts['copy_overridden']}"
    )


if __name__ == "__main__":
    main()
