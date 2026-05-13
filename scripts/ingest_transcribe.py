"""Phase 2 CLI: YouTube 영상 다운로드 + WhisperX 전사.

Example:
    uv run python scripts/ingest_transcribe.py --youtube-id 1wwEY0KEkoA --print-text
    uv run python scripts/ingest_transcribe.py --url https://youtu.be/1wwEY0KEkoA
"""
import sys

import click

from app.config import settings
from app.pipeline.ingest import ingest
from app.pipeline.transcribe import transcribe

# Windows 콘솔 한글 깨짐 방지 (cp949 → utf-8).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option(
    "--youtube-id", "-i", default=None,
    help="YouTube video ID (11자, 예: 1wwEY0KEkoA)",
)
@click.option(
    "--url", "-u", default=None,
    help="YouTube URL (https://youtu.be/... 또는 watch?v=...)",
)
@click.option(
    "--print-text", is_flag=True,
    help="전사 결과 텍스트를 stdout에 출력",
)
def main(youtube_id: str | None, url: str | None, print_text: bool) -> None:
    """YouTube 영상 다운로드 + WhisperX 전사. 둘 다 캐시되어 있으면 ~1초."""
    if not youtube_id and not url:
        raise click.UsageError("--youtube-id 또는 --url 둘 중 하나는 필수")
    if youtube_id and url:
        raise click.UsageError("--youtube-id 와 --url 둘 중 하나만 지정")
    arg = youtube_id or url
    if arg is None:  # pragma: no cover (위 검사로 unreachable)
        raise click.UsageError("입력값이 비어있습니다.")

    video = ingest(arg)
    transcript = transcribe(video)

    if print_text:
        click.echo("")
        click.echo("=" * 60)
        for seg in transcript.segments:
            click.echo(seg.text)
        click.echo("=" * 60)

    json_path = settings.transcripts_dir / f"{video.youtube_id}.json"
    total_words = sum(len(s.words) for s in transcript.segments)
    click.echo(
        f"OK: youtube_id={video.youtube_id} "
        f"title={video.title!r} "
        f"segments={len(transcript.segments)} "
        f"words={total_words} "
        f"json={json_path}"
    )


if __name__ == "__main__":
    main()
