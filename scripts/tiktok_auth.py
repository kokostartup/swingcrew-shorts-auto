"""TikTok OAuth 1회 인증 — 영빈 PC에서 실행.

흐름:
  1. authorize URL 출력 + 브라우저 자동 open
  2. 영빈 TikTok 로그인 + 권한 부여 → callback page (GitHub Pages)로 redirect
  3. 페이지가 'code' 표시 + Copy 버튼
  4. 영빈이 code를 이 script prompt에 paste
  5. code → access_token + refresh_token + open_id 교환 + 파일 저장

이후 모든 호출은 cached token + 자동 refresh.
"""

from __future__ import annotations

import sys
import webbrowser

from app.integrations.tiktok import build_authorize_url, exchange_code_for_token

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    url = build_authorize_url()
    print("=== TikTok OAuth 1회 인증 ===")
    print()
    print("아래 URL을 브라우저에서 열어 TikTok 로그인 + 권한 부여:")
    print(url)
    print()
    try:
        webbrowser.open(url)
        print("(브라우저 자동으로 열림)")
    except Exception:
        pass
    print()
    print("권한 부여 후 callback 페이지에서 표시되는 'code' 값을 복사해서 붙여넣으세요.")
    print()
    code = input("code: ").strip()
    if not code:
        print("code 비어있음. 종료.")
        return
    print()
    print("token 교환 중...")
    token = exchange_code_for_token(code)
    print()
    print("=== 성공 ===")
    print(f"open_id: {token['open_id']}")
    print(f"expires_at: {token['expires_at']}")
    print(f"scope: {token['scope']}")
    print()
    print("token 저장 위치: data/tiktok_token.json")
    print("이후 호출은 자동으로 이 token 사용 + 만료 시 refresh.")


if __name__ == "__main__":
    main()
