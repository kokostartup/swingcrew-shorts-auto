"""1회용: P021 6개 모먼트 Buffer TikTok customScheduled 호출.

전제:
  - R2 mp4 6개 이미 업로드됨 (publish_ready 단계 완료)
  - 노션 + SQLite scheduled_at 정상 (영빈 S01 수정 후 sync 필요)
  - publish_meta_json 6개 완료

흐름:
  1. poll_status_from_notion('ko') — 영빈 노션 수정 SQLite sync
  2. P021 6개 fetch (scheduled_at, publish_meta_json)
  3. Buffer TikTok channel_id 조회
  4. 각 모먼트:
     - R2 URL 구성: {R2_PUBLIC_URL}/{internal_id}.mp4
     - buffer text: description + hashtags
     - scheduled_at → UTC ISO
     - buffer.create_video_post(tiktok, customScheduled, dueAt=UTC)
  5. 결과 보고
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.integrations import buffer as buffer_api
from app.pipeline.approve import poll_status_from_notion
from app.pipeline.publish import _build_buffer_text
from app.pipeline.publish_meta import PublishMeta

KST = ZoneInfo("Asia/Seoul")
INTERNAL_IDS = [f"26-P021-S{i:02d}" for i in range(1, 7)]


def _scheduled_at_to_utc_iso(scheduled_at_local: str) -> str:
    dt = datetime.fromisoformat(scheduled_at_local)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    print("[1/3] 노션 → SQLite sync (영빈 수정 반영)")
    counts = poll_status_from_notion("ko")
    print(f"  {counts}\n")

    print("[2/3] Buffer TikTok channel 조회")
    try:
        channels = buffer_api.get_channels_by_service()
    except Exception as e:
        print(f"  ERROR: {e}")
        return 1
    tiktok_channel_id = channels.get("tiktok")
    if not tiktok_channel_id:
        print(f"  ERROR: Buffer에 TikTok 채널 연결 안 됨. (resolved: {list(channels)})")
        return 1
    print(f"  tiktok channel_id={tiktok_channel_id}\n")

    print("[3/3] P021 6개 → Buffer TikTok customScheduled")
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, internal_id, scheduled_at, publish_meta_json "
        "FROM shorts WHERE internal_id IN ({}) ORDER BY internal_id".format(
            ",".join("?" * len(INTERNAL_IDS))
        ),
        INTERNAL_IDS,
    ).fetchall()
    conn.close()

    now_utc = datetime.now(UTC)
    succeeded = 0
    for r in rows:
        iid = r["internal_id"]
        sched = r["scheduled_at"]
        meta_raw = r["publish_meta_json"]
        if not sched:
            print(f"  {iid}: no scheduled_at, skip")
            continue
        if not meta_raw:
            print(f"  {iid}: no publish_meta_json, skip")
            continue
        try:
            meta = PublishMeta.model_validate_json(meta_raw)
        except Exception as e:
            print(f"  {iid}: meta parse FAIL: {e}")
            continue

        due_at_utc = _scheduled_at_to_utc_iso(sched)
        due_dt = datetime.strptime(due_at_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if due_dt <= now_utc:
            print(f"  {iid}: scheduled_at {sched} 이미 지남 (skip)")
            continue

        r2_url = f"{settings.r2_public_url.rstrip('/')}/{iid}.mp4"
        text = _build_buffer_text(meta)
        try:
            post_id = buffer_api.create_video_post(
                channel_id=tiktok_channel_id,
                text=text,
                video_url=r2_url,
                service="tiktok",
                scheduled_at_utc=due_at_utc,
            )
            succeeded += 1
            print(f"  {iid}: TikTok scheduled at {due_at_utc} (post_id={post_id[:12]}..)")
        except Exception as e:
            print(f"  {iid}: Buffer FAIL: {e}")

    print(f"\nsucceeded: {succeeded}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
