"""FB + IG + Threads 직접 API 통합 1차 테스트 (1회용).

P003-S05 (가장 늦은 슬롯, 5/19 07:00) 1개 모먼트로 3 platform 게시.
결과 확인 후 publish.py 통합 + backlog 12개 진행.
"""
from __future__ import annotations

import json
import sys

from app.config import settings
from app.integrations.social import (
    SocialPostError,
    facebook_video_url,
    instagram_reel_url,
    post_facebook_video,
    post_instagram_reel,
    post_threads_video,
    threads_post_url,
)
from app.pipeline.publish import _build_buffer_text, _find_moment, _resolve_meta
from app.storage.db import get_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

TARGET = "26-P003-S05"


def main() -> None:
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT s.*, v.youtube_id FROM shorts s "
            "JOIN videos v ON s.source_video_id = v.id "
            "WHERE s.internal_id = ?",
            (TARGET,),
        ).fetchone()
    finally:
        conn.close()
    if r is None:
        print(f"{TARGET}: not found")
        return

    print(f"=== {TARGET} test post ===")
    pu = json.loads(r["published_urls"] or "{}")
    yt_url = pu.get("youtube", "")
    print(f"YouTube URL: {yt_url}")

    # R2 public URL 구성
    r2_url = f"{settings.r2_public_url.rstrip('/')}/{r['internal_id']}.mp4"
    print(f"R2 URL: {r2_url}")

    # 메타 결정 (노션 최신값 또는 cache)
    moment = _find_moment(r["youtube_id"], r["start_time"], r["end_time"])
    if moment is None:
        print("ERROR: cache moment 없음")
        return
    meta = _resolve_meta(r, r["notion_page_id"], moment)
    if meta is None:
        print("ERROR: meta unresolved")
        return
    text = _build_buffer_text(meta)
    print(f"Title: {meta.title}")
    print(f"Text (게시 본문): {text[:200]}")

    # Facebook Page
    print("\n--- Facebook Page ---")
    try:
        fb_id = post_facebook_video(r2_url, text)
        print(f"  OK video_id={fb_id} url={facebook_video_url(fb_id)}")
    except SocialPostError as e:
        print(f"  FAIL: {e}")

    # Instagram Reels
    print("\n--- Instagram Reels (2-step, ~30초 polling) ---")
    try:
        ig_id = post_instagram_reel(r2_url, text)
        print(f"  OK media_id={ig_id} url={instagram_reel_url(ig_id)}")
    except SocialPostError as e:
        print(f"  FAIL: {e}")

    # Threads
    print("\n--- Threads (2-step, ~30초 polling) ---")
    try:
        th_id = post_threads_video(r2_url, text[:500])
        print(f"  OK media_id={th_id} url={threads_post_url(th_id)}")
    except SocialPostError as e:
        print(f"  FAIL: {e}")


if __name__ == "__main__":
    main()
