"""Cloudflare R2 (S3 호환) 어댑터 — mp4를 public URL로 호스팅 (Buffer 게시용).

Buffer는 외부 URL만 받으므로 영빈 PC 로컬 mp4를 R2에 업로드 후 public URL 반환.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)


class R2Error(RuntimeError):
    """R2 업로드 실패."""


_client: Any = None


def _get_client() -> Any:
    """boto3 S3 client lazy init."""
    global _client
    if _client is not None:
        return _client
    missing = [
        k for k, v in {
            "R2_ACCOUNT_ID": settings.r2_account_id,
            "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
            "R2_BUCKET": settings.r2_bucket,
            "R2_PUBLIC_URL": settings.r2_public_url,
            "R2_S3_ENDPOINT": settings.r2_s3_endpoint,
        }.items()
        if not v
    ]
    if missing:
        raise R2Error(f"R2 설정 누락: {', '.join(missing)} (.env 확인)")
    import boto3

    _client = boto3.client(
        "s3",
        endpoint_url=settings.r2_s3_endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    return _client


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(R2Error),
    reraise=True,
)
def upload_video(local_path: Path, key: str | None = None) -> str:
    """로컬 mp4를 R2에 업로드 → public URL 반환.

    key: bucket 안의 object key. None이면 파일명 그대로.
    """
    if not local_path.exists():
        raise R2Error(f"local file missing: {local_path}")
    object_key = key or local_path.name
    client = _get_client()
    try:
        client.upload_file(
            Filename=str(local_path),
            Bucket=settings.r2_bucket,
            Key=object_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )
    except Exception as e:
        raise R2Error(f"upload failed: {e}") from e
    public_url = f"{settings.r2_public_url.rstrip('/')}/{object_key}"
    log.info(
        "r2.upload_done",
        bucket=settings.r2_bucket, key=object_key,
        size=local_path.stat().st_size, public_url=public_url,
    )
    return public_url


def object_exists(key: str) -> bool:
    """R2 bucket에 해당 key가 이미 있는지 확인 (재업로드 방지)."""
    client = _get_client()
    try:
        client.head_object(Bucket=settings.r2_bucket, Key=key)
        return True
    except Exception:
        return False


def delete_object(key: str) -> bool:
    """R2 mp4 삭제. 존재 안 하면 False, 삭제 성공이면 True.

    FB/IG/Threads 게시 완료 → R2 fetch source 불필요 → 삭제로 스토리지 정리.
    """
    client = _get_client()
    try:
        client.head_object(Bucket=settings.r2_bucket, Key=key)
    except Exception:
        log.info("r2.delete_skip_not_found", key=key)
        return False
    try:
        client.delete_object(Bucket=settings.r2_bucket, Key=key)
    except Exception as e:
        raise R2Error(f"delete failed: {e}") from e
    log.info("r2.delete_done", bucket=settings.r2_bucket, key=key)
    return True


__all__ = ["R2Error", "delete_object", "object_exists", "upload_video"]
