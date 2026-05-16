"""Single youtube_id end-to-end 파이프라인.

흐름:
  1. ingest (yt-dlp 다운로드 + channel 자동 감지)
  2. transcribe (WhisperX, channel별 언어)
  3. analyze (Gemini, channel별 프롬프트)
  4. sync_to_notion (ko='proposed' 또는 en='approved' 자동)

영어 채널: 노션에 'approved'로 push되면 다음 run_daily cron에서 ffmpeg +
YouTube 예약 업로드까지 자동 진행. 영빈 ✅ 단계 없음.
한국 채널: 'proposed'로 push, 영빈 노션 ✅ 대기.
"""
from __future__ import annotations

import sys

import click

from app.pipeline.analyze import analyze
from app.pipeline.approve import sync_to_notion
from app.pipeline.ingest import ingest
from app.pipeline.transcribe import transcribe

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option("--video-id", required=True, help="YouTube video ID 또는 URL")
def main(video_id: str) -> None:
    click.echo(f"=== run_pipeline: {video_id} ===")

    click.echo("[1/4] ingest (다운로드 + channel 감지)")
    video = ingest(video_id)
    click.echo(f"  channel={video.channel} title={video.title[:60]} duration={video.duration}s")

    click.echo("[2/4] transcribe (WhisperX)")
    transcript = transcribe(video)
    click.echo(f"  segments={len(transcript.segments)} language={transcript.language}")

    click.echo("[3/4] analyze (Gemini)")
    result = analyze(video, transcript)
    click.echo(f"  moments={len(result.moments)}")
    for i, m in enumerate(result.moments, 1):
        click.echo(
            f"    {i}. [{m.start_sec:.1f}-{m.end_sec:.1f}s] "
            f"score={(m.final_score or m.score):.2f} | {m.hook_text}"
        )

    click.echo("[4/4] sync_to_notion")
    created = sync_to_notion(video, result)
    initial = "approved (자동)" if video.channel == "en" else "proposed (영빈 ✅ 대기)"
    click.echo(f"  notion push: {created}개 (status={initial})")

    if video.channel == "en":
        click.echo("\n=> EN 채널 자동 흐름. 다음 단계는 cron(run_daily.py)에서 자동:")
        click.echo("   - approved → ffmpeg + 영어 메타 → generated")
        click.echo("   - generated → LA PT 슬롯 할당 → YouTube 예약 업로드")
    else:
        click.echo("\n=> KO 채널. 영빈이 노션에서 ✅ 토글 후 다음 cron에서 ffmpeg 진행.")


if __name__ == "__main__":
    main()
