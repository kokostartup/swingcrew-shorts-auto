"""Meta 단기 token → long-lived → Page Access Token 자동 변환 + .env update (1회용).

사용:
1. .env에 META_APP_ID / META_APP_SECRET / META_ACCESS_TOKEN (단기) 입력
2. uv run scripts/_exchange_meta_token.py
3. .env가 자동 update됨:
   - META_ACCESS_TOKEN ← Page Access Token (영구)
   - FB_PAGE_ID ← 첫 번째 페이지 ID

페이지가 여러 개면 --page-name 옵션으로 선택.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import httpx

from app.config import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

ENV_PATH = Path(".env")


def _update_env(updates: dict[str, str]) -> None:
    """기존 .env에서 KEY=... 행을 새 값으로 교체. KEY 없으면 append."""
    if not ENV_PATH.exists():
        raise RuntimeError(".env 파일 없음")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        m = re.match(r"^(\w+)=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@click.command()
@click.option(
    "--page-name", default=None,
    help="페이지 이름 (영빈 페이지가 여러 개일 때 매칭, 부분 일치 OK)",
)
def main(page_name: str | None) -> None:
    short = settings.meta_access_token
    app_id = settings.meta_app_id
    app_secret = settings.meta_app_secret
    if not (short and app_id and app_secret):
        raise click.ClickException(
            ".env에 META_APP_ID / META_APP_SECRET / META_ACCESS_TOKEN 다 채워야 함"
        )

    # 1. 단기 → long-lived user token (60일)
    click.echo("Step 1/3: 단기 token → long-lived user token...")
    r = httpx.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short,
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise click.ClickException(f"long-lived exchange 실패: {r.status_code} {r.text[:300]}")
    long_lived = r.json()["access_token"]
    expires_in = r.json().get("expires_in", 0)
    click.echo(f"  ✓ long-lived token 발급 (expires_in={expires_in}s = ~{expires_in//86400}일)")

    # 2. long-lived → Page Access Token (영구)
    click.echo("Step 2/3: /me/accounts → Page Access Token...")
    r = httpx.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={
            "fields": "id,name,access_token,instagram_business_account",
            "access_token": long_lived,
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise click.ClickException(f"/me/accounts 실패: {r.status_code} {r.text[:300]}")
    pages = r.json().get("data", [])
    if not pages:
        raise click.ClickException(
            "영빈이 관리하는 페이지 없음. Meta App에 Page 연결 + pages_show_list 권한 확인."
        )
    click.echo(f"  발견된 페이지: {len(pages)}개")
    for p in pages:
        click.echo(f"    - {p['name']} (id={p['id']})")

    # 페이지 선택
    if page_name:
        matching = [p for p in pages if page_name.lower() in p["name"].lower()]
        if not matching:
            raise click.ClickException(f"'{page_name}' 매칭 페이지 없음")
        chosen = matching[0]
    else:
        chosen = pages[0]
    click.echo(f"  ✓ 선택: {chosen['name']} (id={chosen['id']})")

    # 3. .env update
    click.echo("Step 3/3: .env update...")
    updates = {
        "META_ACCESS_TOKEN": chosen["access_token"],
        "FB_PAGE_ID": chosen["id"],
    }
    _update_env(updates)
    click.echo(f"  ✓ .env 업데이트 완료:")
    click.echo(f"    META_ACCESS_TOKEN ← Page Access Token (영구, 만료 X)")
    click.echo(f"    FB_PAGE_ID ← {chosen['id']} ({chosen['name']})")

    # IG Business 연결 정보 (보너스)
    iba = chosen.get("instagram_business_account", {}) or {}
    if iba.get("id"):
        click.echo(
            f"\n참고: 이 페이지에 연결된 Instagram Business ID = {iba['id']}\n"
            "  (Facebook login mode면 이 값을 IG_USER_ID로 쓰지만, 영빈은 Instagram login mode이므로\n"
            "   IG_USER_ID는 _fetch_meta_ids.py로 별도 fetch.)"
        )


if __name__ == "__main__":
    main()
