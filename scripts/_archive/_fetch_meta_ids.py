"""Meta/Instagram/Threads 각 token으로 user/page ID fetch (1회용 helper).

사용:
1. .env에 META_ACCESS_TOKEN / INSTAGRAM_ACCESS_TOKEN / THREADS_ACCESS_TOKEN 채움
2. uv run scripts/_fetch_meta_ids.py
3. 출력된 ID 값을 다시 .env에 FB_PAGE_ID / IG_USER_ID / THREADS_USER_ID 로 저장
"""
from __future__ import annotations

import sys

import httpx

from app.config import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def fetch_fb_pages() -> None:
    """Meta token으로 영빈이 관리하는 Facebook Page 목록 → FB_PAGE_ID 선택용."""
    if not settings.meta_access_token:
        print("META_ACCESS_TOKEN 비어있음 — skip")
        return
    print("\n=== Facebook Pages (META_ACCESS_TOKEN) ===")
    r = httpx.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={
            "fields": "id,name,access_token,instagram_business_account",
            "access_token": settings.meta_access_token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  ERROR {r.status_code}: {r.text[:300]}")
        return
    data = r.json().get("data", [])
    if not data:
        print("  영빈이 관리하는 Page 없음 — Meta App에 Page 연결 확인 필요")
        return
    for p in data:
        iba = p.get("instagram_business_account", {}) or {}
        iba_id = iba.get("id", "")
        print(f"  FB_PAGE_ID={p['id']}  name={p.get('name','')!r}")
        if iba_id:
            print(f"    (이 페이지에 연결된 IG Business ID = {iba_id})")


def fetch_instagram() -> None:
    """Instagram login mode token으로 IG_USER_ID."""
    if not settings.instagram_access_token:
        print("\nINSTAGRAM_ACCESS_TOKEN 비어있음 — skip")
        return
    print("\n=== Instagram (INSTAGRAM_ACCESS_TOKEN) ===")
    r = httpx.get(
        "https://graph.instagram.com/v21.0/me",
        params={
            "fields": "id,user_id,username,account_type",
            "access_token": settings.instagram_access_token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  ERROR {r.status_code}: {r.text[:300]}")
        return
    d = r.json()
    print(f"  IG_USER_ID={d.get('user_id') or d.get('id')}  "
          f"username={d.get('username','')!r}  type={d.get('account_type','')!r}")


def fetch_threads() -> None:
    """Threads token으로 THREADS_USER_ID."""
    if not settings.threads_access_token:
        print("\nTHREADS_ACCESS_TOKEN 비어있음 — skip")
        return
    print("\n=== Threads (THREADS_ACCESS_TOKEN) ===")
    r = httpx.get(
        "https://graph.threads.net/v1.0/me",
        params={
            "fields": "id,username,name,threads_profile_picture_url",
            "access_token": settings.threads_access_token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  ERROR {r.status_code}: {r.text[:300]}")
        return
    d = r.json()
    print(f"  THREADS_USER_ID={d.get('id')}  username={d.get('username','')!r}  "
          f"name={d.get('name','')!r}")


def main() -> None:
    print("Meta/Instagram/Threads ID fetcher\n")
    fetch_fb_pages()
    fetch_instagram()
    fetch_threads()
    print("\n위 ID들을 .env의 FB_PAGE_ID / IG_USER_ID / THREADS_USER_ID 에 채워.")


if __name__ == "__main__":
    main()
