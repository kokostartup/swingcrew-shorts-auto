"""Phase 8 학습 루프 CLI — channel calibration 트리거.

사용:
    uv run scripts/calibrate.py              # 학습 + 저장
    uv run scripts/calibrate.py --dry-run    # 결과만 출력, 저장 X
    uv run scripts/calibrate.py --min-age-days 7  # 게시 N일+ 숏츠 학습 기준
"""
from __future__ import annotations

import json
import sys

import click

from app.pipeline.calibrate import (
    calibrate,
    calibrate_midform_retention,
    calibrate_published_shorts,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@click.command()
@click.option(
    "--dry-run", is_flag=True,
    help="결과만 출력하고 calibration 테이블에 저장 X",
)
@click.option(
    "--min-age-days", type=int, default=7,
    help="게시 N일+ 지난 숏츠만 B 학습 대상 (default 7)",
)
def main(dry_run: bool, min_age_days: int) -> None:
    if dry_run:
        click.echo("=== C: 미드폼 retention spike 학습 ===")
        c = calibrate_midform_retention()
        click.echo(json.dumps(c, ensure_ascii=False, indent=2, default=str))
        click.echo("\n=== B: 게시 7일+ 숏츠 학습 ===")
        b = calibrate_published_shorts(min_age_days=min_age_days)
        click.echo(json.dumps(b, ensure_ascii=False, indent=2, default=str))
        click.echo("\n(dry-run — 저장 안 함)")
        return

    row_id = calibrate()
    click.echo(f"calibration row saved: id={row_id}")


if __name__ == "__main__":
    main()
