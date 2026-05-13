"""Phase 3 CLI: ingest → transcribe → Gemini analyze.

Example:
    uv run python scripts/analyze.py --youtube-id 1wwEY0KEkoA
"""
import sys

import click

from app.config import settings
from app.pipeline.analyze import analyze
from app.pipeline.approve import sync_to_notion
from app.pipeline.ingest import ingest
from app.pipeline.transcribe import transcribe

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option("--youtube-id", "-i", default=None, help="YouTube video ID (11자)")
@click.option("--url", "-u", default=None, help="YouTube URL")
def main(youtube_id: str | None, url: str | None) -> None:
    """매직 모먼트 후보 추출 (ingest + transcribe + analyze)."""
    if not youtube_id and not url:
        raise click.UsageError("--youtube-id 또는 --url 둘 중 하나는 필수")
    if youtube_id and url:
        raise click.UsageError("--youtube-id 와 --url 둘 중 하나만 지정")
    arg = youtube_id or url
    if arg is None:  # pragma: no cover
        raise click.UsageError("입력값이 비어있습니다.")

    video = ingest(arg)
    transcript = transcribe(video)
    result = analyze(video, transcript)

    click.echo("")
    click.echo("=" * 70)
    click.echo(f"매직 모먼트 후보 {len(result.moments)}개 (model={result.model})")
    click.echo("=" * 70)
    has_retention = any(m.retention_uplift is not None for m in result.moments)
    click.echo(
        "잔존율: " + (
            "fetched (Day 7+, 영빈 채널)"
            if has_retention else "cold start (Day < 7 또는 미인증)"
        )
    )
    click.echo("=" * 70)
    for i, m in enumerate(result.moments, 1):
        final = f"final={m.final_score:.2f}" if m.final_score is not None else ""
        retn = (
            f"retention={m.retention_uplift:.2f}"
            if m.retention_uplift is not None else "retention=-"
        )
        density = f"density={m.density:.2f}" if m.density is not None else ""
        scene = f"scene={m.scene_type}" if m.scene_type else "scene=-"
        click.echo(
            f"\n[{i}] {m.start_sec:.1f}s ~ {m.end_sec:.1f}s "
            f"gemini={m.score:.1f} {retn} {density} {final}"
        )
        click.echo(f"    {scene}")
        click.echo(f"    hook: {m.hook_text}")
        click.echo(f"    copy1: {m.copy1}")
        click.echo(f"    copy2: {m.copy2}")
        click.echo(f"    why  : {m.reasoning}")

    json_path = settings.analyses_dir / f"{video.youtube_id}.json"
    click.echo("")
    click.echo(f"OK: json={json_path}")

    # 자동으로 노션에 push.
    click.echo("")
    click.echo("노션에 후보 push 중...")
    try:
        created = sync_to_notion(video, result)
        click.echo(f"OK: 노션에 새로 push 한 후보 {created}개")
    except Exception as e:
        click.echo(f"WARN: 노션 push 실패 (수동 재시도: sync_notion.py --push {video.youtube_id})")
        click.echo(f"  reason: {e}")


if __name__ == "__main__":
    main()
