"""1회용: 최근 N주간 KO 채널 게시 숏츠 주별 평균 조회수 추세 확인.

흐름:
  1. SQLite shorts where channel='ko', status='published', scheduled_at >= cutoff
  2. YouTube Analytics views per video (scheduled_at ~ now)
  3. ISO week 단위 group → avg views 출력
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.integrations.youtube import build_analytics_client

WEEKS = 6  # 최근 6주
NOW = datetime.now(UTC)
CUTOFF = NOW - timedelta(weeks=WEEKS)


def main() -> int:
    conn = sqlite3.connect("data/state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, internal_id, published_urls, scheduled_at "
        "FROM shorts WHERE channel='ko' AND status IN ('scheduled','published') "
        "AND scheduled_at >= ? AND scheduled_at <= ?",
        (CUTOFF.isoformat(), NOW.isoformat()),
    ).fetchall()
    conn.close()

    print(f"sample: {len(rows)} published shorts (last {WEEKS} weeks)")
    if not rows:
        print("no data")
        return 0

    analytics = build_analytics_client("ko")
    end_date = NOW.strftime("%Y-%m-%d")

    per_video: list[tuple[datetime, int, str]] = []
    for r in rows:
        try:
            pu = json.loads(r["published_urls"] or "{}")
        except json.JSONDecodeError:
            continue
        m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", pu.get("youtube") or "")
        if not m:
            continue
        yt_id = m.group(1)
        sched = datetime.fromisoformat(r["scheduled_at"])
        try:
            resp = (
                analytics.reports()
                .query(
                    ids=f"channel=={settings.youtube_channel_id}",
                    startDate=sched.strftime("%Y-%m-%d"),
                    endDate=end_date,
                    metrics="views",
                    filters=f"video=={yt_id}",
                )
                .execute()
            )
            data = (resp.get("rows") or [[0]])[0]
            views = int(data[0] or 0)
            per_video.append((sched, views, r["internal_id"]))
        except Exception as e:
            print(f"  fail {r['internal_id']}: {e}", flush=True)

    by_week: dict[tuple[int, int], list[int]] = defaultdict(list)
    for sched, views, iid in per_video:
        iso = sched.isocalendar()
        by_week[(iso.year, iso.week)].append(views)

    print(f"\n{'week':<10} {'n':>4} {'avg':>8} {'median':>8} {'max':>8}")
    print("-" * 42)
    for (y, w), vs in sorted(by_week.items()):
        vs_sorted = sorted(vs)
        avg = sum(vs) // len(vs)
        med = vs_sorted[len(vs_sorted) // 2]
        mx = max(vs)
        # 주 시작 (월요일) 표시
        start = datetime.fromisocalendar(y, w, 1)
        label = start.strftime("%m/%d")
        print(f"{label:<10} {len(vs):>4} {avg:>8} {med:>8} {mx:>8}")

    # 전체 vs 최근 2주 비교
    cutoff_recent = NOW - timedelta(weeks=2)
    recent = [v for s, v, _ in per_video if s >= cutoff_recent]
    older = [v for s, v, _ in per_video if s < cutoff_recent]
    if recent and older:
        print()
        print(f"older ({len(older)}): avg={sum(older) // len(older)}")
        print(f"recent 2w ({len(recent)}): avg={sum(recent) // len(recent)}")
        delta = (sum(recent) / len(recent)) - (sum(older) / len(older))
        pct = delta / (sum(older) / len(older)) * 100
        print(f"delta: {delta:+.0f} ({pct:+.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
