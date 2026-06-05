"""매일 22:00 KST 영빈 PC cron — 오늘 누락된 social 게시 catch-up.

흐름:
  1. 노션 'scheduled' (ko + en) 페이지 fetch
  2. scheduled_at이 오늘 00:00 ~ now - 30분 사이인 모먼트만 filter
  3. publish_socials_from_notion의 _publish_one_moment로 catch-up
  4. 결과 보고

Cloudflare Worker cron 또는 GitHub Actions 누락 발생 시 안전장치.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from app.integrations.notion import _get_client, list_pages_by_status

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KST = timezone(timedelta(hours=9))


def _internal_id_from_page(page_id: str) -> str | None:
    client = _get_client()
    resp = client.pages.retrieve(page_id=page_id)
    props = resp.get("properties", {})
    iid_rt = props.get("Internal ID", {}).get("rich_text") or []
    return "".join(t.get("plain_text") or "" for t in iid_rt).strip() or None


def main() -> int:
    now = datetime.now(KST)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff_end = now - timedelta(minutes=30)

    print("=== catchup_today_socials start ===")
    print(f"now KST: {now.isoformat()}")
    print(f"today window: {today_start.isoformat()} ~ {cutoff_end.isoformat()}")
    print()

    target_iids: list[str] = []
    for ch in ("ko", "en"):
        try:
            pages = list_pages_by_status("scheduled", channel=ch)
        except Exception as e:
            print(f"  ch={ch} fetch failed: {e}", flush=True)
            continue
        for p in pages:
            sched_str = p.get("scheduled_at")
            if not sched_str:
                continue
            try:
                sched = datetime.fromisoformat(sched_str)
            except ValueError:
                continue
            sched_kst = sched.astimezone(KST)
            if today_start <= sched_kst <= cutoff_end:
                iid = _internal_id_from_page(p["id"])
                if iid:
                    target_iids.append(iid)
                    print(
                        f"  catch-up candidate: {iid} (sched {sched_kst.isoformat()})",
                        flush=True,
                    )

    if not target_iids:
        print("\n오늘 누락된 social 게시 없음. 종료.")
        return 0

    print(f"\n총 {len(target_iids)}개 catch-up 시도", flush=True)
    print()
    # publish_socials_from_notion 호출 — TARGET_INTERNAL_IDS env로 catch-up
    os.environ["TARGET_INTERNAL_IDS"] = ",".join(target_iids)
    os.environ["SKIP_TIME_FILTER"] = "true"
    # 그 script 안의 main()이 alone 알아서 처리
    from scripts import publish_socials_from_notion  # type: ignore

    publish_socials_from_notion.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
