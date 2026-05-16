"""Phase 7 일 1회 cron — 영빈 ✅ 후 자동 처리 + Phase 8 학습.

Windows 작업 스케줄러로 매일 12:20 (KST) 실행:
    schtasks /create /tn "SwingCrewDaily" /tr "<python.exe> <repo>/scripts/run_daily.py" ^
        /sc daily /st 12:20

미드폼 ingest는 cron에서 제거됨 (영빈 결정 2026-05-13): 영빈이 미드폼 youtube_id를
직접 전달하면 manual로 처리. cron은 영빈 검토 결과의 자동 후처리만 담당.

매 실행 시:
  1. 노션 → SQLite sync (영빈 ✅/❌/Time/Scene/Title 등 override)
  2. status='approved' → ffmpeg + Gemini 메타 + 노션 Title/Description push
  3. status='generated' + Scheduled At NULL → 자동 슬롯 할당 (24h+ 후 빈 슬롯)
  4. status='generated' + Scheduled At 있고 publish 가능한 모먼트 → 멀티 플랫폼 게시
  5. 7일 ✅ 안 받은 'proposed' 모먼트 → 자동 거절
  6. Phase 8 calibration — 게시 7일+ 숏츠 학습 + 미드폼 retention 학습 → 다음 분석에 자동 반영
"""
from __future__ import annotations

import re
import sys
from datetime import UTC, datetime

import click

from app.config import settings
from app.integrations.youtube import list_channel_uploads
from app.pipeline.analyze import analyze
from app.pipeline.approve import (
    auto_reject_stale,
    poll_status_from_notion,
    process_approved,
    sync_to_notion,
)
from app.pipeline.ingest import ingest
from app.pipeline.publish import publish_ready
from app.pipeline.schedule import assign_scheduled_at_for_pending
from app.pipeline.transcribe import transcribe
from app.storage.db import get_connection
from app.utils.logger import get_logger

log = get_logger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# 영빈 내부 ID 패턴 (ingest.py와 동일).
_INTERNAL_ID_PATTERN = re.compile(r"\b\d{2}-[BP]\d{3}\b")


def _has_internal_id(description: str) -> bool:
    return bool(_INTERNAL_ID_PATTERN.search(description or ""))


def _processed_youtube_ids() -> set[str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT youtube_id FROM videos").fetchall()
        return {r["youtube_id"] for r in rows}
    finally:
        conn.close()


def step_ingest_new_videos() -> int:
    """① 영빈 채널 새 영상 감지 (description ID 있는 것만) → analyze + 노션 push."""
    if not settings.youtube_channel_id:
        click.echo("  YOUTUBE_CHANNEL_ID 없음 — skip")
        return 0
    try:
        uploads = list_channel_uploads(
            settings.youtube_channel_id, max_results=50,
        )
    except Exception as e:
        click.echo(f"  uploads list 실패: {e}")
        return 0

    processed = _processed_youtube_ids()
    new_count = 0
    for v in uploads:
        yid = v["video_id"]
        if not yid or yid in processed:
            continue
        if not _has_internal_id(v["description"]):
            log.info(
                "run_daily.skip_no_internal_id",
                youtube_id=yid, title=v.get("title", "")[:50],
            )
            continue
        try:
            click.echo(f"  ingest+analyze: {yid}")
            video = ingest(yid)
            transcript = transcribe(video)
            result = analyze(video, transcript)
            sync_to_notion(video, result)
            new_count += 1
        except Exception as e:
            log.warning(
                "run_daily.ingest_failed",
                youtube_id=yid, error=str(e),
            )
    return new_count


@click.command()
@click.option("--dry-run", is_flag=True, help="실제 처리 X, 흐름만 표시")
def main(dry_run: bool) -> None:
    """매일 1회 자동 cron 실행."""
    start = datetime.now(UTC)
    click.echo(f"=== run_daily start: {start.isoformat()} ===")
    if dry_run:
        click.echo("(dry-run mode)")
        return

    click.echo("Step 1/7: 노션 → SQLite sync (영빈 토글/수정 반영) — ko + en")
    for ch in ("ko", "en"):
        try:
            counts = poll_status_from_notion(channel=ch)
            click.echo(
                f"  [{ch}] approved={counts['approved']} rejected={counts['rejected']} "
                f"scheduled_synced={counts['scheduled_synced']} "
                f"scene_overridden={counts['scene_overridden']} "
                f"time_overridden={counts['time_overridden']}",
            )
        except Exception as e:
            click.echo(f"  [{ch}] poll skip: {e}")

    click.echo("Step 2/7: ffmpeg + Gemini 메타 + 노션 Title/Description push (ko + en)")
    generated = process_approved()
    click.echo(f"  generated: {generated}")

    click.echo("Step 3/7: Scheduled At 자동 슬롯 할당 — ko KST 4슬롯 + en LA 2슬롯")
    total_assigned = 0
    for ch in ("ko", "en"):
        try:
            a = assign_scheduled_at_for_pending(channel=ch)
            click.echo(f"  [{ch}] scheduled: {a}")
            total_assigned += a
        except Exception as e:
            click.echo(f"  [{ch}] schedule skip: {e}")

    click.echo("Step 4/7: 게시 (ko: YouTube + R2; en: YouTube native scheduling — slot cron 불필요)")
    published = publish_ready()
    click.echo(f"  published: {published}")

    click.echo("Step 5/7: 자동 거절 (7일 ✅ 안 받은 후보 — ko만 해당, en은 즉시 approved)")
    rejected = auto_reject_stale(max_age_days=7)
    click.echo(f"  rejected: {rejected}")

    click.echo("Step 6/7: Phase 8 calibration (게시 7일+ 숏츠 + 미드폼 retention 학습)")
    from app.pipeline.calibrate import calibrate
    cal_row_id = calibrate()
    click.echo(f"  calibration row: {cal_row_id}")

    click.echo("Step 7/7: Meta token refresh (만료 ≤ 7일 남은 token 자동 갱신)")
    from app.integrations.meta_tokens import refresh_if_needed
    refresh_results = refresh_if_needed()
    if refresh_results:
        for platform, status in refresh_results.items():
            click.echo(f"  {platform}: {status}")
    else:
        click.echo("  대상 없음 (IG/Threads token 미설정 또는 expires_at 비어있음)")

    elapsed = (datetime.now(UTC) - start).total_seconds()
    click.echo(f"=== run_daily done in {elapsed:.0f}s ===")


if __name__ == "__main__":
    main()
