"""TikTok Inbox upload 테스트 — sandbox + audit demo video 녹화용.

흐름:
  1. R2-hosted video URL 1개 받기 (CLI arg 또는 SQLite scheduled 첫 row)
  2. /inbox/video/init/ 호출 (video.upload scope)
  3. status polling → SEND_TO_USER_INBOX 확인
  4. 영빈이 TikTok 앱 inbox에서 영상 확인 가능

사용:
    .venv/Scripts/python.exe scripts/tiktok_upload_test.py --url https://pub-90a92f3601da415f94ea9200e042858f.r2.dev/26-P016-S05.mp4
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from app.integrations.tiktok import (
    TikTokAPIError,
    direct_post_file,
    upload_to_inbox,
    upload_to_inbox_file,
    wait_for_completion,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@click.command()
@click.option("--url", default="", help="R2-hosted MP4 URL (PULL_FROM_URL, verified domain만)")
@click.option(
    "--file", "file_path", default="", help="로컬 MP4 path (FILE_UPLOAD, R2 domain unverified일 때)"
)
@click.option(
    "--direct", is_flag=True, help="Direct Post (caption + 즉시 게시, video.publish scope)"
)
@click.option(
    "--caption", default="", help="Direct Post 시 caption (TikTok title). 빈값이면 prompt"
)
@click.option(
    "--privacy",
    default="SELF_ONLY",
    type=click.Choice(["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "PUBLIC_TO_EVERYONE"]),
    help="Direct Post privacy. Sandbox는 SELF_ONLY만 가능 (audit 후 PUBLIC).",
)
def main(url: str, file_path: str, direct: bool, caption: str, privacy: str) -> None:
    print("=== TikTok Upload Test ===")
    try:
        if direct:
            if not file_path:
                print("--direct는 --file 필요", file=sys.stderr)
                sys.exit(1)
            if not caption:
                print("Caption을 입력하세요 (Enter로 마침):")
                caption = input("> ").strip()
            if not caption:
                print("caption 비어있음. 종료.", file=sys.stderr)
                sys.exit(1)
            print(f"DIRECT POST: {file_path}")
            print(f"caption: {caption[:80]}{'...' if len(caption) > 80 else ''}")
            print(f"privacy: {privacy}")
            print()
            publish_id = direct_post_file(Path(file_path), caption, privacy_level=privacy)
        elif file_path:
            print(f"FILE_UPLOAD (inbox): {file_path}")
            print()
            publish_id = upload_to_inbox_file(Path(file_path))
        elif url:
            print(f"PULL_FROM_URL (inbox): {url}")
            print()
            publish_id = upload_to_inbox(url)
        else:
            print("--url 또는 --file 중 하나 필요 (또는 --direct + --file)", file=sys.stderr)
            sys.exit(1)
        print(f"publish_id: {publish_id}")
        print()
        print("status polling...")
        result = wait_for_completion(publish_id)
        print()
        print("=== 완료 ===")
        print(f"status: {result.get('status')}")
        print(f"fail_reason: {result.get('fail_reason', '(none)')}")
        print()
        print("영빈 TikTok 앱 → 알림 또는 inbox에서 영상 확인 가능.")
    except TikTokAPIError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
