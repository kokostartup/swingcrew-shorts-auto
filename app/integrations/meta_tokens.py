"""Meta(Instagram + Threads) long-lived token lifecycle 관리.

Instagram과 Threads는 user token이라 max 60일. Meta Page Access Token처럼 영구는 안 됨.
대신 refresh endpoint로 60일 갱신 가능.

흐름:
1. 단기 token (1~2시간) → long-lived (60일) — `exchange_*` 함수 (1회용 첫 변환)
2. 만료 ≤ 7일 남았을 때 → refresh로 새 60일 token — `refresh_*` 함수 (cron이 호출)
3. .env에 `*_ACCESS_TOKEN` + `*_TOKEN_EXPIRES_AT` (ISO) 갱신

cron 통합 — `refresh_if_needed()` 호출하면 만료 임박한 token만 자동 갱신.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

# httpx default INFO logger가 URL을 그대로 로그에 출력 (token + secret 노출).
# 명시적으로 WARNING으로 올려 secret leak 방지.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ENV_PATH = Path(".env")
REFRESH_THRESHOLD_DAYS = 7
# GitHub Actions(publish_slot.yml)가 같은 토큰을 GitHub Secrets에서 읽으므로
# 갱신 시 함께 동기화해야 함 — 2026-07-12 IG 토큰 만료로 2건 게시 실패 사고 원인.
GITHUB_REPO = "kokostartup/swingcrew-shorts-auto"


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


def _sync_github_secrets(updates: dict[str, str]) -> None:
    """*_ACCESS_TOKEN 갱신값을 GitHub Secrets에도 반영 (best-effort).

    로컬 .env만 갱신하면 GitHub Actions는 옛 토큰으로 게시를 계속 시도한다
    (60일 후 반드시 만료 — 2026-07-12 사고). gh CLI 미설치/미인증 시 warning만.
    """
    import subprocess

    for key, value in updates.items():
        if not key.endswith("_ACCESS_TOKEN"):
            continue
        try:
            r = subprocess.run(
                ["gh", "secret", "set", key, "--repo", GITHUB_REPO, "--body", value],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                log.info("meta_tokens.github_secret_synced", key=key)
            else:
                log.warning(
                    "meta_tokens.github_secret_sync_failed",
                    key=key, stderr=r.stderr[:200],
                )
        except Exception as e:  # noqa: BLE001 — 동기화 실패해도 .env 갱신은 유효
            log.warning(
                "meta_tokens.github_secret_sync_failed", key=key, error=str(e),
            )


def _expires_at_iso(expires_in_sec: int) -> str:
    """API 응답 expires_in (초) → 만료 ISO timestamp."""
    return (datetime.now(UTC) + timedelta(seconds=expires_in_sec)).isoformat()


def _days_until_expiry(expires_at_iso_str: str | None) -> float | None:
    """만료까지 남은 일수. 파싱 실패/없음 → None."""
    if not expires_at_iso_str:
        return None
    try:
        exp = datetime.fromisoformat(expires_at_iso_str)
        return (exp - datetime.now(UTC)).total_seconds() / 86400
    except ValueError:
        return None


def exchange_instagram(short_token: str) -> dict[str, Any]:
    """Instagram 단기 token → long-lived (60일).

    Endpoint: GET https://graph.instagram.com/access_token
    이미 long-lived면 400 + error.code 100. 호출자가 catch해서 처리.
    """
    if not settings.instagram_app_secret:
        raise RuntimeError("INSTAGRAM_APP_SECRET .env 미설정")
    r = httpx.get(
        "https://graph.instagram.com/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_app_secret,
            "access_token": short_token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("error", {})
            raise RuntimeError(
                f"IG exchange {r.status_code} "
                f"code={err.get('code')} subcode={err.get('error_subcode')}: "
                f"{err.get('message', r.text)}"
            )
        except (ValueError, KeyError):
            raise RuntimeError(f"IG exchange {r.status_code}: {r.text[:300]}") from None
    data = r.json()
    return {
        "access_token": data["access_token"],
        "expires_in": int(data.get("expires_in", 5184000)),
    }


def refresh_instagram(long_lived_token: str) -> dict[str, Any]:
    """Instagram long-lived token → 새 60일 token (만료 임박 시).

    Endpoint: GET https://graph.instagram.com/refresh_access_token
    """
    r = httpx.get(
        "https://graph.instagram.com/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": long_lived_token,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "access_token": data["access_token"],
        "expires_in": int(data.get("expires_in", 5184000)),
    }


def exchange_threads(short_token: str) -> dict[str, Any]:
    """Threads 단기 token → long-lived (60일).

    Endpoint: GET https://graph.threads.net/access_token
    """
    if not settings.threads_app_secret:
        raise RuntimeError("THREADS_APP_SECRET .env 미설정")
    r = httpx.get(
        "https://graph.threads.net/access_token",
        params={
            "grant_type": "th_exchange_token",
            "client_secret": settings.threads_app_secret,
            "access_token": short_token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("error", {})
            raise RuntimeError(
                f"Threads exchange {r.status_code} "
                f"code={err.get('code')} subcode={err.get('error_subcode')}: "
                f"{err.get('message', r.text)}"
            )
        except (ValueError, KeyError):
            raise RuntimeError(f"Threads exchange {r.status_code}: {r.text[:300]}") from None
    data = r.json()
    return {
        "access_token": data["access_token"],
        "expires_in": int(data.get("expires_in", 5184000)),
    }


def refresh_threads(long_lived_token: str) -> dict[str, Any]:
    """Threads long-lived token → 새 60일 token (만료 ≤ 7일 시 가능).

    Endpoint: GET https://graph.threads.net/refresh_access_token
    """
    r = httpx.get(
        "https://graph.threads.net/refresh_access_token",
        params={
            "grant_type": "th_refresh_token",
            "access_token": long_lived_token,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "access_token": data["access_token"],
        "expires_in": int(data.get("expires_in", 5184000)),
    }


def _token_expires_at(token: str, app_id: str, app_secret: str) -> int | None:
    """Meta debug_token으로 만료 unix timestamp 조회. 0이면 영구, None이면 실패."""
    try:
        r = httpx.get(
            "https://graph.facebook.com/v21.0/debug_token",
            params={
                "input_token": token,
                "access_token": f"{app_id}|{app_secret}",
            },
            timeout=15,
        )
        r.raise_for_status()
        return int(r.json().get("data", {}).get("expires_at", 0))
    except Exception:
        return None


# Meta가 "이미 long-lived token" 시 반환하는 subcode들.
_ALREADY_LONG_LIVED_SUBCODES = {2207055, 4279019}


def _try_exchange_or_assume_long_lived(
    platform: str,
    exchange_fn: Any,
    token: str,
    env_token_key: str,
    env_expires_key: str,
) -> str:
    """exchange 시도. 이미 long-lived면 60일 가정해서 expires_at만 저장."""
    try:
        result = exchange_fn(token)
        _update_env({
            env_token_key: result["access_token"],
            env_expires_key: _expires_at_iso(result["expires_in"]),
        })
        _sync_github_secrets({env_token_key: result["access_token"]})
        log.info(f"meta_tokens.{platform}_exchanged", expires_in=result["expires_in"])
        return f"60일 token (expires_in={result['expires_in']}s)"
    except RuntimeError as e:
        msg = str(e)
        if any(f"subcode={s}" in msg for s in _ALREADY_LONG_LIVED_SUBCODES):
            _update_env({env_expires_key: _expires_at_iso(5184000)})  # 60일 가정
            _sync_github_secrets({env_token_key: token})
            log.info(f"meta_tokens.{platform}_already_long_lived")
            return "이미 long-lived (60일 가정, 만료 7일 전 cron이 자동 refresh)"
        log.warning(f"meta_tokens.{platform}_exchange_failed", error=msg)
        return f"FAIL: {msg}"


def initialize_long_lived() -> dict[str, str]:
    """1회용 — .env의 short token을 long-lived (60일)로 첫 변환 + .env update.

    Meta Developer Console에서 발급된 token은 이미 long-lived인 경우가 많음 (subcode
    2207055/4279019). 그 경우 exchange skip + expires_at만 저장 (60일 가정).
    Returns: 변환된 platform 목록.
    """
    results: dict[str, str] = {}

    if settings.instagram_access_token:
        results["instagram"] = _try_exchange_or_assume_long_lived(
            "ig", exchange_instagram, settings.instagram_access_token,
            "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_TOKEN_EXPIRES_AT",
        )

    if settings.threads_access_token:
        results["threads"] = _try_exchange_or_assume_long_lived(
            "threads", exchange_threads, settings.threads_access_token,
            "THREADS_ACCESS_TOKEN", "THREADS_TOKEN_EXPIRES_AT",
        )

    return results


def refresh_if_needed(threshold_days: float = REFRESH_THRESHOLD_DAYS) -> dict[str, str]:
    """Cron이 호출 — 만료 ≤ N일 남은 token만 자동 refresh + .env update.

    Settings reload는 안 함 (cron 한 번 호출 후 다음 cron에서 새 값 사용).
    Returns: 각 platform refresh 결과.
    """
    results: dict[str, str] = {}

    # Instagram
    ig_exp = _days_until_expiry(
        getattr(settings, "instagram_token_expires_at", None),
    )
    if settings.instagram_access_token and ig_exp is not None:
        if ig_exp <= threshold_days:
            try:
                ig = refresh_instagram(settings.instagram_access_token)
                _update_env({
                    "INSTAGRAM_ACCESS_TOKEN": ig["access_token"],
                    "INSTAGRAM_TOKEN_EXPIRES_AT": _expires_at_iso(ig["expires_in"]),
                })
                _sync_github_secrets({"INSTAGRAM_ACCESS_TOKEN": ig["access_token"]})
                results["instagram"] = f"refreshed (was {ig_exp:.1f}d → new 60d)"
                log.info("meta_tokens.ig_refreshed", was_days_left=ig_exp)
            except Exception as e:
                results["instagram"] = f"FAIL: {e}"
                log.warning("meta_tokens.ig_refresh_failed", error=str(e))
        else:
            results["instagram"] = f"skip ({ig_exp:.1f}d 남음)"

    # Threads
    th_exp = _days_until_expiry(
        getattr(settings, "threads_token_expires_at", None),
    )
    if settings.threads_access_token and th_exp is not None:
        if th_exp <= threshold_days:
            try:
                th = refresh_threads(settings.threads_access_token)
                _update_env({
                    "THREADS_ACCESS_TOKEN": th["access_token"],
                    "THREADS_TOKEN_EXPIRES_AT": _expires_at_iso(th["expires_in"]),
                })
                _sync_github_secrets({"THREADS_ACCESS_TOKEN": th["access_token"]})
                results["threads"] = f"refreshed (was {th_exp:.1f}d → new 60d)"
                log.info("meta_tokens.threads_refreshed", was_days_left=th_exp)
            except Exception as e:
                results["threads"] = f"FAIL: {e}"
                log.warning("meta_tokens.threads_refresh_failed", error=str(e))
        else:
            results["threads"] = f"skip ({th_exp:.1f}d 남음)"

    return results


__all__ = [
    "exchange_instagram",
    "exchange_threads",
    "initialize_long_lived",
    "refresh_if_needed",
    "refresh_instagram",
    "refresh_threads",
]
