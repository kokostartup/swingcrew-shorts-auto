"""Phase 1 시그니처 레이아웃 엔진 CLI 진입점 (/test-template 슬래시 커맨드용).

Example:
    uv run python scripts/test_template.py data/samples/1wwEY0KEkoA.mp4 \\
        --start 30 --end 90 \\
        --strategy letterbox_4_5 \\
        --copy1 "프로들의 스윙 연습" \\
        --copy2 "이걸 연결해야 합니다"
"""
from pathlib import Path
from typing import cast

import click

from app.config import settings
from app.pipeline.edit import Strategy, make_short


@click.command()
@click.argument("src", type=click.Path(exists=True, path_type=Path, dir_okay=False))
@click.option("--start", type=float, required=True, help="시작 초")
@click.option("--end", type=float, required=True, help="종료 초")
@click.option(
    "--strategy",
    type=click.Choice(["letterbox_4_5", "talking_head_crop_static"]),
    required=True,
)
@click.option("--copy1", required=True, help="상단 카피 1줄 (흰색)")
@click.option("--copy2", required=True, help="상단 카피 2줄 (노란 강조)")
@click.option("--output", type=click.Path(path_type=Path), default=None,
              help="기본: outputs/test/<strategy>.mp4")
def main(
    src: Path,
    start: float,
    end: float,
    strategy: str,
    copy1: str,
    copy2: str,
    output: Path | None,
) -> None:
    """단일 미드폼 영상에 시그니처 9:16 레이아웃 적용."""
    if output is None:
        output = settings.output_dir / "test" / f"{strategy}.mp4"
    result = make_short(
        src=src, start=start, end=end,
        strategy=cast(Strategy, strategy),
        copy1=copy1, copy2=copy2,
        output=output,
    )
    click.echo(f"OK: {result}")


if __name__ == "__main__":
    main()
